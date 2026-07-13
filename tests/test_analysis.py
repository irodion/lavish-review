"""Tests for the Analysis Schema Validator (issues #6, #39, #84).

The validator's contract: a complete, well-shaped ``review-analysis/0.4`` document
passes with no errors, and every missing or mis-typed piece produces a clear,
located error. These tables pin both halves — a known-good step-centric document
(threads > steps > evidence, ADR-0016), then a battery of single mutations each
expected to trip exactly the rule that owns it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from branch_review.analysis import (
    CONFIDENCE_LEVELS,
    IMPACTS,
    PROMPT_REQUIRED_IMPACTS,
    SCHEMA,
    step_ids,
    validate_analysis,
)

Mutator = Callable[[dict[str, Any]], None]


def _valid() -> dict[str, Any]:
    """A complete, structurally valid step-centric analysis exercising every impact."""
    return {
        "schema": SCHEMA,
        "title": "Retry backoff becomes exponential; config key renamed",
        "intent_summary": "Replaces the fixed retry delay with capped exponential backoff.",
        "widened_into": ["src/client/pool.py"],
        "alignment": {
            "serves_goal": ["t1", "t3"],
            "drive_by": ["t2", "t4"],
        },
        "threads": [
            {
                "id": "t1",
                "title": "Exponential backoff",
                "summary": "The retry loop's delay policy changes.",
                "paths": ["src/retry.py"],
                "steps": [
                    {
                        "id": "t1.s1",
                        "impact": "behavior-change",
                        "summary": "Retries now back off exponentially, capped at 60s.",
                        "detail": "Delay doubles per attempt from the base.",
                        "confidence": "high",
                        "why_now": "Start here — the observable heart of the branch.",
                        "review_prompts": ["Is the cap applied after the doubling?"],
                        "evidence": [{"path": "src/retry.py", "hunk": 1}],
                        "attention_notes": [
                            {
                                "text": "No test in the diff exercises the new timing.",
                                "evidence": [{"note": "tests/test_retry.py untouched"}],
                            },
                            {"text": "The goal asked for jitter; no thread adds it."},
                        ],
                    },
                    {
                        "id": "t1.s2",
                        "impact": "unknown-impact",
                        "summary": "Whether the cap bites depends on the caller's timeout.",
                        "confidence": "medium",
                        "why_now": "Read next: the caller's timeout decides if the cap matters.",
                        "review_prompts": ["Is the pool's request timeout under 60s?"],
                        "evidence": [{"note": "widened: src/client/pool.py sets the timeout"}],
                    },
                ],
            },
            {
                "id": "t2",
                "title": "Config key rename (drive-by)",
                "summary": "RETRY_DELAY becomes RETRY_BASE_DELAY.",
                "paths": ["src/config.py"],
                "steps": [
                    {
                        "id": "t2.s1",
                        "impact": "behavior-change",
                        "summary": "The old key is no longer read; overrides silently lost.",
                        "confidence": "high",
                        "why_now": "Where the change reaches operators — the compatibility break.",
                        "review_prompts": ["Does any deployment still set RETRY_DELAY?"],
                        "evidence": [{"path": "src/config.py", "hunk": 1}],
                    },
                ],
            },
            {
                "id": "t3",
                "title": "Extract the delay helper",
                "summary": "The delay math moves into compute_delay().",
                "paths": ["src/retry.py"],
                "steps": [
                    {
                        "id": "t3.s1",
                        "impact": "behavior-preserving",
                        "summary": "The doubling-and-cap arithmetic is lifted verbatim.",
                        "confidence": "high",
                        "why_now": "Same math relocated — confirm it to trust t1.s1.",
                        "review_prompts": ["Does compute_delay() keep cap-after-doubling order?"],
                        "evidence": [{"path": "src/retry.py", "hunk": 2}],
                    },
                ],
            },
            {
                "id": "t4",
                "title": "Test and lockfile (drive-by)",
                "summary": "A timing test and a regenerated lockfile ride along.",
                "paths": ["tests/test_retry.py", "poetry.lock"],
                "steps": [
                    {
                        "id": "t4.s1",
                        "impact": "test-change",
                        "summary": "A new test asserts the 1,2,4,8 sequence and the 60s cap.",
                        "confidence": "high",
                        "why_now": "The evidence the new behavior is pinned.",
                        "review_prompts": ["Does it fail if the cap is removed?"],
                        "relates_to": ["t1.s1"],
                        "evidence": [{"path": "tests/test_retry.py", "hunk": 1}],
                    },
                    {
                        "id": "t4.s2",
                        "impact": "mechanical-change",
                        "summary": "poetry.lock regenerated with no version changes.",
                        "confidence": "high",
                        "why_now": "Skim last or skip — nothing moved.",
                        "evidence": [{"note": "poetry.lock body omitted as lockfile churn"}],
                    },
                ],
            },
        ],
        "test_runner": {
            "runner": "pytest",
            "runner_evidence": "pyproject.toml",
            "command": "pytest",
        },
        "diagrams": [
            {"title": "Backoff curve", "kind": "mermaid", "source": "graph TD; A-->B"},
        ],
    }


def test_complete_analysis_passes() -> None:
    assert validate_analysis(_valid()) == []


def test_reference_example_analysis_is_valid() -> None:
    # The shipped reference the narrator is told to mirror must itself validate under
    # the current schema — otherwise the example teaches a shape the validator rejects.
    import json
    from pathlib import Path

    skill = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "branch-review-cockpit"
    example = skill / "reference" / "analysis.example.json"
    doc = json.loads(example.read_text(encoding="utf-8"))
    assert doc["schema"] == SCHEMA  # the example carries the current tag
    assert validate_analysis(doc) == []


def test_empty_optional_lists_are_allowed() -> None:
    # Nothing widened into, no diagrams, no runner found, a thread with no direct
    # file coverage — all honestly-empty shapes the validator must not force.
    doc = _valid()
    doc["widened_into"] = []
    doc["diagrams"] = []
    doc["test_runner"] = {"runner": None, "runner_evidence": None, "command": None}
    doc["threads"][1]["paths"] = []
    assert validate_analysis(doc) == []


def test_optional_step_fields_may_be_absent() -> None:
    # detail, review_prompts (on test/mechanical), relates_to, and attention_notes
    # are all optional — a step carrying none of them still validates.
    doc = _valid()
    del doc["threads"][0]["steps"][0]["detail"]
    del doc["threads"][0]["steps"][0]["attention_notes"]
    del doc["threads"][3]["steps"][0]["relates_to"]
    assert validate_analysis(doc) == []


def test_non_object_is_rejected() -> None:
    errors = validate_analysis([1, 2, 3])
    assert errors and errors[0].location == "$"


# --- step_ids (the set the Cockpit Linter's structural pass checks, issue #62) ---


def test_step_ids_returns_every_id_in_document_order() -> None:
    assert step_ids(_valid()) == ["t1.s1", "t1.s2", "t2.s1", "t3.s1", "t4.s1", "t4.s2"]


def test_step_ids_tolerates_malformed_shapes() -> None:
    # Not a raise: a rough or partial analysis still yields whatever ids are present.
    assert step_ids("not a mapping") == []
    assert step_ids({}) == []
    assert step_ids({"threads": "nope"}) == []
    mixed = {"threads": [{"steps": [{"id": 7}, {"summary": "no id"}, {"id": "t1.s1"}]}]}
    assert step_ids(mixed) == ["t1.s1"]


def test_step_ids_ignores_the_retired_claims_key() -> None:
    # ADR-0016's clean break: the sole id walk reads the ``steps`` key. An old-schema
    # analysis carrying the retired ``claims`` key yields no step ids — its consumers
    # (lint, dispositions, evidence) see nothing to key on, and the validator refuses
    # the document upstream anyway.
    legacy = {"threads": [{"claims": [{"id": "t1.c1"}, {"id": "t1.c2"}]}]}
    assert step_ids(legacy) == []


def test_null_alignment_is_valid() -> None:
    # ADR-0010's degraded mode: no stated goal was found, so there is nothing to
    # measure the threads against — alignment is an explicit null, never absent.
    doc = _valid()
    doc["alignment"] = None
    assert validate_analysis(doc) == []


def test_relates_to_may_point_forward_and_across_threads() -> None:
    # A link may target a step in a later thread; the id set is complete before the
    # integrity check runs. t2.s1 -> t4.s1 is a forward, cross-thread link.
    doc = _valid()
    doc["threads"][1]["steps"][0]["relates_to"] = ["t4.s1"]
    assert validate_analysis(doc) == []


def test_prompts_optional_on_test_and_mechanical_steps() -> None:
    # A mechanical step needs no comparison; a test step's prompt is optional. Empty
    # or absent prompts on these impacts must not be forced.
    doc = _valid()
    doc["threads"][3]["steps"][0]["review_prompts"] = []  # test-change: allowed empty
    del doc["threads"][3]["steps"][1]  # drop the mechanical step's neighbor untouched
    assert validate_analysis(doc) == []


def test_hunk_anchored_evidence_is_optional_and_passes() -> None:
    # A {path} ref may carry a 1-based hunk index, or omit it entirely — a path-only
    # ref keeps file-level anchoring. Both shapes are valid.
    doc = _valid()
    doc["threads"][0]["steps"][0]["evidence"] = [{"path": "src/retry.py", "hunk": 1}]
    assert validate_analysis(doc) == []
    doc["threads"][0]["steps"][0]["evidence"] = [{"path": "src/retry.py"}]  # no hunk
    assert validate_analysis(doc) == []


# (label, mutate(doc), expected location substring) — each trips exactly one rule.
def _drop(key: str) -> Mutator:
    def mutate(doc: dict[str, Any]) -> None:
        del doc[key]

    return mutate


def _set(path: list[Any], value: Any) -> Mutator:
    def mutate(doc: dict[str, Any]) -> None:
        target: Any = doc
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return mutate


def _del(path: list[Any]) -> Mutator:
    def mutate(doc: dict[str, Any]) -> None:
        target: Any = doc
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]

    return mutate


_S = ["threads", 0, "steps"]  # t1's steps: [0]=behavior-change, [1]=unknown-impact
_PRESERVE = ["threads", 2, "steps", 0]  # t3.s1: behavior-preserving
_TEST = ["threads", 3, "steps", 0]  # t4.s1: test-change (carries relates_to)
_MECH = ["threads", 3, "steps", 1]  # t4.s2: mechanical-change


_BAD_CASES = [
    ("missing-title", _drop("title"), "title"),
    ("empty-title", _set(["title"], "   "), "title"),
    ("missing-intent", _drop("intent_summary"), "intent_summary"),
    ("bad-schema", _set(["schema"], "something-else"), "schema"),
    # The clean break: a 0.3 (or older) document is refused with a located error, not
    # revalidated under 0.4 rules. No dual-schema path.
    ("outdated-schema-0.3", _set(["schema"], "review-analysis/0.3"), "schema"),
    ("outdated-schema-0.2", _set(["schema"], "review-analysis/0.2"), "schema"),
    # ADR-0011: widened_into is required (an honest "nothing" is [], not absence).
    ("missing-widened-into", _drop("widened_into"), "widened_into"),
    ("widened-into-not-list", _set(["widened_into"], "src/retry.py"), "widened_into"),
    # Threads: the L1 backbone.
    ("missing-threads", _drop("threads"), "threads"),
    ("threads-empty", _set(["threads"], []), "threads"),
    ("threads-not-list", _set(["threads"], {}), "threads"),
    ("thread-not-object", _set(["threads"], ["nope"]), "threads[0]"),
    ("thread-bad-id", _set(["threads", 0, "id"], "thread-one"), "threads[0].id"),
    ("thread-duplicate-id", _set(["threads", 1, "id"], "t1"), "threads[1].id"),
    ("thread-missing-title", _set(["threads", 0, "title"], ""), "threads[0].title"),
    ("thread-missing-summary", _del(["threads", 0, "summary"]), "threads[0].summary"),
    ("thread-paths-not-list", _set(["threads", 0, "paths"], "src"), "threads[0].paths"),
    ("thread-no-steps", _set(["threads", 0, "steps"], []), "threads[0].steps"),
    # A thread's impact is derived, never authored (ADR-0016).
    (
        "thread-authored-impact",
        _set(["threads", 0, "impact"], "behavior-change"),
        "threads[0].impact",
    ),
    # Steps: the L2 contract (ADR-0009/0012/0016).
    ("step-not-object", _set([*_S, 0], "nope"), "threads[0].steps[0]"),
    ("step-bad-id-prefix", _set([*_S, 0, "id"], "t9.s1"), "threads[0].steps[0].id"),
    ("step-bad-id-shape", _set([*_S, 0, "id"], "t1-s1"), "threads[0].steps[0].id"),
    ("step-bad-id-claim-suffix", _set([*_S, 0, "id"], "t1.c1"), "threads[0].steps[0].id"),
    ("step-duplicate-id", _set([*_S, 1, "id"], "t1.s1"), "threads[0].steps[1].id"),
    ("step-bad-impact", _set([*_S, 0, "impact"], "opinion"), "threads[0].steps[0].impact"),
    ("step-missing-impact", _del([*_S, 0, "impact"]), "threads[0].steps[0].impact"),
    ("step-missing-summary", _set([*_S, 0, "summary"], ""), "threads[0].steps[0].summary"),
    ("step-missing-why-now", _del([*_S, 0, "why_now"]), "threads[0].steps[0].why_now"),
    ("step-empty-why-now", _set([*_S, 0, "why_now"], "  "), "threads[0].steps[0].why_now"),
    ("step-missing-confidence", _del([*_S, 0, "confidence"]), "threads[0].steps[0].confidence"),
    (
        "step-bad-confidence",
        _set([*_S, 0, "confidence"], "certain"),
        "threads[0].steps[0].confidence",
    ),
    # review_prompts: required where the reviewer has a comparison to make.
    (
        "prompts-missing-on-behavior-change",
        _del([*_S, 0, "review_prompts"]),
        "threads[0].steps[0].review_prompts",
    ),
    (
        "prompts-empty-on-behavior-change",
        _set([*_S, 0, "review_prompts"], []),
        "threads[0].steps[0].review_prompts",
    ),
    (
        "prompts-missing-on-unknown-impact",
        _del([*_S, 1, "review_prompts"]),
        "threads[0].steps[1].review_prompts",
    ),
    (
        "prompts-missing-on-preserving",
        _del([*_PRESERVE, "review_prompts"]),
        "threads[2].steps[0].review_prompts",
    ),
    # Present-but-malformed prompts on an optional impact are still rejected.
    (
        "prompts-bad-type-on-test",
        _set([*_TEST, "review_prompts"], [1]),
        "threads[3].steps[0].review_prompts[0]",
    ),
    # Evidence: a step must be substantiated.
    ("step-no-evidence", _set([*_S, 0, "evidence"], []), "threads[0].steps[0].evidence"),
    ("evidence-empty-ref", _set([*_S, 0, "evidence"], [{}]), "threads[0].steps[0].evidence[0]"),
    (
        "evidence-bad-path",
        _set([*_S, 0, "evidence"], [{"path": 5}]),
        "threads[0].steps[0].evidence[0].path",
    ),
    # A behavior-preserving step with no evidence is rejected — the preservation
    # claim needs the code whose equivalence is asserted (ADR-0016).
    ("preserving-no-evidence", _set([*_PRESERVE, "evidence"], []), "threads[2].steps[0].evidence"),
    # Hunk-anchored evidence (ADR-0014): optional, path-only, 1-based int.
    (
        "evidence-hunk-on-note-ref",
        _set([*_S, 1, "evidence", 0, "hunk"], 1),
        "threads[0].steps[1].evidence[0].hunk",
    ),
    (
        "evidence-hunk-not-integer",
        _set([*_S, 0, "evidence", 0, "hunk"], "2"),
        "threads[0].steps[0].evidence[0].hunk",
    ),
    (
        "evidence-hunk-out-of-range",
        _set([*_S, 0, "evidence", 0, "hunk"], 0),
        "threads[0].steps[0].evidence[0].hunk",
    ),
    (
        "evidence-hunk-boolean",
        _set([*_S, 0, "evidence", 0, "hunk"], True),
        "threads[0].steps[0].evidence[0].hunk",
    ),
    # relates_to: id integrity across the whole document.
    (
        "relates-to-not-list",
        _set([*_TEST, "relates_to"], "t1.s1"),
        "threads[3].steps[0].relates_to",
    ),
    (
        "relates-to-dangling",
        _set([*_TEST, "relates_to"], ["t9.s9"]),
        "threads[3].steps[0].relates_to[0]",
    ),
    (
        "relates-to-self",
        _set([*_TEST, "relates_to"], ["t4.s1"]),
        "threads[3].steps[0].relates_to[0]",
    ),
    # Attention notes: muted asides only — no hunting attributes (ADR-0016).
    (
        "note-missing-text",
        _del([*_S, 0, "attention_notes", 0, "text"]),
        "threads[0].steps[0].attention_notes[0].text",
    ),
    (
        "note-forbidden-severity",
        _set([*_S, 0, "attention_notes", 0, "severity"], "high"),
        "threads[0].steps[0].attention_notes[0].severity",
    ),
    (
        "note-forbidden-category",
        _set([*_S, 0, "attention_notes", 0, "category"], "security"),
        "threads[0].steps[0].attention_notes[0].category",
    ),
    (
        "note-forbidden-level",
        _set([*_S, 0, "attention_notes", 0, "level"], "high"),
        "threads[0].steps[0].attention_notes[0].level",
    ),
    (
        "note-bad-evidence",
        _set([*_S, 0, "attention_notes", 0, "evidence"], [{}]),
        "threads[0].steps[0].attention_notes[0].evidence[0]",
    ),
    # Alignment: the goal↔implementation partition (ADR-0010).
    ("missing-alignment", _drop("alignment"), "alignment"),
    ("alignment-not-object", _set(["alignment"], "aligned"), "alignment"),
    ("alignment-missing-serves", _del(["alignment", "serves_goal"]), "alignment.serves_goal"),
    ("alignment-missing-drive-by", _del(["alignment", "drive_by"]), "alignment.drive_by"),
    ("alignment-not-str-list", _set(["alignment", "serves_goal"], [1]), "alignment.serves_goal[0]"),
    (
        "alignment-unknown-thread",
        _set(["alignment", "serves_goal"], ["t1", "t9"]),
        "alignment.serves_goal[1]",
    ),
    (
        "alignment-thread-listed-twice",
        _set(["alignment", "drive_by"], ["t2", "t4", "t1"]),
        "alignment.drive_by[2]",
    ),
    ("alignment-uncovered-thread", _set(["alignment", "drive_by"], ["t2"]), "alignment"),
    # Runner block + diagrams.
    ("missing-test-runner", _drop("test_runner"), "test_runner"),
    ("test-runner-not-object", _set(["test_runner"], []), "test_runner"),
    ("test-runner-wrong-type", _set(["test_runner", "runner"], 5), "test_runner.runner"),
    ("missing-diagrams", _drop("diagrams"), "diagrams"),
    ("diagram-missing-source", _set(["diagrams", 0, "source"], ""), "diagrams[0].source"),
]


@pytest.mark.parametrize(("label", "mutate", "location"), _BAD_CASES, ids=lambda c: c)
def test_malformed_analysis_produces_located_error(
    label: str, mutate: Mutator, location: str
) -> None:
    doc = _valid()
    mutate(doc)
    errors = validate_analysis(doc)
    locations = [e.location for e in errors]
    assert any(loc == location for loc in locations), f"{label}: {location!r} not in {locations}"


def test_non_object_entry_does_not_shift_later_error_index() -> None:
    # A non-object earlier in a list is reported at its real index AND must not
    # renumber a genuinely-bad object after it (the validator filters non-objects
    # but preserves original positions).
    doc = _valid()
    steps = doc["threads"][0]["steps"]
    bad = dict(steps[1])
    bad["summary"] = ""
    bad["id"] = "t1.s3"
    doc["threads"][0]["steps"] = ["not-an-object", steps[1], bad]  # bad is at index 2
    locations = [e.location for e in validate_analysis(doc)]
    assert "threads[0].steps[0]" in locations  # the non-object, at its real index
    assert "threads[0].steps[2].summary" in locations  # located at 2 — not 1
    assert "threads[0].steps[1].summary" not in locations  # the valid middle entry


def test_vocabularies_are_canonical() -> None:
    # The cockpit and SKILL share these vocabularies; pin them so a drift is caught.
    assert set(IMPACTS) == {
        "behavior-change",
        "behavior-preserving",
        "test-change",
        "mechanical-change",
        "unknown-impact",
    }
    assert set(CONFIDENCE_LEVELS) == {"high", "medium", "low"}
    # Prompts are required exactly where the reviewer has a comparison to make.
    assert {"behavior-change", "behavior-preserving", "unknown-impact"} == PROMPT_REQUIRED_IMPACTS
