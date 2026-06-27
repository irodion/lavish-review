"""Table-driven tests for the Cockpit Linter (ADR-0002).

The linter is a tripwire: a clean cockpit must pass, and each hardening violation
must fail with a stable rule id. The tables below mutate one thing at a time off a
known-good baseline so every rule is pinned in isolation.
"""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from branch_review.escape import STRICT_CSP, UNTRUSTED_CLOSE, UNTRUSTED_OPEN, fragment
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
]


@pytest.mark.parametrize(("label", "csp", "expected"), _CSP_CASES, ids=lambda c: c)
def test_csp_rules(label: str, csp: str | None, expected: str | None) -> None:
    errors = lint_cockpit(_cockpit(csp=csp))
    csp_rules = {r for r in _rules(errors) if r.startswith("csp-")}
    if expected is None:
        assert csp_rules == set(), f"{label}: unexpected {csp_rules}"
    else:
        assert expected in csp_rules, f"{label}: expected {expected} in {csp_rules}"
