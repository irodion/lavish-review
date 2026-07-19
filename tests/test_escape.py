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
    FRAGMENTS_DIRNAME,
    UNTRUSTED_CLOSE,
    UNTRUSTED_OPEN,
    build_fragments,
    diff_fragment,
    escape_text,
    file_diff_fragment,
    file_fragment_id,
    fragment,
    fragment_index_entry,
    goal_fragment,
    hunk_anchor_id,
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
    # No goal passed → the fragment carries the degraded notice (ADR-0010).
    assert "<!-- fragment: goal -->" in out
    assert "No stated goal found; intent inferred from the diff." in out


# --- Goal Evidence fragment (ADR-0010) ---------------------------------------


def test_goal_fragment_escapes_hostile_goal_and_provenance() -> None:
    # Issue bodies and branch names are attacker-writable; both text and the
    # provenance (which may embed a branch name) must cross the boundary.
    out = goal_fragment(
        {
            "text": '<script>alert(1)</script> & "quotes"',
            "source": "issue",
            "provenance": "issue #40, referenced by branch evil<b>",
        }
    )
    assert "<script>" not in out and "<b>" not in out
    assert "&lt;script&gt;" in out and "evil&lt;b&gt;" in out
    assert 'class="goal-text"' in out and 'class="goal-provenance"' in out
    assert out.count(UNTRUSTED_OPEN) == 2  # text + provenance regions
    assert out.count(UNTRUSTED_OPEN) == out.count(UNTRUSTED_CLOSE)


def test_goal_fragment_none_is_trusted_degraded_notice() -> None:
    out = goal_fragment(None)
    assert "No stated goal found; intent inferred from the diff." in out
    assert UNTRUSTED_OPEN not in out  # a fixed trusted literal carries no markers


def test_build_fragments_carries_the_goal_block() -> None:
    out = build_fragments(
        branch="feature",
        base="main",
        head_sha="0" * 40,
        changed_file_count=0,
        files=[],
        commit_lines=[],
        goal={"text": "Add <jitter> to backoff", "source": "issue", "provenance": "issue #40"},
    )
    assert "<!-- fragment: goal -->" in out
    assert "Add &lt;jitter&gt; to backoff" in out
    assert "<jitter>" not in out
    assert out.count(UNTRUSTED_OPEN) == out.count(UNTRUSTED_CLOSE)


# --- Per-file fragment substrate (issue #21) ---------------------------------

# Hostile and unusual paths the fragment id must tame into a safe filename stem.
_PATHS = [
    "src/app.py",
    "a/../../etc/passwd",  # traversal attempt
    "weird name with spaces.py",
    "deep/nest/café_你好_\U0001f600.ts",
    "a" * 300 + ".py",  # absurdly long
    ".hidden",
    "",  # degenerate but must not crash
]


@pytest.mark.parametrize("path", _PATHS, ids=lambda p: p[:20] or "empty")
def test_file_fragment_id_is_filename_safe(path: str) -> None:
    fid = file_fragment_id(path)
    # Hex only: cannot contain a path separator, dot-dot, leading dot, or space —
    # so `fragments/<id>.html` can never escape the fragments dir.
    assert fid and all(c in "0123456789abcdef" for c in fid)
    assert "/" not in fid and ".." not in fid
    # Stable: same path → same id across calls.
    assert file_fragment_id(path) == fid


def test_file_fragment_id_distinct_paths_do_not_collide() -> None:
    ids = [file_fragment_id(p) for p in _PATHS]
    assert len(set(ids)) == len(ids)


def test_fragment_index_entry_normal_file() -> None:
    entry = fragment_index_entry({"status": "M", "path": "src/app.py"})
    fid = file_fragment_id("src/app.py")
    assert entry == {
        "path": "src/app.py",
        "path_html": fragment("src/app.py"),
        "status": "M",
        "id": fid,
        "fragment": f"{FRAGMENTS_DIRNAME}/{fid}.html",
        "omitted": False,
    }


def test_fragment_index_entry_escapes_path_html() -> None:
    # The cockpit-facing path string must arrive escaped — a hostile filename can't
    # carry live markup into a Walkthrough/Route heading.
    entry = fragment_index_entry({"status": "A", "path": "x/<script>.py"})
    path_html = entry["path_html"]
    assert isinstance(path_html, str)
    assert "<script>" not in path_html
    assert "&lt;script&gt;" in path_html
    assert path_html.startswith(UNTRUSTED_OPEN) and path_html.endswith(UNTRUSTED_CLOSE)


def test_fragment_index_entry_rename_keeps_old_path() -> None:
    entry = fragment_index_entry({"status": "R100", "path": "new.py", "old_path": "old.py"})
    assert entry["old_path"] == "old.py"
    assert entry["old_path_html"] == fragment("old.py")
    assert entry["status"] == "R100"
    assert entry["fragment"] is not None


def test_fragment_index_entry_omitted_requires_reason() -> None:
    # An omitted file is still listed; an omission with no reason would render an
    # empty, unexplained record — reject it at construction.
    with pytest.raises(ValueError):
        fragment_index_entry({"status": "M", "path": "x.py"}, omitted=True)
    with pytest.raises(ValueError):
        fragment_index_entry({"status": "M", "path": "x.py"}, omitted=True, reason="   ")


def test_fragment_index_entry_records_disposition_when_given() -> None:
    # The classifier's stable verdict string rides along so the cockpit can group
    # omissions by kind; absent the kwarg the key is omitted (back-compat).
    plain = fragment_index_entry({"status": "M", "path": "a.py"})
    assert "disposition" not in plain
    tagged = fragment_index_entry(
        {"status": "M", "path": "uv.lock"},
        omitted=True,
        reason="dependency lockfile",
        disposition="omit:lockfile",
    )
    assert tagged["disposition"] == "omit:lockfile"


def test_fragment_index_entry_omitted_drops_body_keeps_reason() -> None:
    # Excluded/capped files (issue #7) stay in the index with a reason, body gone —
    # nothing omitted is ever hidden.
    entry = fragment_index_entry(
        {"status": "M", "path": "pkg-lock.json"},
        omitted=True,
        reason="excluded: lockfile",
    )
    assert entry["omitted"] is True
    assert entry["fragment"] is None
    assert entry["reason"] == "excluded: lockfile"


# --- Hunk Anchorer: per-hunk ids + a manifest hunk index (ADR-0014, issue #63) ---

# Two real single-file unified diffs, each with a known hunk count.
_TWO_HUNK_DIFF = (
    "diff --git a/m.py b/m.py\n"
    "index 111..222 100644\n"
    "--- a/m.py\n"
    "+++ b/m.py\n"
    "@@ -1,3 +1,3 @@ def head():\n"
    " a\n"
    "-b\n"
    "+B\n"
    " c\n"
    "@@ -20,2 +20,3 @@ def tail():\n"
    " y\n"
    "+z\n"
    " w\n"
)
_ONE_HUNK_DIFF = "diff --git a/o.py b/o.py\n--- a/o.py\n+++ b/o.py\n@@ -1 +1 @@\n-old\n+new\n"
_RENAME_ONLY_DIFF = (
    "diff --git a/old.py b/new.py\nsimilarity index 100%\nrename from old.py\nrename to new.py\n"
)


def test_hunk_anchor_id_is_deterministic_and_url_safe() -> None:
    fid = file_fragment_id("src/m.py")
    assert hunk_anchor_id(fid, 1) == f"hunk-{fid}-1"
    # Only [a-z0-9-] (the "hunk-" prefix, a hex fid, digits) — a safe HTML id and a
    # safe URL #fragment: no separator, dot, space, or byte that needs escaping.
    anchor = hunk_anchor_id(fid, 7)
    assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in anchor)


def test_file_diff_fragment_splits_each_hunk_into_an_anchored_section() -> None:
    fid = file_fragment_id("m.py")
    html, hunks = file_diff_fragment(_TWO_HUNK_DIFF, fid)

    assert [h["index"] for h in hunks] == [1, 2]
    assert [h["anchor"] for h in hunks] == [hunk_anchor_id(fid, 1), hunk_anchor_id(fid, 2)]
    # header_html is the @@ line crossed through the boundary (escaped, marker-wrapped).
    assert hunks[0]["header_html"] == fragment("@@ -1,3 +1,3 @@ def head():")
    assert hunks[1]["header_html"] == fragment("@@ -20,2 +20,3 @@ def tail():")
    # `lines` is the exact rendered diff-body count for the reading weight (issue #100),
    # header excluded. Hunk 1 modifies in place (1 context + 1 removed + 1 added + 1
    # context = 4) — the header's max(3, 3) = 3 would undercount it; hunk 2 is a pure
    # addition (2 context + 1 added = 3), which the header would size the same.
    assert [h["lines"] for h in hunks] == [4, 3]
    # Each hunk is an individually anchored <pre>, and the preamble leads separately.
    assert html.startswith('<div class="file-diff">')
    assert html.count('<section class="hunk"') == 2
    assert f'<section class="hunk" id="{hunk_anchor_id(fid, 1)}">' in html
    assert '<pre class="diff diff-preamble">' in html


def test_file_diff_fragment_is_byte_lossless() -> None:
    # The reviewed change must render byte-for-byte: unescaping every untrusted region
    # back and concatenating must reproduce the original diff exactly.
    fid = file_fragment_id("m.py")
    html, _hunks = file_diff_fragment(_TWO_HUNK_DIFF, fid)
    from html import unescape

    regions = []
    rest = html
    while UNTRUSTED_OPEN in rest:
        _pre, _open, tail = rest.partition(UNTRUSTED_OPEN)
        body, _close, rest = tail.partition(UNTRUSTED_CLOSE)
        regions.append(unescape(body))
    assert "".join(regions) == _TWO_HUNK_DIFF


def test_file_diff_fragment_escapes_hostile_hunk_content() -> None:
    # Hostile diff content renders as text — a <script> hidden in a hunk body can only
    # ever appear as visible characters, per-hunk just as per-file (ADR-0002).
    fid = file_fragment_id("evil.py")
    hostile = (
        "diff --git a/evil.py b/evil.py\n"
        "--- a/evil.py\n"
        "+++ b/evil.py\n"
        "@@ -1 +1 @@ <script>owner()</script>\n"
        "-x\n"
        '+html = "<script>alert(document.cookie)</script>"\n'
    )
    html, hunks = file_diff_fragment(hostile, fid)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    # The hostile section heading crosses the boundary too — escaped, marker-wrapped,
    # never a raw untrusted string in the manifest.
    hdr = str(hunks[0]["header_html"])
    assert "<script>" not in hdr and "&lt;script&gt;" in hdr
    assert hdr.startswith(UNTRUSTED_OPEN) and hdr.endswith(UNTRUSTED_CLOSE)
    assert html.count(UNTRUSTED_OPEN) == html.count(UNTRUSTED_CLOSE)


def test_file_diff_fragment_single_hunk() -> None:
    fid = file_fragment_id("o.py")
    html, hunks = file_diff_fragment(_ONE_HUNK_DIFF, fid)
    assert [h["index"] for h in hunks] == [1]
    assert html.count('<section class="hunk"') == 1


def test_file_diff_fragment_rename_has_no_hunks() -> None:
    # A pure rename carries a preamble but no @@ — no hunk anchors to hand out.
    fid = file_fragment_id("new.py")
    html, hunks = file_diff_fragment(_RENAME_ONLY_DIFF, fid)
    assert hunks == []
    assert '<section class="hunk"' not in html
    assert '<pre class="diff diff-preamble">' in html
    assert "rename from old.py" in escape_text(_RENAME_ONLY_DIFF)  # content shown escaped


def test_file_diff_fragment_empty_diff_degrades_to_notice() -> None:
    html, hunks = file_diff_fragment("", file_fragment_id("x.py"))
    assert hunks == []
    assert "(no changes in this range)" in html
    assert UNTRUSTED_OPEN not in html


def test_file_diff_fragment_cr_in_body_does_not_forge_a_hunk() -> None:
    # A carriage return embedded in a hunk body must not be mistaken for a line break
    # that starts a new @@ hunk — split only on \n (git's line model), not \r.
    fid = file_fragment_id("cr.py")
    diff = "--- a/cr.py\n+++ b/cr.py\n@@ -1 +1 @@\n-old\r@@ -9 +9 @@ not a real header\n+new\n"
    _html, hunks = file_diff_fragment(diff, fid)
    assert [h["index"] for h in hunks] == [1]  # one hunk, not two


def test_fragment_index_entry_carries_hunk_index_when_given() -> None:
    hunks = [{"index": 1, "anchor": "hunk-abc-1", "header_html": fragment("@@ -1 +1 @@")}]
    entry = fragment_index_entry({"status": "M", "path": "m.py"}, hunks=hunks)
    assert entry["hunks"] == hunks
    # No hunks passed → no key (back-compat with pre-0.3 callers/consumers).
    assert "hunks" not in fragment_index_entry({"status": "M", "path": "m.py"})


def test_fragment_index_entry_rejects_hunks_on_an_omitted_body() -> None:
    # An omitted file has no diff fragment, so hunk ids for it are a contradiction.
    with pytest.raises(ValueError):
        fragment_index_entry(
            {"status": "M", "path": "uv.lock"},
            omitted=True,
            reason="dependency lockfile",
            hunks=[{"index": 1, "anchor": "hunk-x-1", "header_html": fragment("@@")}],
        )
