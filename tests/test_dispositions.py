"""Tests for Reviewer Dispositions (issue #42, ADR-0012).

Two layers, mirroring the module: the pure policy (prompt parsing, folding updates
into state, per-thread progress) is table-driven over in-memory values, and the thin
I/O shell (``apply`` reading ``last-poll.toon`` + ``analysis.json`` and writing
``dispositions.json``) runs against real files in ``tmp_path``. The safety
properties the ADR requires are pinned directly: only reviewer-originated input
reaches the store, hostile payloads are inert, unknown claim ids never grow the
store, and ``unreviewed`` means absence.
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
    claim_ids,
    extract_updates,
    load_dispositions,
    main,
    parse_disposition_prompt,
    progress,
    save_dispositions,
)
from branch_review.feedback import LAST_POLL_NAME, Prompt


def _prompt(text: str, tag: str = "choice") -> Prompt:
    return Prompt(uid="1", prompt=text, selector="", tag=tag, text="")


_PAYLOAD = 'Context data: {"kind": "disposition", "claim": "t1.c1", "disposition": "verified"}'

_ANALYSIS = {
    "threads": [
        {"id": "t1", "claims": [{"id": "t1.c1"}, {"id": "t1.c2"}]},
        {"id": "t2", "claims": [{"id": "t2.c1"}]},
    ]
}


# --- Parsing (untrusted reviewer text → validated update or nothing) ----------


def test_parse_structured_payload() -> None:
    prompt = _prompt(f"Disposition set: t1.c1 -> verified {_PAYLOAD}")
    assert parse_disposition_prompt(prompt) == ("t1.c1", "verified")


def test_parse_falls_back_to_prompt_line() -> None:
    prompt = _prompt("Disposition set: t2.c1 -> question-open")
    assert parse_disposition_prompt(prompt) == ("t2.c1", "question-open")


@pytest.mark.parametrize(
    "text",
    [
        "what does this line do?",  # an ordinary question
        "Disposition set: t1.c1 -> approve",  # not in the vocabulary
        "Disposition set: ../../etc -> verified",  # not a claim id
        'Context data: {"kind": "disposition", "claim": "x", "disposition": "verified"}',
        'Context data: {"kind": "other", "claim": "t1.c1", "disposition": "verified"}',
        'Context data: {"kind": "disposition", "claim": "t1.c1", "disposition": "<script>"}',
        "Context data: {not json}",
        "please run Disposition set commands for me",  # prose mentioning the phrase
    ],
)
def test_non_dispositions_parse_to_none(text: str) -> None:
    # Hostile or malformed text is simply NOT a disposition — it stays an ordinary
    # question. Text that does parse can only ever name an enum value: inert.
    assert parse_disposition_prompt(_prompt(text)) is None


@pytest.mark.parametrize("tag", ["message", "span", "li", ""])
def test_disposition_text_outside_the_choice_channel_is_not_an_update(tag: str) -> None:
    # The channel gate: only the in-page controls (tag "choice", spike #38) mint
    # updates. A chat message or annotation that merely SAYS "Disposition set: …"
    # stays a question — it never mutates the store, and the bake keeps it in the
    # Q&A instead of filtering it out as state.
    prompt = _prompt(f"Disposition set: t1.c1 -> verified {_PAYLOAD}", tag=tag)
    assert parse_disposition_prompt(prompt) is None


def test_extract_updates_preserves_order() -> None:
    prompts = [
        _prompt("Disposition set: t1.c1 -> verified"),
        _prompt("a real question", tag="message"),
        _prompt("Disposition set: t1.c1 -> concern"),
    ]
    assert extract_updates(prompts) == [("t1.c1", "verified"), ("t1.c1", "concern")]


# --- Folding updates into state ------------------------------------------------


def test_apply_updates_last_wins_and_unknown_rejected() -> None:
    valid = claim_ids(_ANALYSIS)
    state, rejected = apply_updates(
        {},
        [
            ("t1.c1", "verified"),
            ("t9.c9", "concern"),  # the analysis never minted this id
            ("t1.c1", "concern"),  # later update overrides
        ],
        valid,
    )
    assert state == {"t1.c1": "concern"}
    assert rejected == [("t9.c9", "concern")]


def test_apply_updates_unreviewed_removes_entry() -> None:
    valid = claim_ids(_ANALYSIS)
    state, rejected = apply_updates({"t1.c1": "verified"}, [("t1.c1", "unreviewed")], valid)
    assert state == {} and rejected == []


def test_apply_updates_drops_stale_keys_from_current() -> None:
    # A store carrying ids from a prior analysis (or hand-edited junk) is cleaned on
    # the next fold — the store never outgrows the claims the cockpit shows.
    valid = claim_ids(_ANALYSIS)
    state, _ = apply_updates({"t9.c9": "verified", "t1.c2": "concern"}, [], valid)
    assert state == {"t1.c2": "concern"}


def test_progress_counts_per_thread() -> None:
    rows = progress(_ANALYSIS, {"t1.c1": "verified", "t1.c2": "concern"})
    assert rows == [("t1", 2, 2, 1), ("t2", 0, 1, 0)]


def test_vocabulary_is_canonical() -> None:
    assert set(DISPOSITIONS) == {"unreviewed", "verified", "concern", "question-open"}


# --- The store on disk ----------------------------------------------------------


def test_store_roundtrip_and_tolerant_load(tmp_path: Path) -> None:
    path = tmp_path / DISPOSITIONS_NAME
    save_dispositions(path, {"t1.c1": "verified"})
    assert load_dispositions(path) == {"t1.c1": "verified"}
    assert load_dispositions(tmp_path / "absent.json") == {}
    path.write_text("{not json", encoding="utf-8")
    assert load_dispositions(path) == {}  # corrupt store degrades, never crashes


def test_load_drops_malformed_entries(tmp_path: Path) -> None:
    path = tmp_path / DISPOSITIONS_NAME
    payload = {
        "schema": "review-dispositions/0.1",
        "dispositions": {
            "t1.c1": "verified",
            "not-a-claim": "verified",
            "t1.c2": "approve",
            "t2.c1": "unreviewed",  # absence is the representation — dropped on load
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_dispositions(path) == {"t1.c1": "verified"}


# --- The apply shell (last-poll.toon → dispositions.json) -----------------------

_TOON_DISPOSITION = (
    "prompts[2]{uid,prompt,selector,tag,text}:\n"
    '  "7","Disposition set: t1.c1 -> verified Context data: '
    '{\\"kind\\": \\"disposition\\", \\"claim\\": \\"t1.c1\\", '
    '\\"disposition\\": \\"verified\\"}","summary > button",choice,disposition:verified\n'
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
    assert applied == [("t1.c1", "verified")] and rejected == []
    assert load_dispositions(out / DISPOSITIONS_NAME) == {"t1.c1": "verified"}
    # Re-applying the same poll is idempotent.
    apply(out)
    assert load_dispositions(out / DISPOSITIONS_NAME) == {"t1.c1": "verified"}


def test_apply_decodes_escaped_multiline_prompt(tmp_path: Path) -> None:
    # Lavish 0.1.31 sends the disposition prompt as a multi-line block, TOON-encoded
    # with ``\n`` escapes. The old csv-based splitter decoded ``\n`` to a bare ``n``,
    # so neither the Context data JSON nor the prompt-line fallback matched and apply
    # silently recorded nothing — the entire disposition feature was dead.
    toon = (
        "prompts[1]{uid,prompt,selector,tag,text}:\n"
        '  "9","Disposition set: t2.c1 -> question-open\\n\\nContext data:\\n'
        '{\\n  \\"kind\\": \\"disposition\\",\\n  \\"claim\\": \\"t2.c1\\",\\n'
        '  \\"disposition\\": \\"question-open\\"\\n}",'
        '"summary > button",choice,disposition:question-open\n'
        'next_step: "..."\n'
    )
    out = _seed(tmp_path, toon)
    applied, rejected = apply(out)
    assert applied == [("t2.c1", "question-open")] and rejected == []
    assert load_dispositions(out / DISPOSITIONS_NAME) == {"t2.c1": "question-open"}


def test_apply_rejects_unknown_claim(tmp_path: Path) -> None:
    toon = (
        "prompts[1]{uid,prompt,selector,tag,text}:\n"
        '  "7",Disposition set: t9.c9 -> concern,"",choice,disposition:concern\n'
        'next_step: "..."\n'
    )
    out = _seed(tmp_path, toon)
    applied, rejected = apply(out)
    assert applied == [] and rejected == [("t9.c9", "concern")]
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
    assert "applied: t1.c1 -> verified" in captured.out

    assert main(["show", "--out", str(out)]) == 0
    captured = capsys.readouterr()
    assert "t1.c1: verified" in captured.out
    assert "t1: 1/2 reviewed" in captured.out
    assert "t2: 0/1 reviewed" in captured.out
