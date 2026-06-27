"""Tests for the Analysis Schema Validator (issue #6).

The validator's contract: a complete, well-shaped ``analysis.json`` passes with no
errors, and every missing or mis-typed section produces a clear, located error.
These tables pin both halves — a known-good document, then a battery of single
mutations each expected to trip exactly the rule that owns it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from branch_review.analysis import (
    RISK_CATEGORIES,
    SCHEMA,
    validate_analysis,
)

Mutator = Callable[[dict[str, Any]], None]


def _valid() -> dict[str, Any]:
    """A complete, structurally valid analysis covering every section."""
    return {
        "schema": SCHEMA,
        "title": "Add per-file diff fragments",
        "intent_summary": "Splits the diff into per-file escaped fragments for the walkthrough.",
        "review_route": [
            {"path": "src/branch_review/escape.py", "reason": "Core of the new substrate."},
            {"path": "src/branch_review/collect.py", "reason": "Wires fragments into collection."},
        ],
        "behavior_changes": [
            {
                "summary": "Collector now writes per-file fragments.",
                "detail": "One escaped fragment per changed file plus an index.",
                "paths": ["src/branch_review/collect.py"],
            }
        ],
        "risk_map": [
            {
                "category": "security",
                "level": "medium",
                "reason": "Untrusted paths become filenames.",
                "challenge_questions": ["Can a path escape the fragments dir?"],
            },
            {
                "category": "correctness",
                "level": "low",
                "reason": "Ordering must match changed-files.",
                "challenge_questions": ["Is the index order stable?", "Are renames represented?"],
            },
        ],
        "file_walkthrough": [
            {"path": "src/branch_review/escape.py", "explanation": "Adds id + index helpers."},
        ],
        "suspicious_omissions": [
            {"summary": "No cap on huge diffs yet.", "kind": "other", "detail": "Deferred to #7."},
        ],
        "test_checklist": {
            "runner": "pytest",
            "runner_evidence": "pyproject.toml",
            "command": "pytest",
            "items": ["Run the per-file fragment table tests."],
        },
        "diagrams": [
            {"title": "Fragment flow", "kind": "mermaid", "source": "graph TD; A-->B"},
        ],
    }


def test_complete_analysis_passes() -> None:
    assert validate_analysis(_valid()) == []


def test_empty_list_sections_are_allowed() -> None:
    # An empty diff has no route/risks; the validator checks shape, not completeness.
    doc = _valid()
    for section in (
        "review_route",
        "behavior_changes",
        "risk_map",
        "file_walkthrough",
        "suspicious_omissions",
        "diagrams",
    ):
        doc[section] = []
    doc["test_checklist"]["items"] = []
    assert validate_analysis(doc) == []


def test_non_object_is_rejected() -> None:
    errors = validate_analysis([1, 2, 3])
    assert errors and errors[0].location == "$"


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


_BAD_CASES = [
    ("missing-title", _drop("title"), "title"),
    ("empty-title", _set(["title"], "   "), "title"),
    ("missing-intent", _drop("intent_summary"), "intent_summary"),
    ("missing-risk-map", _drop("risk_map"), "risk_map"),
    ("missing-test-checklist", _drop("test_checklist"), "test_checklist"),
    ("bad-schema", _set(["schema"], "something-else"), "schema"),
    # An unknown future revision must fail — this validator encodes only 0.1.
    ("unsupported-schema-version", _set(["schema"], "review-analysis/0.2"), "schema"),
    ("route-not-list", _set(["review_route"], {}), "review_route"),
    ("route-missing-path", _set(["review_route", 0, "path"], None), "review_route[0].path"),
    ("route-step-not-object", _set(["review_route"], ["nope"]), "review_route[0]"),
    ("risk-bad-category", _set(["risk_map", 0, "category"], "ux"), "risk_map[0].category"),
    ("risk-bad-level", _set(["risk_map", 0, "level"], "critical"), "risk_map[0].level"),
    ("risk-missing-reason", _set(["risk_map", 0, "reason"], ""), "risk_map[0].reason"),
    (
        "risk-no-questions",
        _set(["risk_map", 0, "challenge_questions"], []),
        "risk_map[0].challenge_questions",
    ),
    (
        "risk-question-not-str",
        _set(["risk_map", 0, "challenge_questions"], [1]),
        "risk_map[0].challenge_questions[0]",
    ),
    (
        "walkthrough-missing-explanation",
        _set(["file_walkthrough", 0, "explanation"], None),
        "file_walkthrough[0].explanation",
    ),
    (
        "omission-bad-kind",
        _set(["suspicious_omissions", 0, "kind"], "whoops"),
        "suspicious_omissions[0].kind",
    ),
    ("checklist-not-object", _set(["test_checklist"], []), "test_checklist"),
    (
        "checklist-items-not-list",
        _set(["test_checklist", "items"], "run them"),
        "test_checklist.items",
    ),
    ("checklist-runner-wrong-type", _set(["test_checklist", "runner"], 5), "test_checklist.runner"),
    ("diagram-missing-source", _set(["diagrams", 0, "source"], ""), "diagrams[0].source"),
    (
        "behavior-summary-missing",
        _set(["behavior_changes", 0, "summary"], None),
        "behavior_changes[0].summary",
    ),
]


@pytest.mark.parametrize(("label", "mutate", "location"), _BAD_CASES, ids=lambda c: c)
def test_malformed_section_produces_located_error(
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
    good = doc["risk_map"][0]
    bad = {"category": "security", "level": "high", "reason": "", "challenge_questions": ["q"]}
    doc["risk_map"] = ["not-an-object", good, bad]  # bad is at index 2
    locations = [e.location for e in validate_analysis(doc)]
    assert "risk_map[0]" in locations  # the non-object, at its real index
    assert "risk_map[2].reason" in locations  # the empty reason, located at 2 — not 1
    assert "risk_map[1].reason" not in locations  # the valid middle entry is untouched


def test_risk_categories_are_the_canonical_seven() -> None:
    # The cockpit and SKILL share this vocabulary; pin it so a drift is caught.
    assert set(RISK_CATEGORIES) == {
        "correctness",
        "compatibility",
        "concurrency",
        "security",
        "performance",
        "maintainability",
        "test_coverage",
    }


def test_valid_fixture_is_json_round_trippable() -> None:
    # The agent writes this as JSON; ensure the shape we validate is JSON-clean.
    doc = _valid()
    assert validate_analysis(json.loads(json.dumps(doc))) == []


def test_skill_ships_an_example_analysis_that_validates(tmp_path: Path) -> None:
    # The bundled example the SKILL points the agent at must itself pass the gate.
    example = (
        Path(__file__).resolve().parents[1]
        / ".claude/skills/branch-review-cockpit/reference/analysis.example.json"
    )
    if not example.is_file():
        pytest.skip("example analysis not bundled")
    doc = json.loads(example.read_text(encoding="utf-8"))
    assert validate_analysis(doc) == [], "bundled example analysis must validate"
