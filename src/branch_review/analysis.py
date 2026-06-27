"""The Analysis Schema Validator — a deep module guarding ``analysis.json`` (issue #6).

``analysis.json`` is the agent's structured intermediate reasoning about the diff
(CONTEXT: *Analysis*): the substrate the Review Cockpit is authored from and the
substrate the feedback loop answers from (ADR-0001). The cockpit's correctness
depends on that substrate being well-formed — a Risk Map entry missing its
``level``, a route step with no ``path``, a ``category`` outside the canonical set
would all surface as a broken or misleading section. This module is the single
deterministic gate the agent runs *before* authoring the HTML: it proves the
analysis has every required section, each shaped correctly, with risk categories
and levels drawn from the fixed vocabularies (CONTEXT: *Risk Map*).

Deep module (a simple surface over fussy internals): the only entry point is
:func:`validate_analysis`, which returns a list of :class:`AnalysisError` — empty
means the file is structurally sound. It mirrors the Cockpit Linter
(:mod:`branch_review.lint`): a tripwire that *refuses* a malformed analysis, never
one that edits it. It validates **structure, types, and vocabulary**, not editorial
quality — whether a risk reason is *insightful* is the agent's job; whether the
section is *shaped right* is this module's.

The canonical vocabularies (:data:`RISK_CATEGORIES`, :data:`RISK_LEVELS`,
:data:`OMISSION_KINDS`) live here as the single source of truth the SKILL guidance
and the cockpit share. See ``CONTEXT.md`` and ``DESIGN.md`` ("Cockpit sections").
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

# The schema tag a valid analysis must carry; bump the suffix on a breaking change.
SCHEMA = "review-analysis/0.1"

# The Risk Map's fixed categories (CONTEXT: *Risk Map*; DESIGN "Cockpit sections").
# A closed set so the cockpit can group deterministically and the agent can't coin
# ad-hoc categories that fragment the map. Language-specific concerns ride *inside*
# these via the Language Lens (e.g. C++ lifetime → correctness/security), not as new
# top-level categories.
RISK_CATEGORIES = (
    "correctness",
    "compatibility",
    "concurrency",
    "security",
    "performance",
    "maintainability",
    "test_coverage",
)

# Severity levels a Risk Map entry may carry, low→high.
RISK_LEVELS = ("low", "medium", "high")

# What a Suspicious Omission is adjacent to (CONTEXT: *Suspicious Omission*) — the
# untouched thing the diff arguably should have changed. ``other`` is the escape
# hatch so the vocabulary never forces a miscategorisation.
OMISSION_KINDS = ("tests", "callers", "docs", "config", "error_handling", "other")


@dataclass(frozen=True)
class AnalysisError:
    """One reason ``analysis.json`` is malformed: a JSON-ish ``location`` and why."""

    location: str
    message: str

    def __str__(self) -> str:
        return f"{self.location}: {self.message}"


# --- Small structural-check helpers ------------------------------------------
#
# Each returns the errors it found (empty == ok) and is composed by the section
# validators below. Keeping them tiny and uniform is what lets the section rules
# read as a declarative description of the schema rather than nested type-checks.


def _require_str(value: object, location: str, *, allow_empty: bool = False) -> list[AnalysisError]:
    """``value`` must be a string (non-empty unless ``allow_empty``)."""
    if not isinstance(value, str):
        return [AnalysisError(location, f"expected a string, got {_typename(value)}")]
    if not allow_empty and not value.strip():
        return [AnalysisError(location, "must not be empty")]
    return []


def _require_str_list(value: object, location: str, *, min_len: int = 0) -> list[AnalysisError]:
    """``value`` must be a list of non-empty strings with at least ``min_len`` items."""
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return [AnalysisError(location, f"expected a list, got {_typename(value)}")]
    items = list(value)
    if len(items) < min_len:
        return [AnalysisError(location, f"expected at least {min_len} item(s), got {len(items)}")]
    errors: list[AnalysisError] = []
    for i, item in enumerate(items):
        errors.extend(_require_str(item, f"{location}[{i}]"))
    return errors


def _require_enum(value: object, location: str, allowed: Sequence[str]) -> list[AnalysisError]:
    """``value`` must be one of ``allowed`` (a closed vocabulary)."""
    if value not in allowed:
        shown = value if isinstance(value, str) else _typename(value)
        return [AnalysisError(location, f"must be one of {list(allowed)}, got {shown!r}")]
    return []


def _typename(value: object) -> str:
    """A friendly type name for messages (``null`` rather than ``NoneType``)."""
    return "null" if value is None else type(value).__name__


def _as_objects(
    value: object, location: str
) -> tuple[list[tuple[int, Mapping[str, object]]], list[AnalysisError]]:
    """Coerce ``value`` to ``(original_index, object)`` pairs, reporting bad shapes.

    The index is the position in the *original* list, not the filtered one: a
    non-object entry is reported and skipped, but the surviving objects keep their
    real indices so a later field error still locates correctly (e.g. a bad entry
    at original index 2 reports ``risk_map[2]``, not ``[1]``).
    """
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return [], [AnalysisError(location, f"expected a list, got {_typename(value)}")]
    objects: list[tuple[int, Mapping[str, object]]] = []
    errors: list[AnalysisError] = []
    for i, item in enumerate(value):
        if isinstance(item, Mapping):
            objects.append((i, item))
        else:
            errors.append(
                AnalysisError(f"{location}[{i}]", f"expected an object, got {_typename(item)}")
            )
    return objects, errors


# --- Section validators -------------------------------------------------------


def _validate_review_route(value: object) -> list[AnalysisError]:
    """``review_route``: ordered ``{path, reason}`` steps (CONTEXT: *Review Route*)."""
    objects, errors = _as_objects(value, "review_route")
    for i, step in objects:
        loc = f"review_route[{i}]"
        errors.extend(_require_str(step.get("path"), f"{loc}.path"))
        errors.extend(_require_str(step.get("reason"), f"{loc}.reason"))
    return errors


def _validate_behavior_changes(value: object) -> list[AnalysisError]:
    """``behavior_changes``: ``{summary, detail?, paths?}`` records."""
    objects, errors = _as_objects(value, "behavior_changes")
    for i, change in objects:
        loc = f"behavior_changes[{i}]"
        errors.extend(_require_str(change.get("summary"), f"{loc}.summary"))
        if "detail" in change:
            errors.extend(_require_str(change["detail"], f"{loc}.detail", allow_empty=True))
        if "paths" in change:
            errors.extend(_require_str_list(change["paths"], f"{loc}.paths"))
    return errors


def _validate_risk_map(value: object) -> list[AnalysisError]:
    """``risk_map``: ``{category, level, reason, challenge_questions[]}`` (CONTEXT: *Risk Map*).

    Category and level come from the closed vocabularies; every entry must carry a
    reason and at least one challenge question — that triplet *is* the Risk Map's
    contract.
    """
    objects, errors = _as_objects(value, "risk_map")
    for i, entry in objects:
        loc = f"risk_map[{i}]"
        errors.extend(_require_enum(entry.get("category"), f"{loc}.category", RISK_CATEGORIES))
        errors.extend(_require_enum(entry.get("level"), f"{loc}.level", RISK_LEVELS))
        errors.extend(_require_str(entry.get("reason"), f"{loc}.reason"))
        questions = entry.get("challenge_questions")
        errors.extend(_require_str_list(questions, f"{loc}.challenge_questions", min_len=1))
    return errors


def _validate_file_walkthrough(value: object) -> list[AnalysisError]:
    """``file_walkthrough``: ``{path, explanation}`` per file the route visits."""
    objects, errors = _as_objects(value, "file_walkthrough")
    for i, item in objects:
        loc = f"file_walkthrough[{i}]"
        errors.extend(_require_str(item.get("path"), f"{loc}.path"))
        errors.extend(_require_str(item.get("explanation"), f"{loc}.explanation"))
    return errors


def _validate_suspicious_omissions(value: object) -> list[AnalysisError]:
    """``suspicious_omissions``: ``{summary, kind?, detail?}`` (CONTEXT: *Suspicious Omission*)."""
    objects, errors = _as_objects(value, "suspicious_omissions")
    for i, omission in objects:
        loc = f"suspicious_omissions[{i}]"
        errors.extend(_require_str(omission.get("summary"), f"{loc}.summary"))
        if "kind" in omission:
            errors.extend(_require_enum(omission["kind"], f"{loc}.kind", OMISSION_KINDS))
        if "detail" in omission:
            errors.extend(_require_str(omission["detail"], f"{loc}.detail", allow_empty=True))
    return errors


def _validate_test_checklist(value: object) -> list[AnalysisError]:
    """``test_checklist``: ``{runner, runner_evidence?, command?, items[]}``.

    ``runner``/``command`` are nullable — the detector may find no runner — but
    ``items`` is always a list of suggestions. The runner is *suggested, never
    executed* (DESIGN: "checklist + read-only runner detection, no execution").
    """
    if not isinstance(value, Mapping):
        return [AnalysisError("test_checklist", f"expected an object, got {_typename(value)}")]
    errors: list[AnalysisError] = []
    for key in ("runner", "runner_evidence", "command"):
        if value.get(key) is not None:
            errors.extend(_require_str(value[key], f"test_checklist.{key}"))
    errors.extend(_require_str_list(value.get("items"), "test_checklist.items"))
    return errors


def _validate_diagrams(value: object) -> list[AnalysisError]:
    """``diagrams``: ``{title, kind, source}`` — source captured, rendering deferred."""
    objects, errors = _as_objects(value, "diagrams")
    for i, diagram in objects:
        loc = f"diagrams[{i}]"
        errors.extend(_require_str(diagram.get("title"), f"{loc}.title"))
        errors.extend(_require_str(diagram.get("kind"), f"{loc}.kind"))
        errors.extend(_require_str(diagram.get("source"), f"{loc}.source"))
    return errors


# Each required section → its validator. List sections may be empty (an empty diff
# has no route); the validators check shape, not editorial completeness.
_SECTION_VALIDATORS = {
    "review_route": _validate_review_route,
    "behavior_changes": _validate_behavior_changes,
    "risk_map": _validate_risk_map,
    "file_walkthrough": _validate_file_walkthrough,
    "suspicious_omissions": _validate_suspicious_omissions,
    "test_checklist": _validate_test_checklist,
    "diagrams": _validate_diagrams,
}


def validate_analysis(obj: object) -> list[AnalysisError]:
    """Validate a parsed ``analysis.json``; return every problem (empty == valid).

    Checks the top-level object carries the schema tag, a non-empty ``title`` and
    ``intent_summary`` (the Executive Summary's source), and every required
    section, each structurally sound with risk categories/levels and omission
    kinds drawn from the canonical vocabularies. Returns a flat list of
    :class:`AnalysisError` with ``location`` paths (e.g. ``risk_map[2].level``) so a
    malformed file points the agent straight at what to fix.
    """
    if not isinstance(obj, Mapping):
        return [AnalysisError("$", f"analysis must be a JSON object, got {_typename(obj)}")]

    errors: list[AnalysisError] = []

    # Pin to the exact supported revision: this validator encodes the 0.1 shape, so
    # an unknown future tag (e.g. review-analysis/0.2) must fail rather than be
    # validated against rules it may no longer match. Bump SCHEMA when the shape changes.
    schema = obj.get("schema")
    if schema != SCHEMA:
        errors.append(AnalysisError("schema", f"must be {SCHEMA!r}, got {schema!r}"))

    errors.extend(_require_str(obj.get("title"), "title"))
    errors.extend(_require_str(obj.get("intent_summary"), "intent_summary"))

    for name, validator in _SECTION_VALIDATORS.items():
        if name not in obj:
            errors.append(AnalysisError(name, "required section is missing"))
            continue
        errors.extend(validator(obj[name]))

    return errors


def main(argv: list[str] | None = None) -> int:
    """CLI: validate an ``analysis.json`` file; exit non-zero on any problem.

    The skill runs this after the agent authors ``analysis.json`` and before it
    authors the cockpit — a malformed analysis is fixed first, never rendered.
    """
    parser = argparse.ArgumentParser(
        prog="validate_analysis",
        description="Validate analysis.json against the Review Analysis schema.",
    )
    parser.add_argument("path", type=Path, help="Path to analysis.json.")
    args = parser.parse_args(argv)

    try:
        obj = json.loads(args.path.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"error: cannot read {args.path}: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"error: {args.path} is not valid JSON: {exc}", file=sys.stderr)
        return 2

    errors = validate_analysis(obj)
    if errors:
        for error in errors:
            print(f"analysis: {error}", file=sys.stderr)
        print(
            f"Analysis validation FAILED: {len(errors)} problem(s) in {args.path}",
            file=sys.stderr,
        )
        return 1

    print(f"Analysis OK: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
