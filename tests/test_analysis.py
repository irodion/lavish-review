"""Tests for the Analysis Schema Validator (issues #6, #39).

The validator's contract: a complete, well-shaped ``review-analysis/0.2`` document
passes with no errors, and every missing or mis-typed piece produces a clear,
located error. These tables pin both halves — a known-good claim-centric document
(threads > claims > evidence, ADR-0009), then a battery of single mutations each
expected to trip exactly the rule that owns it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from branch_review.analysis import (
    CLAIM_KINDS,
    CONFIDENCE_LEVELS,
    OMISSION_KINDS,
    RISK_CATEGORIES,
    SCHEMA,
    validate_analysis,
)

Mutator = Callable[[dict[str, Any]], None]


def _valid() -> dict[str, Any]:
    """A complete, structurally valid claim-centric analysis."""
    return {
        "schema": SCHEMA,
        "title": "Retry backoff becomes exponential",
        "intent_summary": "Replaces the fixed retry delay with capped exponential backoff.",
        "widened_into": ["src/retry.py"],
        "alignment": {
            "serves_goal": ["t1"],
            "drive_by": ["t2"],
        },
        "threads": [
            {
                "id": "t1",
                "title": "Exponential backoff",
                "summary": "The retry loop's delay policy changes.",
                "paths": ["src/retry.py", "src/config.py"],
                "claims": [
                    {
                        "id": "t1.c1",
                        "kind": "behavior",
                        "summary": "Retries now back off exponentially, capped at 60s.",
                        "detail": "Delay doubles per attempt from 1s.",
                        "confidence": "high",
                        "challenge_questions": ["What bounds the first delay?"],
                        "evidence": [{"path": "src/retry.py"}],
                    },
                    {
                        "id": "t1.c2",
                        "kind": "risk",
                        "category": "correctness",
                        "level": "medium",
                        "summary": "No jitter — synchronized clients retry in lockstep.",
                        "confidence": "medium",
                        "challenge_questions": ["Do concurrent callers share the schedule?"],
                        "evidence": [
                            {"path": "src/retry.py", "note": "the backoff computation"},
                        ],
                    },
                    {
                        "id": "t1.c3",
                        "kind": "verify",
                        "summary": "Run the retry timing tests.",
                        "confidence": "high",
                        "challenge_questions": ["Does any test pin the cap?"],
                        "evidence": [{"note": "tests/test_retry.py exists but was not changed"}],
                    },
                ],
            },
            {
                "id": "t2",
                "title": "Drive-by config rename",
                "summary": "RETRY_DELAY becomes RETRY_BASE_DELAY.",
                "paths": ["src/config.py"],
                "claims": [
                    {
                        "id": "t2.c1",
                        "kind": "omission",
                        "omission_kind": "docs",
                        "summary": "README still documents RETRY_DELAY.",
                        "confidence": "high",
                        "challenge_questions": ["Is the old name used anywhere else?"],
                        "evidence": [{"note": "README.md untouched by the diff"}],
                    }
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


def test_empty_optional_lists_are_allowed() -> None:
    # Nothing widened into, no diagrams, no runner found, a thread with no direct
    # file coverage — all honestly-empty shapes the validator must not force.
    doc = _valid()
    doc["widened_into"] = []
    doc["diagrams"] = []
    doc["test_runner"] = {"runner": None, "runner_evidence": None, "command": None}
    doc["threads"][1]["paths"] = []
    assert validate_analysis(doc) == []


def test_non_object_is_rejected() -> None:
    errors = validate_analysis([1, 2, 3])
    assert errors and errors[0].location == "$"


def test_null_alignment_is_valid() -> None:
    # ADR-0010's degraded mode: no stated goal was found, so there is nothing to
    # measure the threads against — alignment is an explicit null, never absent.
    doc = _valid()
    doc["alignment"] = None
    assert validate_analysis(doc) == []


def test_goal_omission_with_alignment_passes() -> None:
    # A goal-unserved item is a first-class Suspicious Omission (ADR-0010).
    doc = _valid()
    doc["threads"][1]["claims"][0]["omission_kind"] = "goal"
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


_C = ["threads", 0, "claims"]


def _null_alignment_with_goal_omission(doc: dict[str, Any]) -> None:
    """No stated goal, yet a claim says the goal is unserved — a contradiction."""
    doc["alignment"] = None
    doc["threads"][1]["claims"][0]["omission_kind"] = "goal"


_BAD_CASES = [
    ("missing-title", _drop("title"), "title"),
    ("empty-title", _set(["title"], "   "), "title"),
    ("missing-intent", _drop("intent_summary"), "intent_summary"),
    ("bad-schema", _set(["schema"], "something-else"), "schema"),
    # The old revision must fail — this validator encodes only 0.2.
    ("outdated-schema-version", _set(["schema"], "review-analysis/0.1"), "schema"),
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
    ("thread-no-claims", _set(["threads", 0, "claims"], []), "threads[0].claims"),
    # Claims: the L2 contract (ADR-0009/0012).
    ("claim-not-object", _set([*_C, 0], "nope"), "threads[0].claims[0]"),
    ("claim-bad-id-prefix", _set([*_C, 0, "id"], "t9.c1"), "threads[0].claims[0].id"),
    ("claim-bad-id-shape", _set([*_C, 0, "id"], "t1-c1"), "threads[0].claims[0].id"),
    ("claim-duplicate-id", _set([*_C, 1, "id"], "t1.c1"), "threads[0].claims[1].id"),
    ("claim-bad-kind", _set([*_C, 0, "kind"], "opinion"), "threads[0].claims[0].kind"),
    ("claim-missing-summary", _set([*_C, 0, "summary"], ""), "threads[0].claims[0].summary"),
    (
        "claim-missing-confidence",
        _del([*_C, 0, "confidence"]),
        "threads[0].claims[0].confidence",
    ),
    (
        "claim-bad-confidence",
        _set([*_C, 0, "confidence"], "certain"),
        "threads[0].claims[0].confidence",
    ),
    (
        "claim-no-questions",
        _set([*_C, 0, "challenge_questions"], []),
        "threads[0].claims[0].challenge_questions",
    ),
    (
        "claim-question-not-str",
        _set([*_C, 0, "challenge_questions"], [1]),
        "threads[0].claims[0].challenge_questions[0]",
    ),
    ("claim-no-evidence", _set([*_C, 0, "evidence"], []), "threads[0].claims[0].evidence"),
    (
        "claim-evidence-empty-ref",
        _set([*_C, 0, "evidence"], [{}]),
        "threads[0].claims[0].evidence[0]",
    ),
    (
        "claim-evidence-bad-path",
        _set([*_C, 0, "evidence"], [{"path": 5}]),
        "threads[0].claims[0].evidence[0].path",
    ),
    # Risk claims: category + level required; level is risk-only.
    ("risk-missing-category", _del([*_C, 1, "category"]), "threads[0].claims[1].category"),
    ("risk-bad-category", _set([*_C, 1, "category"], "ux"), "threads[0].claims[1].category"),
    ("risk-missing-level", _del([*_C, 1, "level"]), "threads[0].claims[1].level"),
    ("risk-bad-level", _set([*_C, 1, "level"], "critical"), "threads[0].claims[1].level"),
    ("level-on-non-risk", _set([*_C, 0, "level"], "low"), "threads[0].claims[0].level"),
    # Omission claims own omission_kind.
    (
        "omission-bad-kind",
        _set(["threads", 1, "claims", 0, "omission_kind"], "whoops"),
        "threads[1].claims[0].omission_kind",
    ),
    (
        "omission-kind-on-non-omission",
        _set([*_C, 0, "omission_kind"], "docs"),
        "threads[0].claims[0].omission_kind",
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
        # t1 already sits in serves_goal — a thread is in exactly one list.
        "alignment-thread-listed-twice",
        _set(["alignment", "drive_by"], ["t2", "t1"]),
        "alignment.drive_by[1]",
    ),
    (
        # t2 ends up in neither list — the partition must cover every thread.
        "alignment-uncovered-thread",
        _set(["alignment", "drive_by"], []),
        "alignment",
    ),
    (
        "goal-omission-under-null-alignment",
        _null_alignment_with_goal_omission,
        "threads[1].claims[0].omission_kind",
    ),
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
    claims = doc["threads"][0]["claims"]
    bad = dict(claims[2])
    bad["summary"] = ""
    bad["id"] = "t1.c4"
    doc["threads"][0]["claims"] = ["not-an-object", claims[1], bad]  # bad is at index 2
    locations = [e.location for e in validate_analysis(doc)]
    assert "threads[0].claims[0]" in locations  # the non-object, at its real index
    assert "threads[0].claims[2].summary" in locations  # located at 2 — not 1
    assert "threads[0].claims[1].summary" not in locations  # the valid middle entry


def test_vocabularies_are_canonical() -> None:
    # The cockpit and SKILL share these vocabularies; pin them so a drift is caught.
    assert set(RISK_CATEGORIES) == {
        "correctness",
        "compatibility",
        "concurrency",
        "security",
        "performance",
        "maintainability",
        "test_coverage",
    }
    assert set(CLAIM_KINDS) == {"behavior", "risk", "omission", "verify"}
    assert set(CONFIDENCE_LEVELS) == {"high", "medium", "low"}
    assert set(OMISSION_KINDS) == {
        "tests",
        "callers",
        "docs",
        "config",
        "error_handling",
        "goal",
        "other",
    }
