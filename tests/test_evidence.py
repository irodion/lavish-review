"""Tests for live evidence fragment injection (issue #43).

The acceptance criteria are pinned directly: an escaped, linted fragment lands at
the right claim's seam; a lint failure blocks the write entirely (nothing on disk
changes); injection is idempotent per seam and never touches content outside the
markers; and an injected fragment survives the close-time bake in the
self-contained artifact. Pure seam rendering/injection is exercised in memory; the
``add_evidence`` shell and the CLI run against real files in ``tmp_path``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from branch_review.bake import bake_review
from branch_review.escape import INTERACTIVE_CSP, STRICT_CSP, UNTRUSTED_OPEN
from branch_review.evidence import (
    EVIDENCE_NAME,
    EvidenceFragment,
    add_evidence,
    evidence_seam,
    inject_evidence_html,
    load_fragments,
    main,
    render_claim_evidence,
)
from branch_review.lint import lint_cockpit

_SEAM = "<!--brc:evidence:t1.c1--><!--/brc:evidence:t1.c1-->"

_COCKPIT = f"""<!doctype html>
<html><head>
<meta http-equiv="Content-Security-Policy" content="{INTERACTIVE_CSP}">
<link rel="stylesheet" href="assets/cockpit.css">
</head><body><main>
<section class="thread" id="t1"><h2><span class="thread-id">t1</span> Thread</h2>
<details class="claim" id="t1.c1"><summary>the claim</summary>
<div class="claim-body"><p class="detail">detail</p>
{_SEAM}
</div></details></section></main>
<!--brc:qa-log--><!--/brc:qa-log-->
<script src="assets/app.js"></script>
</body></html>
"""


def _fragment(body: str, *, title: str = "Callers", claim: str = "t1.c1") -> EvidenceFragment:
    return EvidenceFragment(
        claim=claim, seq=1, ts="2026-07-03T00:00:00+00:00", title=title, body=body
    )


@pytest.fixture
def cockpit(tmp_path: Path) -> Path:
    """A minimal, lint-clean interactive cockpit with the t1.c1 seam planted."""
    path = tmp_path / "review.html"
    path.write_text(_COCKPIT, encoding="utf-8")
    return path


# --- Pure: rendering and seam-bounded injection --------------------------------


def test_render_escapes_hostile_body_and_title() -> None:
    out = render_claim_evidence([_fragment("<script>alert(1)</script>", title="a <b> title")])
    assert "<script>" not in out and "<b>" not in out
    assert "&lt;script&gt;" in out and "a &lt;b&gt; title" in out
    assert UNTRUSTED_OPEN in out  # the body crossed the boundary, marker-wrapped


def test_render_empty_is_empty() -> None:
    assert render_claim_evidence([]) == ""


def test_evidence_seam_rejects_non_claim_ids() -> None:
    with pytest.raises(ValueError):
        evidence_seam("--><script>")


def test_injection_touches_only_the_seam_and_is_idempotent() -> None:
    content = render_claim_evidence([_fragment("+ new line")])
    once, found = inject_evidence_html(_COCKPIT, "t1.c1", content)
    assert found
    twice, found_again = inject_evidence_html(once, "t1.c1", content)
    assert found_again
    assert once == twice  # wholesale seam rewrite: re-injection never duplicates
    # Nothing outside the seam moved: strip both seam regions and compare.
    open_marker, close_marker = evidence_seam("t1.c1")
    before = _COCKPIT.split(open_marker)[0] + _COCKPIT.split(close_marker)[1]
    after = once.split(open_marker)[0] + once.split(close_marker)[1]
    assert before == after


def test_injection_without_seam_reports_not_found() -> None:
    html = _COCKPIT.replace(_SEAM, "")
    out, found = inject_evidence_html(html, "t1.c1", "anything")
    assert not found and out == html


# --- The gated shell -------------------------------------------------------------


def test_add_evidence_injects_escaped_and_lint_clean(cockpit: Path) -> None:
    errors = add_evidence(cockpit, "t1.c1", "Callers of retry()", '+ x = "<img src=x>"\n')
    assert errors == []
    html = cockpit.read_text(encoding="utf-8")
    assert "Callers of retry()" in html
    assert "<img" not in html and "&lt;img" in html  # the hostile body is inert
    assert lint_cockpit(html, csp_mode="interactive") == []
    # The record persisted beside the cockpit.
    fragments = load_fragments(cockpit.parent / EVIDENCE_NAME)
    assert len(fragments) == 1 and fragments[0].claim == "t1.c1"


def test_add_evidence_accumulates_without_duplicating(cockpit: Path) -> None:
    assert add_evidence(cockpit, "t1.c1", "First", "+ one\n") == []
    assert add_evidence(cockpit, "t1.c1", "Second", "+ two\n") == []
    html = cockpit.read_text(encoding="utf-8")
    assert html.count("First") == 1 and html.count("Second") == 1
    assert html.count('<figure class="live-evidence">') == 2
    assert [f.seq for f in load_fragments(cockpit.parent / EVIDENCE_NAME)] == [1, 2]


@pytest.mark.parametrize(
    ("claim", "title", "body", "needle"),
    [
        ("../../etc", "t", "b", "not a claim id"),
        ("t9.c9", "t", "b", "no evidence seam"),  # valid shape, but no seam authored
        ("t1.c1", "   ", "b", "title must not be empty"),
        ("t1.c1", "t", "", "body must not be empty"),
    ],
)
def test_add_evidence_refuses_bad_input_and_writes_nothing(
    cockpit: Path, claim: str, title: str, body: str, needle: str
) -> None:
    errors = add_evidence(cockpit, claim, title, body)
    assert errors and needle in errors[0]
    assert cockpit.read_text(encoding="utf-8") == _COCKPIT
    assert not (cockpit.parent / EVIDENCE_NAME).exists()


def test_lint_failure_blocks_injection_entirely(tmp_path: Path) -> None:
    # A cockpit with no CSP meta fails the lint — the injection must refuse to
    # write either the page or the record (the chat-only floor).
    broken = _COCKPIT.replace(
        f'<meta http-equiv="Content-Security-Policy" content="{INTERACTIVE_CSP}">\n', ""
    )
    cockpit = tmp_path / "review.html"
    cockpit.write_text(broken, encoding="utf-8")
    errors = add_evidence(cockpit, "t1.c1", "Callers", "+ x\n")
    assert errors and any(e.startswith("lint:") for e in errors)
    assert cockpit.read_text(encoding="utf-8") == broken
    assert not (tmp_path / EVIDENCE_NAME).exists()


def test_injected_evidence_survives_the_bake(cockpit: Path) -> None:
    # AC: present in the baked review.html — the bake rewrites only its own Q&A
    # seam, and the strict-CSP artifact still lints clean with the fragment in it.
    assert add_evidence(cockpit, "t1.c1", "Kept at close", "+ kept\n") == []

    bake_review(cockpit, qa_path=cockpit.parent / "absent.jsonl")

    baked = cockpit.read_text(encoding="utf-8")
    assert "Kept at close" in baked and "+ kept" in baked
    assert STRICT_CSP in baked
    assert lint_cockpit(baked, csp_mode="strict") == []


def test_corrupt_record_degrades_to_empty(tmp_path: Path) -> None:
    (tmp_path / EVIDENCE_NAME).write_text("{not json", encoding="utf-8")
    assert load_fragments(tmp_path / EVIDENCE_NAME) == []


def test_non_numeric_seq_falls_back_instead_of_crashing(tmp_path: Path) -> None:
    # Well-formed JSON with a hand-mangled seq must not raise through the
    # injection path — degrade, never crash.
    payload = {
        "schema": "review-live-evidence/0.1",
        "fragments": [{"claim": "t1.c1", "seq": "x", "ts": "t", "title": "T", "body": "b"}],
    }
    (tmp_path / EVIDENCE_NAME).write_text(json.dumps(payload), encoding="utf-8")
    fragments = load_fragments(tmp_path / EVIDENCE_NAME)
    assert len(fragments) == 1 and fragments[0].seq == 1


# --- CLI -------------------------------------------------------------------------


def test_cli_success_and_floor(cockpit: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tmp_path = cockpit.parent
    body = tmp_path / "evidence-input.txt"
    body.write_text("+ the callers\n", encoding="utf-8")

    ok = main(["t1.c1", "--title", "Callers", "--input", str(body), "--cockpit", str(cockpit)])
    assert ok == 0
    assert "Evidence injected" in capsys.readouterr().out

    # Unknown seam → blocked, non-zero, nothing written, "answer in chat" floor.
    before = cockpit.read_text(encoding="utf-8")
    fail = main(["t9.c9", "--title", "X", "--input", str(body), "--cockpit", str(cockpit)])
    assert fail == 1
    assert "answer in chat" in capsys.readouterr().err
    assert cockpit.read_text(encoding="utf-8") == before

    # Missing input file → usage-level error.
    assert (
        main(
            ["t1.c1", "--title", "X", "--input", str(tmp_path / "nope"), "--cockpit", str(cockpit)]
        )
        == 2
    )


def test_cli_records_are_json_readable(cockpit: Path) -> None:
    assert add_evidence(cockpit, "t1.c1", "T", "+ b\n") == []
    payload = json.loads((cockpit.parent / EVIDENCE_NAME).read_text(encoding="utf-8"))
    assert payload["schema"] == "review-live-evidence/0.1"
    assert payload["fragments"][0]["claim"] == "t1.c1"
