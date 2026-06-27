"""Table-driven tests for the Cockpit Linter (ADR-0002).

The linter is a tripwire: a clean cockpit must pass, and each hardening violation
must fail with a stable rule id. The tables below mutate one thing at a time off a
known-good baseline so every rule is pinned in isolation.
"""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from branch_review.escape import (
    INTERACTIVE_CSP,
    STRICT_CSP,
    UNTRUSTED_CLOSE,
    UNTRUSTED_OPEN,
    fragment,
)
from branch_review.lint import LintError, lint_cockpit


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
