"""Table-driven tests for the deterministic Escape Boundary (ADR-0002).

The boundary's contract is mechanical: any untrusted string becomes a fragment
that (a) carries no executable markup and (b) is wrapped in the sentinel markers
the Cockpit Linter relies on. These tables pin that input→output behaviour for the
hostile cases the issue calls out — ``<script>``, quotes, ampersands, mixed
Unicode, and an HTML-containing diff hunk.
"""

from __future__ import annotations

import pytest

from branch_review.escape import (
    UNTRUSTED_CLOSE,
    UNTRUSTED_OPEN,
    build_fragments,
    diff_fragment,
    escape_text,
    fragment,
)

# (label, raw input, substrings that MUST appear, substrings that MUST NOT appear)
_ESCAPE_CASES = [
    (
        "script-tag",
        "<script>alert(1)</script>",
        ["&lt;script&gt;", "&lt;/script&gt;"],
        ["<script>", "</script>"],
    ),
    (
        "double-quote",
        'value="x"',
        ["&quot;"],
        ['"x"'],
    ),
    (
        "single-quote",
        "it's",
        ["&#x27;"],
        ["it's"],
    ),
    (
        "ampersand",
        "a & b && c",
        ["a &amp; b &amp;&amp; c"],
        ["a & b"],
    ),
    (
        "angle-brackets",
        "1 < 2 > 0",
        ["1 &lt; 2 &gt; 0"],
        ["< 2 >"],
    ),
    (
        "mixed-unicode",
        "café 你好 \U0001f600 <b>",
        ["café 你好 \U0001f600", "&lt;b&gt;"],
        ["<b>"],
    ),
    (
        "img-onerror",
        '<img src=x onerror="alert(1)">',
        ["&lt;img", "onerror=&quot;"],
        ["<img", 'onerror="alert(1)">'],
    ),
]


@pytest.mark.parametrize(("label", "raw", "must", "must_not"), _ESCAPE_CASES, ids=lambda c: c)
def test_escape_text_neutralizes_markup(
    label: str, raw: str, must: list[str], must_not: list[str]
) -> None:
    out = escape_text(raw)
    for needle in must:
        assert needle in out, f"{label}: expected {needle!r} in {out!r}"
    for needle in must_not:
        assert needle not in out, f"{label}: {needle!r} must not survive in {out!r}"


@pytest.mark.parametrize(("label", "raw", "must", "must_not"), _ESCAPE_CASES, ids=lambda c: c)
def test_fragment_wraps_escaped_text_in_markers(
    label: str, raw: str, must: list[str], must_not: list[str]
) -> None:
    out = fragment(raw)
    assert out.startswith(UNTRUSTED_OPEN)
    assert out.endswith(UNTRUSTED_CLOSE)
    inner = out[len(UNTRUSTED_OPEN) : -len(UNTRUSTED_CLOSE)]
    # The contract the linter checks: no literal angle bracket survives in a region.
    assert "<" not in inner and ">" not in inner
    for needle in must_not:
        assert needle not in out


def test_markers_are_html_comments() -> None:
    # Invisible in the browser and absent from element.textContent — so they reach
    # neither the rendered page nor app.js. That property is load-bearing.
    assert UNTRUSTED_OPEN.startswith("<!--") and UNTRUSTED_OPEN.endswith("-->")
    assert UNTRUSTED_CLOSE.startswith("<!--") and UNTRUSTED_CLOSE.endswith("-->")


def test_diff_fragment_escapes_html_containing_hunk() -> None:
    hunk = (
        "@@ -1,2 +1,3 @@\n"
        "-old = 1\n"
        '+html = "<script>alert(document.cookie)</script>"\n'
        "+amp = a && b\n"
    )
    out = diff_fragment(hunk)

    assert out.startswith('<pre class="diff">')
    assert out.rstrip().endswith("</pre>")
    # The attacker payload is escaped; no executable markup remains in the body.
    assert "<script>" not in out
    assert "&lt;script&gt;alert(document.cookie)&lt;/script&gt;" in out
    assert "a &amp;&amp; b" in out
    # The only literal tags are the trusted <pre> shell and the comment markers.
    assert out.count("<pre") == 1 and out.count("</pre>") == 1


def test_diff_fragment_empty_has_no_markers() -> None:
    out = diff_fragment("")
    assert "(no changes in this range)" in out
    assert UNTRUSTED_OPEN not in out  # nothing untrusted → no region to mark


def test_build_fragments_escapes_every_untrusted_field() -> None:
    out = build_fragments(
        branch="evil<branch>",
        base="main",
        head_sha="deadbeefcafef00d1234",
        changed_file_count=2,
        files=[
            {"status": "A", "path": "x/<script>.py"},
            {"status": "R100", "path": "new&name.py", "old_path": "old<>.py"},
        ],
        commit_lines=["abc123 feat: <script>alert(1)</script>", "def456 fix: a & b"],
    )

    # No untrusted value renders as live markup anywhere in the fragments file.
    assert "<script>" not in out
    assert "<branch>" not in out
    assert "&lt;script&gt;" in out
    assert "evil&lt;branch&gt;" in out
    assert "x/&lt;script&gt;.py" in out
    assert "old&lt;&gt;.py" in out
    assert "new&amp;name.py" in out
    assert "a &amp; b" in out
    # Head SHA is truncated to 12 and rendered through the boundary too.
    assert "deadbeefcafe" in out
    # Balanced markers (the linter requires this).
    assert out.count(UNTRUSTED_OPEN) == out.count(UNTRUSTED_CLOSE)


def test_build_fragments_handles_empty_ranges() -> None:
    out = build_fragments(
        branch="feature",
        base="main",
        head_sha="0" * 40,
        changed_file_count=0,
        files=[],
        commit_lines=[],
    )
    assert "No files changed in this range." in out
    assert "No commits in this range." in out
    assert UNTRUSTED_OPEN in out  # branch/base still cross the boundary
