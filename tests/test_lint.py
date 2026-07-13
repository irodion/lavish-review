"""Table-driven tests for the Cockpit Linter (ADR-0002).

The linter is a tripwire: a clean cockpit must pass, and each hardening violation
must fail with a stable rule id. The tables below mutate one thing at a time off a
known-good baseline so every rule is pinned in isolation.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import pytest

from branch_review.escape import (
    INTERACTIVE_CSP,
    STRICT_CSP,
    UNTRUSTED_CLOSE,
    UNTRUSTED_OPEN,
    fragment,
)
from branch_review.lint import LintError, lint_cockpit, main


def _cockpit(
    *,
    csp: str | None = STRICT_CSP,
    head_extra: str = "",
    body: str = "",
    script: str = '<script src="assets/app.js"></script>',
) -> str:
    """A minimal cockpit; each kwarg lets one test perturb a single facet."""
    meta_csp = (
        f'<meta http-equiv="Content-Security-Policy" content="{csp}">\n' if csp is not None else ""
    )
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        f"{meta_csp}"
        '<link rel="stylesheet" href="assets/cockpit.css">\n'
        f"{head_extra}"
        "<title>Review</title>\n</head>\n<body>\n<main>\n"
        f"{body}\n"
        "</main>\n"
        f"{script}\n"
        "</body>\n</html>\n"
    )


def _diff(escaped_body: str, *, mark: bool = True) -> str:
    """A diff section; ``mark`` toggles the untrusted markers to test their role."""
    inner = f"{UNTRUSTED_OPEN}{escaped_body}{UNTRUSTED_CLOSE}" if mark else escaped_body
    return f'<section><h2>Diff</h2><pre class="diff">{inner}</pre></section>'


def _rules(errors: Iterable[LintError]) -> set[str]:
    return {e.rule for e in errors}


# --- a clean cockpit passes -------------------------------------------------


def test_clean_cockpit_passes() -> None:
    html = _cockpit(
        body=_diff("&lt;script&gt;alert(1)&lt;/script&gt;\n+amp = a &amp;&amp; b")
        + fragment("feat/some-branch")
    )
    assert lint_cockpit(html) == []


def test_real_escape_boundary_output_passes() -> None:
    # Feed the actual boundary output, not a hand-rolled approximation.
    from branch_review.escape import build_fragments, diff_fragment

    frag = build_fragments(
        branch="evil<branch>",
        base="main",
        head_sha="deadbeefcafef00d",
        changed_file_count=1,
        files=[{"status": "A", "path": "x/<script>.py"}],
        commit_lines=["abc123 feat: <script>alert(1)</script>"],
    )
    diff = diff_fragment('+x = "<script>alert(1)</script>"\n')
    assert lint_cockpit(_cockpit(body=frag + diff)) == []


# --- untrusted regions ------------------------------------------------------

# (label, escaped_body, mark?, expected rule or None)
_UNTRUSTED_CASES = [
    ("escaped-script", "&lt;script&gt;x&lt;/script&gt;", True, None),
    ("raw-script-in-region", "<script>alert(1)</script>", True, "untrusted-markup"),
    ("raw-lt-in-region", "a < b", True, "untrusted-markup"),
    ("raw-gt-in-region", "a > b", True, "untrusted-markup"),
    ("entities-only", "&amp; &lt; &gt; &quot; &#x27;", True, None),
]


@pytest.mark.parametrize(("label", "esc", "mark", "expected"), _UNTRUSTED_CASES, ids=lambda c: c)
def test_untrusted_region_rule(label: str, esc: str, mark: bool, expected: str | None) -> None:
    errors = lint_cockpit(_cockpit(body=_diff(esc, mark=mark)))
    if expected is None:
        assert errors == [], f"{label}: expected clean, got {errors}"
    else:
        assert expected in _rules(errors), f"{label}: expected {expected} in {_rules(errors)}"


def test_unbalanced_markers_fail() -> None:
    # A stray open marker (e.g. an injected close that truncated a region).
    body = f'<pre class="diff">{UNTRUSTED_OPEN}safe text</pre>'
    assert "untrusted-unbalanced" in _rules(lint_cockpit(_cockpit(body=body)))


# --- inline JS / no-inline-script -------------------------------------------

# (label, script-or-body html, expected rule)
_INLINE_JS_CASES = [
    ("inline-script-body", "<script>alert(1)</script>", "inline-js"),
    ("script-src-plus-body", '<script src="assets/app.js">alert(1)</script>', "inline-js"),
    ("onclick-handler", '<button onclick="x()">go</button>', "inline-js"),
    ("onerror-handler", '<img src="assets/a.png" onerror="x()">', "inline-js"),
    ("javascript-uri", '<a href="javascript:alert(1)">x</a>', "inline-js"),
    # Browsers strip these ASCII controls during URL parsing, so the scheme still
    # resolves to javascript: — the linter must catch them too.
    ("js-uri-tab", '<a href="java\tscript:alert(1)">x</a>', "inline-js"),
    ("js-uri-newline", '<a href="java\nscript:alert(1)">x</a>', "inline-js"),
    ("js-uri-cr", '<a href="java\rscript:alert(1)">x</a>', "inline-js"),
    ("js-uri-leading-control", '<a href="\x01javascript:alert(1)">x</a>', "inline-js"),
]


@pytest.mark.parametrize(("label", "html_frag", "expected"), _INLINE_JS_CASES, ids=lambda c: c)
def test_inline_js_rules(label: str, html_frag: str, expected: str) -> None:
    # Put the offender in the body and keep the legitimate external script too,
    # except when the offender IS the script element.
    if html_frag.strip().startswith("<script"):
        html = _cockpit(script=html_frag)
    else:
        html = _cockpit(body=html_frag)
    assert expected in _rules(lint_cockpit(html)), f"{label}: {_rules(lint_cockpit(html))}"


# --- remote assets under vendored styling -----------------------------------

# (label, head_extra/body html, styling, expected rule or None)
_REMOTE_CASES = [
    (
        "remote-css-vendored",
        {"head_extra": '<link rel="stylesheet" href="https://cdn.example/x.css">'},
        "vendored",
        "remote-asset",
    ),
    (
        "remote-script-vendored",
        {"script": '<script src="https://cdn.example/app.js"></script>'},
        "vendored",
        "remote-asset",
    ),
    (
        "protocol-relative-img-vendored",
        {"body": '<img src="//cdn.example/x.png">'},
        "vendored",
        "remote-asset",
    ),
    (
        "remote-css-cdn-allowed",
        {"head_extra": '<link rel="stylesheet" href="https://cdn.example/x.css">'},
        "cdn",
        None,
    ),
    (
        "local-relative-vendored-ok",
        {"body": '<img src="assets/x.png">'},
        "vendored",
        None,
    ),
    (
        "fragment-anchor-vendored-ok",
        {"body": '<a href="#section">jump</a>'},
        "vendored",
        None,
    ),
]


@pytest.mark.parametrize(("label", "kwargs", "styling", "expected"), _REMOTE_CASES, ids=lambda c: c)
def test_remote_asset_rules(
    label: str, kwargs: dict[str, str], styling: str, expected: str | None
) -> None:
    errors = lint_cockpit(_cockpit(**kwargs), styling=styling)
    if expected is None:
        assert "remote-asset" not in _rules(errors), f"{label}: {errors}"
    else:
        assert expected in _rules(errors), f"{label}: {_rules(errors)}"


# --- CSP ---------------------------------------------------------------------

# A baseline-complete strict policy; each case below perturbs one directive off it.
_BASE = "default-src 'none'; script-src 'self'; base-uri 'none'; form-action 'none'"

# (label, csp content or None, expected rule or None)
_CSP_CASES = [
    ("strict", STRICT_CSP, None),
    ("base-complete", _BASE, None),
    # 'self' is accepted for base-uri/form-action (default-src must stay 'none').
    (
        "base-uri-form-action-self-ok",
        "default-src 'none'; script-src 'self'; base-uri 'self'; form-action 'self'",
        None,
    ),
    ("missing", None, "csp-missing"),
    ("unsafe-inline", "default-src 'none'; script-src 'self' 'unsafe-inline'", "csp-weak"),
    ("script-wildcard", "default-src 'none'; script-src *", "csp-weak"),
    ("remote-script-host", "script-src 'self' https://cdn.example", "csp-weak"),
    ("no-script-directive", "img-src 'self'", "csp-weak"),
    # The reported gap: scripts are constrained but nothing else is — other resource
    # types fall to the browser default. Must fail (not pass as it did before).
    ("script-src-only-no-default", "script-src 'self'", "csp-weak"),
    # default-src must be the catch-all denial 'none', not the weaker 'self'.
    ("default-src-self-too-weak", "default-src 'self'; script-src 'self'", "csp-weak"),
    # base-uri / form-action don't inherit from default-src, so each is required.
    ("missing-base-uri", "default-src 'none'; script-src 'self'; form-action 'none'", "csp-weak"),
    ("missing-form-action", "default-src 'none'; script-src 'self'; base-uri 'none'", "csp-weak"),
    # A wildcard, a scheme-source, or a bare host in a NON-baseline fetch directive
    # allows arbitrary remote loads and must fail strict mode (it allows no remote at
    # all) — these are exactly the tokens the old prefix-only remote check missed.
    ("img-src-wildcard", f"{_BASE}; img-src *", "csp-weak"),
    ("connect-src-scheme-source", f"{_BASE}; connect-src https:", "csp-weak"),
    ("img-src-bare-host", f"{_BASE}; img-src cdn.example", "csp-weak"),
    ("img-src-host-wildcard", f"{_BASE}; img-src https://*.example", "csp-weak"),
    # data:/blob: are not remote loads, so they must NOT be flagged (no false positive)
    # even though the linter now treats unknown tokens as remote by default.
    ("img-src-data-blob-ok", f"{_BASE}; img-src 'self' data:; worker-src blob:", None),
]


@pytest.mark.parametrize(("label", "csp", "expected"), _CSP_CASES, ids=lambda c: c)
def test_csp_rules(label: str, csp: str | None, expected: str | None) -> None:
    errors = lint_cockpit(_cockpit(csp=csp))
    csp_rules = {r for r in _rules(errors) if r.startswith("csp-")}
    if expected is None:
        assert csp_rules == set(), f"{label}: unexpected {csp_rules}"
    else:
        assert expected in csp_rules, f"{label}: expected {expected} in {csp_rules}"


# --- interactive CSP mode (cockpit served through Lavish-AXI, ADR-0004) -------


def _csp_rules(errors: Iterable[LintError]) -> set[str]:
    return {r for r in _rules(errors) if r.startswith("csp-")}


def test_interactive_csp_passes_in_interactive_mode() -> None:
    # The relaxed policy Lavish needs is accepted only when we ask for it.
    assert _csp_rules(lint_cockpit(_cockpit(csp=INTERACTIVE_CSP), csp_mode="interactive")) == set()


def test_interactive_csp_fails_under_strict_mode() -> None:
    # The portable-artifact default must still reject the relaxed policy — the two
    # modes are genuinely different, not both permissive.
    assert "csp-weak" in _csp_rules(lint_cockpit(_cockpit(csp=INTERACTIVE_CSP)))


def test_strict_csp_still_passes_in_interactive_mode() -> None:
    # Strict is a subset of the interactive baseline, so it remains acceptable.
    assert _csp_rules(lint_cockpit(_cockpit(csp=STRICT_CSP), csp_mode="interactive")) == set()


# Interactive mode is relaxed but still BOUNDED — these must fail even in it.
_INTERACTIVE_REJECTS = [
    ("script-wildcard", "default-src 'none'; script-src *; base-uri 'none'; form-action 'none'"),
    (
        "arbitrary-remote-host",
        "default-src 'none'; script-src 'self' https://evil.example; "
        "base-uri 'none'; form-action 'none'",
    ),
    (
        "default-src-self-too-weak",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "base-uri 'none'; form-action 'none'",
    ),
    (
        "base-uri-unlocked",
        "default-src 'none'; script-src 'self' 'unsafe-inline'; base-uri *; form-action 'none'",
    ),
    # A remote host smuggled into a non-baseline fetch directive must still fail —
    # interactive is bounded to 'self' + the Lavish CDN across EVERY directive.
    (
        "img-src-arbitrary-remote",
        "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self'; "
        "img-src https://evil.example; base-uri 'none'; form-action 'none'",
    ),
    (
        "connect-src-arbitrary-remote",
        "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self'; "
        "connect-src https://evil.example; base-uri 'none'; form-action 'none'",
    ),
    # The reported gap: a wildcard or a scheme-source (not a full URL) bypassed the
    # remote bound. Interactive mode is widened to 'self' + the Lavish CDN, never to
    # an open '*' or an any-https scheme, so these must still fail.
    (
        "img-src-wildcard",
        "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self'; "
        "img-src *; base-uri 'none'; form-action 'none'",
    ),
    (
        "connect-src-scheme-source",
        "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self'; "
        "connect-src https:; base-uri 'none'; form-action 'none'",
    ),
    (
        "img-src-bare-host",
        "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self'; "
        "img-src cdn.evil.example; base-uri 'none'; form-action 'none'",
    ),
]


@pytest.mark.parametrize(("label", "csp"), _INTERACTIVE_REJECTS, ids=lambda c: c)
def test_interactive_mode_is_still_bounded(label: str, csp: str) -> None:
    assert "csp-weak" in _csp_rules(lint_cockpit(_cockpit(csp=csp), csp_mode="interactive")), label


# --- duplicate attributes (browser keeps the FIRST; lint must not be fooled) --


def test_duplicate_attr_dangerous_first_value_is_caught() -> None:
    # The dict collapse kept the safe last value; the raw-pair audit catches the
    # dangerous first one the browser would actually use.
    body = '<a href="javascript:alert(1)" href="#ok">x</a>'
    assert "inline-js" in _rules(lint_cockpit(_cockpit(body=body)))


def test_duplicate_attr_dangerous_second_value_is_caught() -> None:
    body = '<a href="#ok" href="javascript:alert(1)">x</a>'
    assert "inline-js" in _rules(lint_cockpit(_cockpit(body=body)))


def test_duplicate_remote_href_is_caught_under_vendored() -> None:
    body = '<link rel="stylesheet" href="assets/x.css" href="https://evil.example/x.css">'
    assert "remote-asset" in _rules(lint_cockpit(_cockpit(body=body)))


def test_duplicate_safe_hrefs_still_pass() -> None:
    # Two harmless local hrefs must not trip anything — the audit flags danger, not
    # duplication per se.
    body = '<a href="#a" href="#b">x</a>'
    assert _rules(lint_cockpit(_cockpit(body=body))) == set()


# --- duplicate CSP directives (browser uses the first; lint must not be fooled) --


def test_duplicate_csp_directive_is_flagged() -> None:
    # A repeated directive: the browser honours the first (weak) script-src and
    # ignores the safe second; the linter must flag the duplicate either way.
    csp = (
        "default-src 'none'; script-src 'self' 'unsafe-inline'; script-src 'self'; "
        "base-uri 'none'; form-action 'none'"
    )
    assert "csp-weak" in _csp_rules(lint_cockpit(_cockpit(csp=csp)))


def test_lavish_cdn_is_an_allowed_remote_in_interactive() -> None:
    # The bound permits exactly the Lavish CDN (and data:/blob:), so the real
    # interactive policy must not be rejected by the remote-host scan.
    assert _csp_rules(lint_cockpit(_cockpit(csp=INTERACTIVE_CSP), csp_mode="interactive")) == set()


# --- structural rules (analysis↔cockpit coherence, issues #62/#86) -----------
#
# These run ONLY when the caller passes the analysis's step id set. The helpers
# below assemble a well-formed cockpit whose step panels, seams, and evidence
# anchors match a two-step analysis, then each table row perturbs one thing.

_QA_SEAM = "<!--brc:qa-log--><!--/brc:qa-log-->"


def _step(
    step_id: str,
    *,
    evidence_seam: bool = True,
    seam_close: bool = True,
    seam_reversed: bool = False,
    extra_body: str = "",
) -> str:
    """One L2 Review Step panel: a <details class="step"> with its live-evidence seam.

    ``evidence_seam=False`` omits the seam entirely; ``seam_close=False`` plants only
    the open marker (an unpaired seam); ``seam_reversed=True`` plants the close before
    the open (both present but unfillable — the injector's regex matches nothing).
    """
    open_m = f"<!--brc:evidence:{step_id}-->"
    close_m = f"<!--/brc:evidence:{step_id}-->"
    if not evidence_seam:
        seam = ""
    elif seam_reversed:
        seam = close_m + open_m
    elif seam_close:
        seam = open_m + close_m
    else:
        seam = open_m
    return (
        f'<details class="step" id="{step_id}"><summary>step {step_id}</summary>'
        f'<div class="step-body">{extra_body}{seam}</div></details>'
    )


def _structured_cockpit(
    *,
    steps: str | None = None,
    qa_seam: str = _QA_SEAM,
    body_extra: str = "",
) -> str:
    """A well-formed two-step cockpit (t1.s1, t1.s2) with both seams and the Q&A seam."""
    if steps is None:
        steps = _step("t1.s1") + _step("t1.s2")
    body = f'<section class="thread" id="t1"><h2>Thread</h2>{steps}</section>{body_extra}'
    return _cockpit(csp=INTERACTIVE_CSP, body=f"{body}\n{qa_seam}")


# The analysis's step id set for the well-formed fixture above.
_ANALYSIS_IDS = ["t1.s1", "t1.s2"]


def test_well_formed_cockpit_passes_the_structural_pass() -> None:
    html = _structured_cockpit()
    assert lint_cockpit(html, csp_mode="interactive", step_ids=_ANALYSIS_IDS) == []


def test_structural_pass_is_off_without_step_ids() -> None:
    # No analysis handed in → only escape/CSP rules run; a page with no steps/seams
    # (the minimal fixture) must still pass, exactly as before this rule existed.
    assert lint_cockpit(_cockpit(csp=INTERACTIVE_CSP), csp_mode="interactive") == []


# (label, cockpit kwargs, analysis step ids, expected structural rule)
_STRUCTURAL_CASES = [
    (
        "dangling-anchor",
        {"body_extra": '<a href="#t9.s9">see</a>'},
        _ANALYSIS_IDS,
        "dangling-anchor",
    ),
    (
        "resolving-anchor-ok",
        {"body_extra": '<a href="#t1.s2">see</a>'},
        _ANALYSIS_IDS,
        None,
    ),
    (
        "bare-hash-anchor-ok",
        {"body_extra": '<a href="#">top</a>'},
        _ANALYSIS_IDS,
        None,
    ),
    (
        "step-missing-from-dom",
        {"steps": _step("t1.s1")},  # analysis expects t1.s1 AND t1.s2
        _ANALYSIS_IDS,
        "step-id-missing",
    ),
    (
        "step-extra-in-dom",
        {"steps": _step("t1.s1") + _step("t1.s2") + _step("t1.s3")},
        _ANALYSIS_IDS,
        "step-id-unknown",
    ),
    (
        "step-duplicate",
        {"steps": _step("t1.s1") + _step("t1.s1") + _step("t1.s2")},
        _ANALYSIS_IDS,
        "step-id-duplicate",
    ),
    (
        "missing-qa-seam",
        {"qa_seam": ""},
        _ANALYSIS_IDS,
        "seam-missing",
    ),
    # Unpaired seams: only the open marker is planted. The injectors match open…close,
    # so lint must reject these too — otherwise the failure surfaces only later.
    (
        "unpaired-qa-seam",
        {"qa_seam": "<!--brc:qa-log-->"},
        _ANALYSIS_IDS,
        "seam-missing",
    ),
    (
        "missing-evidence-seam",
        {"steps": _step("t1.s1", evidence_seam=False) + _step("t1.s2")},
        _ANALYSIS_IDS,
        "seam-missing",
    ),
    (
        "unpaired-evidence-seam",
        {"steps": _step("t1.s1", seam_close=False) + _step("t1.s2")},
        _ANALYSIS_IDS,
        "seam-missing",
    ),
    # Reversed seams: both markers present but close precedes open, so the injector's
    # `open .*? close` regex matches nothing — a naive presence check misses it.
    (
        "reversed-qa-seam",
        {"qa_seam": "<!--/brc:qa-log--><!--brc:qa-log-->"},
        _ANALYSIS_IDS,
        "seam-missing",
    ),
    (
        "reversed-evidence-seam",
        {"steps": _step("t1.s1", seam_reversed=True) + _step("t1.s2")},
        _ANALYSIS_IDS,
        "seam-missing",
    ),
    # Misplaced seam: t1.s1's seam is fillable but planted inside t1.s2's panel, so the
    # injector would render t1.s1's evidence under t1.s2. Lint must reject the misfile.
    (
        "misplaced-evidence-seam",
        {
            "steps": _step("t1.s1", evidence_seam=False)
            + _step("t1.s2", extra_body="<!--brc:evidence:t1.s1--><!--/brc:evidence:t1.s1-->")
        },
        _ANALYSIS_IDS,
        "seam-misplaced",
    ),
    # Raw-text-hidden seam: t1.s1's markers sit inside a <style> (any raw-text element —
    # <textarea>/<title>/<script>/<xmp> too). _seam_is_fillable finds them by substring,
    # but HTMLParser emits no handle_comment for raw-text content, so the markers are
    # attributed to no panel. inject_evidence would still match them and write the answer
    # inside the <style> — invisible/wrong. Lint must reject it, not pass it vacuously.
    (
        "raw-text-hidden-evidence-seam",
        {
            "steps": _step("t1.s1", evidence_seam=False) + _step("t1.s2"),
            "body_extra": "<style><!--brc:evidence:t1.s1--><!--/brc:evidence:t1.s1--></style>",
        },
        _ANALYSIS_IDS,
        "seam-misplaced",
    ),
]


@pytest.mark.parametrize(("label", "kwargs", "ids", "expected"), _STRUCTURAL_CASES, ids=lambda c: c)
def test_structural_rules(
    label: str, kwargs: dict[str, str], ids: list[str], expected: str | None
) -> None:
    errors = lint_cockpit(_structured_cockpit(**kwargs), csp_mode="interactive", step_ids=ids)
    struct_rules = {
        r for r in _rules(errors) if r.startswith(("step-id-", "dangling-anchor", "seam-"))
    }
    if expected is None:
        assert struct_rules == set(), f"{label}: unexpected {struct_rules}"
    else:
        assert expected in struct_rules, f"{label}: expected {expected} in {struct_rules}"


def test_duplicate_class_uses_browser_first_value_semantics() -> None:
    # The browser keeps the FIRST of duplicate attributes; a collapsed dict keeps the
    # last. `class="not-step" class="step"` must read as "not-step" (browser truth),
    # so the panel is NOT a step and the analysis's step goes missing — lint must not
    # be fooled by the last-wins dict into believing the step panel exists.
    panel = (
        '<details class="not-step" class="step" id="t1.s1"><summary>s</summary>'
        '<div class="step-body"><!--brc:evidence:t1.s1--><!--/brc:evidence:t1.s1--></div>'
        "</details>"
    )
    html = _cockpit(
        csp=INTERACTIVE_CSP,
        body=f'<section class="thread" id="t1">{panel}</section>\n{_QA_SEAM}',
    )
    assert "step-id-missing" in _rules(
        lint_cockpit(html, csp_mode="interactive", step_ids=["t1.s1"])
    )


def test_structural_failures_do_not_mask_escape_or_csp_failures() -> None:
    # A cockpit that is BOTH structurally broken (missing Q&A seam) and unsafe
    # (unescaped markup in an untrusted region + no CSP) must report every family —
    # the structural pass never short-circuits the escape/CSP one.
    html = _structured_cockpit(
        qa_seam="",
        body_extra=_diff("<script>alert(1)</script>"),
    ).replace(f'<meta http-equiv="Content-Security-Policy" content="{INTERACTIVE_CSP}">\n', "")
    rules = _rules(lint_cockpit(html, csp_mode="interactive", step_ids=_ANALYSIS_IDS))
    assert "untrusted-markup" in rules  # escape
    assert "csp-missing" in rules  # CSP
    assert "seam-missing" in rules  # structural


def test_real_escape_boundary_cockpit_passes_structural_pass() -> None:
    # The e2e hostile-input path: real Escape Boundary output (a hostile branch,
    # path, and commit) wrapped in a well-formed two-step frame with matching seams
    # and a resolving evidence anchor must pass BOTH the escape and structural passes.
    from branch_review.escape import build_fragments, diff_fragment

    frag = build_fragments(
        branch="evil<branch>",
        base="main",
        head_sha="deadbeefcafef00d",
        changed_file_count=1,
        files=[{"status": "A", "path": "x/<script>.py"}],
        commit_lines=["abc123 feat: <script>alert(1)</script>"],
    )
    diff = diff_fragment('+x = "<script>alert(1)</script>"\n')
    steps = _step("t1.s1", extra_body='<a href="#t1.s2">related</a>') + _step(
        "t1.s2", extra_body=frag + diff
    )
    html = _structured_cockpit(steps=steps)
    assert lint_cockpit(html, csp_mode="interactive", step_ids=_ANALYSIS_IDS) == []


# --- hunk-anchored evidence links resolve (Deck Mode, ADR-0014, issue #63) ----
#
# The Hunk Anchorer emits per-hunk element ids inside a file fragment; the anchor
# resolution rule from #62 is what makes a {path, hunk} evidence link land — it needs
# no new lint code, but these pin that a real hunk anchor resolves and a stale one fails.


def _cockpit_with_hunk_evidence(*, anchor: str, fragment_html: str) -> str:
    """A two-step cockpit whose t1.s1 links evidence to ``anchor`` and shows the fragment."""
    steps = _step("t1.s1", extra_body=f'<a href="#{anchor}">jump to hunk</a>') + _step("t1.s2")
    return _structured_cockpit(
        steps=steps,
        body_extra=f"<section><h2>Evidence</h2>{fragment_html}</section>",
    )


def test_hunk_anchored_evidence_link_resolves() -> None:
    from branch_review.escape import file_diff_fragment, file_fragment_id

    fid = file_fragment_id("m.py")
    fragment_html, hunks = file_diff_fragment(
        "--- a/m.py\n+++ b/m.py\n@@ -1 +1 @@\n-old\n+new\n@@ -9 +9 @@\n-p\n+q\n", fid
    )
    # Link the second hunk's real anchor from the manifest — it resolves to its <section>.
    html = _cockpit_with_hunk_evidence(anchor=str(hunks[1]["anchor"]), fragment_html=fragment_html)
    assert lint_cockpit(html, csp_mode="interactive", step_ids=_ANALYSIS_IDS) == []


def test_dangling_hunk_anchor_fails() -> None:
    from branch_review.escape import file_diff_fragment, file_fragment_id

    fid = file_fragment_id("m.py")
    one_hunk = "--- a/m.py\n+++ b/m.py\n@@ -1 +1 @@\n-old\n+new\n"
    fragment_html, hunks = file_diff_fragment(one_hunk, fid)
    # Point at hunk 2, which this one-hunk file never emitted — the link lands nowhere.
    html = _cockpit_with_hunk_evidence(anchor=f"hunk-{fid}-2", fragment_html=fragment_html)
    assert len(hunks) == 1  # guard: there is no hunk 2 to resolve to
    rules = _rules(lint_cockpit(html, csp_mode="interactive", step_ids=_ANALYSIS_IDS))
    assert "dangling-anchor" in rules


# --- seam markers have one source of truth (the escape leaf) ------------------


def test_seam_markers_come_from_the_escape_leaf() -> None:
    # The linter, the bake, and the evidence injector all check/plant the same seams;
    # escape.py owns the one definition so they cannot drift. This pins that wiring.
    from branch_review import escape
    from branch_review.bake import QA_SEAM_OPEN as bake_qa
    from branch_review.evidence import evidence_seam

    assert bake_qa is escape.QA_SEAM_OPEN
    assert evidence_seam("t1.s2") == escape.evidence_seam_markers("t1.s2")


# --- CLI: --analysis turns on the structural pass ----------------------------


def _analysis_json(ids: list[str]) -> dict[str, object]:
    """A minimal analysis-shaped object carrying just the step ids to extract."""
    return {"threads": [{"steps": [{"id": sid} for sid in ids]}]}


def test_cli_without_analysis_skips_structural(tmp_path: Path) -> None:
    # A cockpit with a dangling anchor but no --analysis: security-only lint passes.
    page = tmp_path / "review.html"
    page.write_text(_structured_cockpit(body_extra='<a href="#t9.s9">x</a>'), encoding="utf-8")
    assert main([str(page), "--csp-mode", "interactive"]) == 0


def test_cli_with_analysis_runs_structural(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    page = tmp_path / "review.html"
    page.write_text(_structured_cockpit(body_extra='<a href="#t9.s9">x</a>'), encoding="utf-8")
    analysis = tmp_path / "analysis.json"
    analysis.write_text(json.dumps(_analysis_json(_ANALYSIS_IDS)), encoding="utf-8")
    assert main([str(page), "--csp-mode", "interactive", "--analysis", str(analysis)]) == 1
    assert "dangling-anchor" in capsys.readouterr().err


def test_cli_with_analysis_passes_a_well_formed_cockpit(tmp_path: Path) -> None:
    page = tmp_path / "review.html"
    page.write_text(_structured_cockpit(), encoding="utf-8")
    analysis = tmp_path / "analysis.json"
    analysis.write_text(json.dumps(_analysis_json(_ANALYSIS_IDS)), encoding="utf-8")
    assert main([str(page), "--csp-mode", "interactive", "--analysis", str(analysis)]) == 0
