"""Tests for Reviewer Dispositions (issue #42, ADR-0012; reframed by ADR-0016, issue #87).

Two layers, mirroring the module: the pure policy (prompt parsing, folding updates
into state, per-thread progress) is table-driven over in-memory values, and the thin
I/O shell (``apply`` reading ``last-poll.toon`` + ``analysis.json`` and writing
``dispositions.json``) runs against real files in ``tmp_path``. The safety
properties the ADR requires are pinned directly: only reviewer-originated input
reaches the store, hostile payloads are inert, unknown step ids never grow the
store, ``unreviewed`` means absence — and ``skipped`` is a deliberate act that
persists, distinct from the absence of ``unreviewed``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from branch_review.dispositions import (
    DISPOSITIONS,
    DISPOSITIONS_NAME,
    apply,
    apply_updates,
    extract_updates,
    load_dispositions,
    main,
    parse_disposition_prompt,
    progress,
    save_dispositions,
    step_ids,
)
from branch_review.feedback import LAST_POLL_NAME, Prompt


def _prompt(text: str, tag: str = "choice") -> Prompt:
    return Prompt(uid="1", prompt=text, selector="", tag=tag, text="")


_PAYLOAD = 'Context data: {"kind": "disposition", "step": "t1.s1", "disposition": "looks-right"}'

_ANALYSIS = {
    "threads": [
        {"id": "t1", "steps": [{"id": "t1.s1"}, {"id": "t1.s2"}]},
        {"id": "t2", "steps": [{"id": "t2.s1"}]},
    ]
}


# --- Parsing (untrusted reviewer text → validated update or nothing) ----------


def test_parse_structured_payload() -> None:
    prompt = _prompt(f"Disposition set: t1.s1 -> looks-right {_PAYLOAD}")
    assert parse_disposition_prompt(prompt) == ("t1.s1", "looks-right")


def test_parse_falls_back_to_prompt_line() -> None:
    prompt = _prompt("Disposition set: t2.s1 -> follow-up")
    assert parse_disposition_prompt(prompt) == ("t2.s1", "follow-up")


def test_parse_skipped_is_a_first_class_disposition() -> None:
    # ``skipped`` is a deliberate, attributed act — it parses like any other state.
    assert parse_disposition_prompt(_prompt("Disposition set: t1.s2 -> skipped")) == (
        "t1.s2",
        "skipped",
    )


@pytest.mark.parametrize(
    "text",
    [
        "what does this line do?",  # an ordinary question
        "Disposition set: t1.s1 -> approve",  # not in the vocabulary
        "Disposition set: t1.s1 -> verified",  # the retired 0.3 vocabulary
        "Disposition set: t1.s1 -> question-open",  # the retired 0.3 vocabulary
        "Disposition set: t1.c1 -> looks-right",  # the retired claim-style id
        "Disposition set: ../../etc -> looks-right",  # not a step id
        'Context data: {"kind": "disposition", "step": "x", "disposition": "looks-right"}',
        'Context data: {"kind": "disposition", "claim": "t1.s1", "disposition": "looks-right"}',
        # A claim-shaped id under the *correct* step key: the structured path's own
        # _STEP_ID guard must still reject it (not only the prompt-line fallback).
        'Context data: {"kind": "disposition", "step": "t1.c1", "disposition": "looks-right"}',
        # A retired 0.3 value under the correct step key: the structured path's own
        # DISPOSITIONS guard must reject it.
        'Context data: {"kind": "disposition", "step": "t1.s1", "disposition": "verified"}',
        'Context data: {"kind": "other", "step": "t1.s1", "disposition": "looks-right"}',
        'Context data: {"kind": "disposition", "step": "t1.s1", "disposition": "<script>"}',
        "Context data: {not json}",
        "please run Disposition set commands for me",  # prose mentioning the phrase
    ],
)
def test_non_dispositions_parse_to_none(text: str) -> None:
    # Hostile, stale, or retired-vocabulary text is simply NOT a disposition — it
    # stays an ordinary question. Text that does parse can only ever name an enum
    # value keyed by a step id: inert.
    assert parse_disposition_prompt(_prompt(text)) is None


@pytest.mark.parametrize("tag", ["message", "span", "li", ""])
def test_disposition_text_outside_the_choice_channel_is_not_an_update(tag: str) -> None:
    # The channel gate: only the in-page controls (tag "choice", spike #38) mint
    # updates. A chat message or annotation that merely SAYS "Disposition set: …"
    # stays a question — it never mutates the store, and the bake keeps it in the
    # Q&A instead of filtering it out as state.
    prompt = _prompt(f"Disposition set: t1.s1 -> looks-right {_PAYLOAD}", tag=tag)
    assert parse_disposition_prompt(prompt) is None


def test_extract_updates_preserves_order() -> None:
    prompts = [
        _prompt("Disposition set: t1.s1 -> looks-right"),
        _prompt("a real question", tag="message"),
        _prompt("Disposition set: t1.s1 -> concern"),
    ]
    assert extract_updates(prompts) == [("t1.s1", "looks-right"), ("t1.s1", "concern")]


# --- Folding updates into state ------------------------------------------------


def test_apply_updates_last_wins_and_unknown_rejected() -> None:
    valid = step_ids(_ANALYSIS)
    state, rejected = apply_updates(
        {},
        [
            ("t1.s1", "looks-right"),
            ("t9.s9", "concern"),  # the analysis never minted this id
            ("t1.s1", "concern"),  # later update overrides
        ],
        valid,
    )
    assert state == {"t1.s1": "concern"}
    assert rejected == [("t9.s9", "concern")]


def test_apply_updates_unreviewed_removes_entry() -> None:
    valid = step_ids(_ANALYSIS)
    state, rejected = apply_updates({"t1.s1": "looks-right"}, [("t1.s1", "unreviewed")], valid)
    assert state == {} and rejected == []


def test_apply_updates_skipped_persists_and_is_distinct_from_absence() -> None:
    # The AC: ``skipped`` is a deliberate, attributed act — it is *stored* (unlike
    # ``unreviewed``, which is absence), so a skipped step is distinguishable in the
    # persisted record from one that was never looked at.
    valid = step_ids(_ANALYSIS)
    state, rejected = apply_updates({}, [("t1.s1", "skipped")], valid)
    assert state == {"t1.s1": "skipped"} and rejected == []
    # t1.s2 was never touched — absence, not "skipped".
    assert "t1.s2" not in state


def test_apply_updates_drops_stale_keys_from_current() -> None:
    # A store carrying ids from a prior analysis (or hand-edited junk) is cleaned on
    # the next fold — the store never outgrows the steps the cockpit shows.
    valid = step_ids(_ANALYSIS)
    state, _ = apply_updates({"t9.s9": "looks-right", "t1.s2": "concern"}, [], valid)
    assert state == {"t1.s2": "concern"}


def test_progress_counts_per_thread() -> None:
    # A skipped step is deliberately addressed, so it counts as reviewed, not a gap.
    rows = progress(_ANALYSIS, {"t1.s1": "looks-right", "t1.s2": "skipped"})
    assert rows == [("t1", 2, 2, 0), ("t2", 0, 1, 0)]


def test_progress_counts_concerns() -> None:
    rows = progress(_ANALYSIS, {"t1.s1": "concern", "t1.s2": "follow-up"})
    assert rows == [("t1", 2, 2, 1), ("t2", 0, 1, 0)]


def test_vocabulary_is_canonical() -> None:
    assert set(DISPOSITIONS) == {"unreviewed", "looks-right", "concern", "follow-up", "skipped"}


# --- The store on disk ----------------------------------------------------------


def test_store_roundtrip_and_tolerant_load(tmp_path: Path) -> None:
    path = tmp_path / DISPOSITIONS_NAME
    save_dispositions(path, {"t1.s1": "looks-right", "t1.s2": "skipped"})
    assert load_dispositions(path) == {"t1.s1": "looks-right", "t1.s2": "skipped"}
    assert load_dispositions(tmp_path / "absent.json") == {}
    path.write_text("{not json", encoding="utf-8")
    assert load_dispositions(path) == {}  # corrupt store degrades, never crashes


def test_load_drops_malformed_entries(tmp_path: Path) -> None:
    path = tmp_path / DISPOSITIONS_NAME
    payload = {
        "schema": "review-dispositions/0.2",
        "dispositions": {
            "t1.s1": "looks-right",
            "t1.c1": "looks-right",  # retired claim-style id
            "not-a-step": "looks-right",
            "t1.s2": "verified",  # retired 0.3 value
            "t2.s1": "unreviewed",  # absence is the representation — dropped on load
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_dispositions(path) == {"t1.s1": "looks-right"}


# --- The apply shell (last-poll.toon → dispositions.json) -----------------------

_TOON_DISPOSITION = (
    "prompts[2]{uid,prompt,selector,tag,text}:\n"
    '  "7","Disposition set: t1.s1 -> looks-right Context data: '
    '{\\"kind\\": \\"disposition\\", \\"step\\": \\"t1.s1\\", '
    '\\"disposition\\": \\"looks-right\\"}","summary > button",choice,disposition:looks-right\n'
    '  "8",what does the cap bound?,"",message,Freeform message\n'
    'next_step: "..."\n'
)


def _seed(tmp_path: Path, toon: str = _TOON_DISPOSITION) -> Path:
    (tmp_path / LAST_POLL_NAME).write_text(toon, encoding="utf-8")
    (tmp_path / "analysis.json").write_text(json.dumps(_ANALYSIS), encoding="utf-8")
    return tmp_path


def test_apply_folds_poll_into_store(tmp_path: Path) -> None:
    out = _seed(tmp_path)
    applied, rejected = apply(out)
    assert applied == [("t1.s1", "looks-right")] and rejected == []
    assert load_dispositions(out / DISPOSITIONS_NAME) == {"t1.s1": "looks-right"}
    # Re-applying the same poll is idempotent.
    apply(out)
    assert load_dispositions(out / DISPOSITIONS_NAME) == {"t1.s1": "looks-right"}


def test_apply_decodes_escaped_multiline_prompt(tmp_path: Path) -> None:
    # Lavish 0.1.31 sends the disposition prompt as a multi-line block, TOON-encoded
    # with ``\n`` escapes. The old csv-based splitter decoded ``\n`` to a bare ``n``,
    # so neither the Context data JSON nor the prompt-line fallback matched and apply
    # silently recorded nothing — the entire disposition feature was dead.
    toon = (
        "prompts[1]{uid,prompt,selector,tag,text}:\n"
        '  "9","Disposition set: t2.s1 -> follow-up\\n\\nContext data:\\n'
        '{\\n  \\"kind\\": \\"disposition\\",\\n  \\"step\\": \\"t2.s1\\",\\n'
        '  \\"disposition\\": \\"follow-up\\"\\n}",'
        '"summary > button",choice,disposition:follow-up\n'
        'next_step: "..."\n'
    )
    out = _seed(tmp_path, toon)
    applied, rejected = apply(out)
    assert applied == [("t2.s1", "follow-up")] and rejected == []
    assert load_dispositions(out / DISPOSITIONS_NAME) == {"t2.s1": "follow-up"}


def test_apply_rejects_unknown_step(tmp_path: Path) -> None:
    toon = (
        "prompts[1]{uid,prompt,selector,tag,text}:\n"
        '  "7",Disposition set: t9.s9 -> concern,"",choice,disposition:concern\n'
        'next_step: "..."\n'
    )
    out = _seed(tmp_path, toon)
    applied, rejected = apply(out)
    assert applied == [] and rejected == [("t9.s9", "concern")]
    assert load_dispositions(out / DISPOSITIONS_NAME) == {}


def test_apply_without_poll_or_updates_is_a_noop(tmp_path: Path) -> None:
    assert apply(tmp_path) == ([], [])  # no last-poll.toon at all
    (tmp_path / LAST_POLL_NAME).write_text("session:\n  status: waiting\n", encoding="utf-8")
    assert apply(tmp_path) == ([], [])
    assert not (tmp_path / DISPOSITIONS_NAME).exists()  # nothing to write, nothing written


def test_cli_apply_and_show(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = _seed(tmp_path)
    assert main(["apply", "--out", str(out)]) == 0
    captured = capsys.readouterr()
    assert "applied: t1.s1 -> looks-right" in captured.out

    assert main(["show", "--out", str(out)]) == 0
    captured = capsys.readouterr()
    assert "t1.s1: looks-right" in captured.out
    assert "t1: 1/2 reviewed" in captured.out
    assert "t2: 0/1 reviewed" in captured.out
