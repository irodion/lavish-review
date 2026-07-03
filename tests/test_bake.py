"""Tests for the Q&A bake at close + Markdown export (issue #9).

The bake's contract is fourfold: lift the reviewer's prompts out of each poll's stored
TOON with a *bounded* extractor (ADR-0007) that ignores the ``prompts[N]`` literals
inside ``dom_snapshot``; fold them into ``review.html`` through the Escape Boundary so
the saved cockpit is escaped and self-contained; swap the cockpit to the strict CSP so it
opens in a plain browser with no Lavish; and emit a Markdown export carrying the review +
Q&A. The acceptance test the issue names — *given qa.jsonl + a base review.html, the
baked output contains every answered question, escaped and self-contained* — is pinned
here against the real TOON shapes and proven self-contained by running the Cockpit Linter
in strict mode over the baked output.
"""

from __future__ import annotations

import json
from pathlib import Path

from branch_review.bake import (
    QA_SEAM_CLOSE,
    QA_SEAM_OPEN,
    Exchange,
    Prompt,
    bake_review,
    build_markdown,
    extract_prompts,
    inject_qa,
    load_exchanges,
    render_qa_html,
    render_qa_markdown,
    swap_csp,
)
from branch_review.escape import INTERACTIVE_CSP, STRICT_CSP, UNTRUSTED_CLOSE, UNTRUSTED_OPEN
from branch_review.lint import lint_cockpit

# --- Real TOON shapes, lifted from an actual qa.jsonl ------------------------
#
# Each is the tail of one poll's TOON: the column-0 prompts header + its row(s),
# followed by the ``next_step:`` top-level key that must terminate the row scan.

_TOON_SPAN = (
    "prompts[1]{uid,prompt,selector,tag,text}:\n"
    '  "2",what does this line do?,'
    '"body > main > section > pre > span:nth-of-type(727)",span,'
    '"+ _git(repo, \\"checkout\\", \\"feature\\")"\n'
    'next_step: "Apply the requested changes."\n'
)

_TOON_MESSAGE = (
    "prompts[1]{uid,prompt,selector,tag,text}:\n"
    '  "",what it the main goal of the branch,"",message,Freeform message\n'
    'next_step: "..."\n'
)

_TOON_LI = (
    "prompts[1]{uid,prompt,selector,tag,text}:\n"
    '  "1",What changed in this file?,'
    '"section#changed-files > ul:nth-of-type(1) > li:nth-of-type(4)",li,'
    "A .claude/skills/branch-review-cockpit/scripts/detect_test_runner.py\n"
    'next_step: "..."\n'
)

# A poll that carried two prompts at once — the row scan must collect exactly two.
_TOON_MULTI = (
    "prompts[2]{uid,prompt,selector,tag,text}:\n"
    '  "5",first question,"sel-a",span,"line a"\n'
    '  "6",second question,"sel-b",li,"line b"\n'
    'next_step: "..."\n'
)

# The decoy: a ``waiting`` poll whose dom_snapshot quotes the SKILL text that mentions
# ``prompts[N]`` — indented and inside a quoted scalar, so the column-0 anchor must skip
# it and find no real prompts block.
_TOON_DECOY = (
    "session:\n"
    "  status: waiting\n"
    'dom_snapshot: "uid=1 p \\"- `prompts[N]` arrived. Go to step b.\\"\\n'
    '  uid=2 span \\"more text\\""\n'
    'next_step: "Re-run poll."\n'
)


def _exchange(seq: int, toon: str, answer: str) -> Exchange:
    return Exchange(
        seq=seq, ts="2026-06-27T00:00:00+00:00", prompts=extract_prompts(toon), answer=answer
    )


# --- The bounded prompt extractor (ADR-0007) --------------------------------


def test_extract_span_annotation_unescapes_quotes() -> None:
    (prompt,) = extract_prompts(_TOON_SPAN)
    assert prompt.prompt == "what does this line do?"
    assert prompt.tag == "span"
    assert prompt.is_annotation
    # TOON's backslash-escaped inner quotes are unescaped by the CSV reader.
    assert prompt.text == '+ _git(repo, "checkout", "feature")'
    assert prompt.selector.endswith("span:nth-of-type(727)")


def test_extract_free_form_message_is_not_an_annotation() -> None:
    (prompt,) = extract_prompts(_TOON_MESSAGE)
    assert prompt.prompt == "what it the main goal of the branch"
    assert prompt.tag == "message"
    assert not prompt.is_annotation


def test_extract_li_annotation() -> None:
    (prompt,) = extract_prompts(_TOON_LI)
    assert prompt.prompt == "What changed in this file?"
    assert prompt.tag == "li"
    assert prompt.text.endswith("detect_test_runner.py")


def test_extract_multiple_prompts_bounded_by_count() -> None:
    prompts = extract_prompts(_TOON_MULTI)
    assert [p.prompt for p in prompts] == ["first question", "second question"]


def test_extract_ignores_prompts_literal_inside_dom_snapshot() -> None:
    # The column-0 anchor means a ``prompts[N]`` mention in the page snapshot is not a block.
    assert extract_prompts(_TOON_DECOY) == []


def test_extract_no_prompts_block_returns_empty() -> None:
    assert extract_prompts("session:\n  status: ended\n") == []


# --- Loading the transcript -------------------------------------------------


def test_load_exchanges_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_exchanges(tmp_path / "qa.jsonl") == []


def test_load_exchanges_skips_blank_and_corrupt_lines(tmp_path: Path) -> None:
    qa = tmp_path / "qa.jsonl"
    good = {"seq": 1, "ts": "t", "feedback_raw": _TOON_MESSAGE, "answer": "an answer"}
    qa.write_text(json.dumps(good) + "\n\nnot json\n", encoding="utf-8")
    exchanges = load_exchanges(qa)
    assert len(exchanges) == 1
    assert exchanges[0].answer == "an answer"
    assert exchanges[0].prompts[0].prompt == "what it the main goal of the branch"


def test_load_exchanges_tolerates_malformed_seq(tmp_path: Path) -> None:
    # A valid JSON object with a non-numeric seq must not abort the bake — it falls
    # back to the positional index rather than crashing on int(None)/int("bad").
    qa = tmp_path / "qa.jsonl"
    records = [
        {"seq": None, "ts": "t", "feedback_raw": _TOON_MESSAGE, "answer": "first"},
        {"seq": "bad", "ts": "t", "feedback_raw": _TOON_LI, "answer": "second"},
    ]
    qa.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    exchanges = load_exchanges(qa)
    assert [e.seq for e in exchanges] == [1, 2]
    assert [e.answer for e in exchanges] == ["first", "second"]


# --- HTML rendering ---------------------------------------------------------


def test_render_escapes_hostile_prompt_and_answer() -> None:
    toon = (
        "prompts[1]{uid,prompt,selector,tag,text}:\n"
        '  "9","<script>alert(1)</script>","s",span,"<img src=x>"\n'
        'next_step: "."\n'
    )
    html = render_qa_html([_exchange(1, toon, "answer with <b>markup</b>")])
    # No live markup survives anywhere in the section.
    assert "<script>" not in html
    assert "<img src=x>" not in html
    assert "<b>markup</b>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;b&gt;markup&lt;/b&gt;" in html


def test_render_untrusted_regions_are_marker_wrapped() -> None:
    html = render_qa_html([_exchange(1, _TOON_SPAN, "ok")])
    assert html.count(UNTRUSTED_OPEN) == html.count(UNTRUSTED_CLOSE)
    assert UNTRUSTED_OPEN in html  # the prompt/anchor/selector crossed the boundary


def test_render_empty_transcript_has_placeholder() -> None:
    html = render_qa_html([])
    assert 'id="qa-log"' in html
    assert "No questions were asked" in html


# --- Injection & idempotency ------------------------------------------------

_SEAM_HTML = f"<html><body><main>x</main>\n{QA_SEAM_OPEN}{QA_SEAM_CLOSE}\n</body></html>"
_NO_SEAM_HTML = "<html><body><main>x</main></body></html>"


def test_inject_fills_the_seam() -> None:
    out = inject_qa(_SEAM_HTML, "<section id='qa-log'>Q</section>\n")
    assert out.count("id=") == 1
    assert "Q</section>" in out
    assert QA_SEAM_OPEN in out and QA_SEAM_CLOSE in out


def test_inject_is_idempotent_across_rebakes() -> None:
    once = inject_qa(_SEAM_HTML, "<section id='qa-log'>FIRST</section>\n")
    twice = inject_qa(once, "<section id='qa-log'>SECOND</section>\n")
    assert twice.count(QA_SEAM_OPEN) == 1
    assert twice.count("id='qa-log'") == 1
    assert "SECOND" in twice and "FIRST" not in twice


def test_inject_fallback_before_body_when_no_seam() -> None:
    out = inject_qa(_NO_SEAM_HTML, "<section id='qa-log'>Q</section>\n")
    assert "id='qa-log'" in out
    assert out.index("qa-log") < out.index("</body>")


def test_inject_replacement_preserves_backslashes() -> None:
    # A regex sub must not interpret backslashes in the rendered section as backreferences.
    out = inject_qa(_SEAM_HTML, "path C:\\n\\g<0> literal\n")
    assert "C:\\n\\g<0>" in out


# --- CSP swap ---------------------------------------------------------------


def test_swap_csp_interactive_to_strict() -> None:
    html = f'<meta http-equiv="Content-Security-Policy" content="{INTERACTIVE_CSP}">'
    out, swapped = swap_csp(html)
    assert swapped
    assert STRICT_CSP in out
    assert "unsafe-inline" not in out


def test_swap_csp_handles_multiline_meta() -> None:
    html = f'<meta http-equiv="Content-Security-Policy"\n      content="{INTERACTIVE_CSP}">'
    out, swapped = swap_csp(html)
    assert swapped and STRICT_CSP in out


def test_swap_csp_reports_when_no_policy() -> None:
    out, swapped = swap_csp("<meta charset='utf-8'>")
    assert not swapped
    assert out == "<meta charset='utf-8'>"


# --- Markdown export --------------------------------------------------------

_ANALYSIS = {
    "title": "My Review",
    "intent_summary": "Does a thing.",
    "widened_into": [],
    "alignment": {"serves_goal": ["t1"], "drive_by": ["t2"]},
    "threads": [
        {
            "id": "t1",
            "title": "The thing",
            "summary": "One thread of change.",
            "paths": ["src/a.py"],
            "claims": [
                {
                    "id": "t1.c1",
                    "kind": "risk",
                    "category": "security",
                    "level": "high",
                    "summary": "R",
                    "confidence": "medium",
                    "challenge_questions": ["Q1?"],
                    "evidence": [{"path": "src/a.py"}],
                },
                {
                    "id": "t1.c2",
                    "kind": "verify",
                    "summary": "run it",
                    "confidence": "high",
                    "challenge_questions": ["Does it pass?"],
                    "evidence": [{"note": "n"}],
                },
            ],
        },
        {
            "id": "t2",
            "title": "A rename that rode along",
            "summary": "Unrelated to the goal.",
            "paths": ["src/b.py"],
            "claims": [
                {
                    "id": "t2.c1",
                    "kind": "behavior",
                    "summary": "B",
                    "confidence": "high",
                    "challenge_questions": ["Q2?"],
                    "evidence": [{"path": "src/b.py"}],
                },
            ],
        },
    ],
    "test_runner": {"runner": "pytest", "command": "pytest"},
    "diagrams": [],
}


def test_markdown_contains_review_and_qa() -> None:
    md = build_markdown(_ANALYSIS, [_exchange(1, _TOON_MESSAGE, "the answer")])
    assert "# My Review" in md
    assert "## Orientation" in md and "Does a thing." in md
    assert "## t1 — The thing" in md and "One thread of change." in md
    assert "[risk] R (confidence: medium; security; level: high)" in md and "Q1?" in md
    # Verify claims export as REAL task-list checkboxes: the marker sits at line
    # start — inside a ### heading GitHub renders "- [ ]" as literal text.
    assert "\n- [ ] [verify] run it (confidence: high)" in md
    assert "### - [ ]" not in md
    assert "\n  - Does it pass?" in md  # its challenge question stays inside the item
    # Goal alignment (ADR-0010): one orientation line, and drive-bys flagged in headings.
    assert (
        "Goal alignment — serving the stated goal: t1; drive-by (unrelated to the goal): t2." in md
    )
    assert "## t2 — A rename that rode along (drive-by)" in md
    assert "## t1 — The thing\n" in md  # a goal-serving thread carries no flag
    assert "`pytest`" in md
    assert "## Q&A Log" in md
    assert "what it the main goal of the branch" in md
    assert "the answer" in md


def test_markdown_null_alignment_has_no_goal_line() -> None:
    # No stated goal (alignment null, ADR-0010): the export carries no alignment
    # line and flags nothing — silence, not an invented judgement.
    analysis = dict(_ANALYSIS, alignment=None)
    md = build_markdown(analysis, [])
    assert "Goal alignment" not in md
    assert "(drive-by)" not in md


def test_markdown_without_analysis_still_has_qa() -> None:
    md = build_markdown(None, [_exchange(1, _TOON_MESSAGE, "ans")])
    assert "# Branch Review" in md
    assert "## Q&A Log" in md and "ans" in md


def test_markdown_fences_outlive_backtick_runs() -> None:
    # An annotated line containing a triple-backtick must not break out of its fence.
    toon = (
        "prompts[1]{uid,prompt,selector,tag,text}:\n"
        '  "1",see this,"s",span,"text with ``` fence"\n'
        'next_step: "."\n'
    )
    md = render_qa_markdown([_exchange(1, toon, "a")])
    assert "````" in md  # fence widened past the inner triple-backtick


def test_markdown_collapses_newlines_in_prompt_heading() -> None:
    # A reviewer prompt carrying a newline + Markdown must not inject new blocks into
    # the pasted PR body — it is collapsed inline onto the question heading.
    exchange = Exchange(
        seq=1,
        ts="t",
        prompts=[
            Prompt(uid="1", prompt="real q?\n## Injected", selector="", tag="message", text="")
        ],
        answer="a",
    )
    md = render_qa_markdown([exchange])
    assert "\n## Injected" not in md  # never a standalone heading block
    assert "### Q1. real q? ## Injected" in md  # collapsed inline into the heading


# --- End-to-end: the issue's acceptance criterion ---------------------------

_BASE_COCKPIT = f"""<!doctype html>
<html><head>
<meta http-equiv="Content-Security-Policy" content="{INTERACTIVE_CSP}">
<link rel="stylesheet" href="assets/cockpit.css">
</head><body>
<main><section id="exec"><h2>Summary</h2><p>prose</p></section></main>
{QA_SEAM_OPEN}{QA_SEAM_CLOSE}
<script src="assets/app.js"></script>
</body></html>
"""


def _write_transcript(path: Path) -> None:
    records = [
        {"seq": 1, "ts": "2026-06-27T13:42Z", "feedback_raw": _TOON_SPAN, "answer": "one"},
        {"seq": 2, "ts": "2026-06-27T13:43Z", "feedback_raw": _TOON_MESSAGE, "answer": "two"},
        {"seq": 3, "ts": "2026-06-27T13:44Z", "feedback_raw": _TOON_LI, "answer": "ans <3>"},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def test_bake_review_contains_every_question_escaped_and_self_contained(tmp_path: Path) -> None:
    """The acceptance criterion: every answered question is baked, escaped, self-contained."""
    cockpit = tmp_path / "review.html"
    cockpit.write_text(_BASE_COCKPIT, encoding="utf-8")
    qa = tmp_path / "qa.jsonl"
    _write_transcript(qa)
    analysis = tmp_path / "analysis.json"
    analysis.write_text(json.dumps(_ANALYSIS), encoding="utf-8")
    md = tmp_path / "review.md"

    result = bake_review(cockpit, qa_path=qa, analysis_path=analysis, markdown_path=md)

    baked = cockpit.read_text(encoding="utf-8")
    # Every question present, escaped.
    assert "what does this line do?" in baked
    assert "what it the main goal of the branch" in baked
    assert "What changed in this file?" in baked
    assert "ans &lt;3&gt;" in baked  # the answer's angle brackets are escaped

    # Self-contained: strict CSP, no Lavish-only allowances, passes the strict linter.
    assert STRICT_CSP in baked
    assert lint_cockpit(baked, csp_mode="strict") == []

    # Markdown export written with review + Q&A.
    assert md.is_file()
    md_text = md.read_text(encoding="utf-8")
    assert "## Q&A Log" in md_text and "one" in md_text

    assert result.exchanges == 3 and result.prompts == 3 and result.csp_swapped


_TOON_DISPOSITION = (
    "prompts[1]{uid,prompt,selector,tag,text}:\n"
    '  "9",Disposition set: t1.c1 -> concern,"summary > button",choice,disposition:concern\n'
    'next_step: "..."\n'
)


def test_bake_review_outcome_section_and_disposition_filtering(tmp_path: Path) -> None:
    """ADR-0012 at close: the outcome states the reviewer's dispositions; disposition
    updates are review state, not conversation, so they vanish from the Q&A log."""
    cockpit = tmp_path / "review.html"
    cockpit.write_text(_BASE_COCKPIT, encoding="utf-8")
    analysis = tmp_path / "analysis.json"
    analysis.write_text(json.dumps(_ANALYSIS), encoding="utf-8")
    (tmp_path / "dispositions.json").write_text(
        json.dumps(
            {
                "schema": "review-dispositions/0.1",
                "dispositions": {"t1.c1": "concern", "t2.c1": "verified"},
            }
        ),
        encoding="utf-8",
    )
    qa = tmp_path / "qa.jsonl"
    records = [
        # A disposition-only exchange: its prompt AND its ack answer are filtered out.
        {"seq": 1, "ts": "t", "feedback_raw": _TOON_DISPOSITION, "answer": "Recorded."},
        {"seq": 2, "ts": "t", "feedback_raw": _TOON_MESSAGE, "answer": "a real answer"},
    ]
    qa.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    md = tmp_path / "review.md"

    result = bake_review(cockpit, qa_path=qa, analysis_path=analysis, markdown_path=md)

    baked = cockpit.read_text(encoding="utf-8")
    assert 'id="review-outcome"' in baked
    # The aggregate is the reviewer's; every claim is accounted for, unreviewed listed.
    assert "verified 1 · concern 1 · question-open 0 · unreviewed 1" in baked
    assert '<span class="disposition concern">concern</span> <code>t1.c1</code>' in baked
    assert '<span class="disposition unreviewed">unreviewed</span> <code>t1.c2</code>' in baked
    # The disposition exchange is state, not conversation — gone from the Q&A log.
    assert "Disposition set:" not in baked
    assert "Recorded." not in baked
    assert "what it the main goal of the branch" in baked  # the real question stays
    assert lint_cockpit(baked, csp_mode="strict") == []
    assert result.exchanges == 1  # only the real exchange was folded

    md_text = md.read_text(encoding="utf-8")
    assert "## Review outcome" in md_text
    assert "Reviewer dispositions — verified 1 · concern 1 · question-open 0 · unreviewed 1." in (
        md_text
    )
    assert "- **concern** — t1.c1: R" in md_text
    assert "- **unreviewed** — t1.c2: run it" in md_text
    assert "Disposition set:" not in md_text
    assert "no overall verdict" in md_text  # the attribution note (verdict line, ADR-0012)


def test_outcome_absent_without_analysis(tmp_path: Path) -> None:
    # No analysis → no claims to account for: the bake degrades to Q&A only.
    cockpit = tmp_path / "review.html"
    cockpit.write_text(_BASE_COCKPIT, encoding="utf-8")
    bake_review(cockpit, qa_path=tmp_path / "absent.jsonl")
    assert 'id="review-outcome"' not in cockpit.read_text(encoding="utf-8")


def test_bake_review_empty_transcript_is_still_self_contained(tmp_path: Path) -> None:
    cockpit = tmp_path / "review.html"
    cockpit.write_text(_BASE_COCKPIT, encoding="utf-8")
    result = bake_review(cockpit, qa_path=tmp_path / "absent.jsonl")
    baked = cockpit.read_text(encoding="utf-8")
    assert result.exchanges == 0
    assert "No questions were asked" in baked
    assert STRICT_CSP in baked
    assert lint_cockpit(baked, csp_mode="strict") == []


def test_bake_review_missing_cockpit_errors(tmp_path: Path) -> None:
    from branch_review.bake import main

    assert main([str(tmp_path / "nope.html")]) == 2
