"""The Analysis Schema Validator — a deep module guarding ``analysis.json`` (issues #6, #39).

``analysis.json`` is the agent's structured intermediate reasoning about the diff
(CONTEXT: *Analysis*): the substrate the Review Cockpit is authored from and the
substrate the feedback loop answers from (ADR-0001). Since ADR-0009 the shape is
**claim-centric**: the changeset is decomposed into narrative **Threads** (the
feature, the drive-by refactor, the config churn), each carrying the **Claims** a
reviewer must judge — behavior changes, risks, suspicious omissions, verification
steps — and every claim carries the agent's **confidence**, at least one challenge
question, and **evidence references** into the diff. The cockpit's L1/L2/L3 layers
are authored straight from this structure, so its correctness depends on the
substrate being well-formed: a claim with no evidence, a risk without a level, an
id that can't anchor a disposition (ADR-0012) would all surface as a broken or
misleading layer.

Deep module (a simple surface over fussy internals): the only entry point is
:func:`validate_analysis`, which returns a list of :class:`AnalysisError` — empty
means the file is structurally sound. It mirrors the Cockpit Linter
(:mod:`branch_review.lint`): a tripwire that *refuses* a malformed analysis, never
one that edits it. It validates **structure, types, vocabulary, and id integrity**,
not editorial quality — whether a claim is *insightful* is the agent's job; whether
it is *shaped right* is this module's.

The canonical vocabularies (:data:`RISK_CATEGORIES`, :data:`RISK_LEVELS`,
:data:`OMISSION_KINDS`, :data:`CLAIM_KINDS`, :data:`CONFIDENCE_LEVELS`) live here
as the single source of truth the SKILL guidance and the cockpit share. See
``CONTEXT.md``, ``DESIGN.md``, and ADR-0009/0010/0011/0012.

Since ADR-0010 the analysis also carries ``alignment`` — the goal↔implementation
partition: which threads serve the stated goal and which are drive-bys, with
goal-unserved work expressed as ``omission`` claims of kind ``goal``. ``null``
when no goal was found.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

# The schema tag a valid analysis must carry; bump the suffix on a breaking change.
# 0.2 is the Layered Review shape (ADR-0009): threads > claims > evidence, plus the
# isolated-analysis accountability field ``widened_into`` (ADR-0011).
SCHEMA = "review-analysis/0.2"

# The risk categories a claim may carry (CONTEXT: *Risk Map*). A closed set so the
# cockpit can badge deterministically and the agent can't coin ad-hoc categories.
# Language-specific concerns ride *inside* these via the Language Lens (e.g. C++
# lifetime → correctness/security), not as new top-level categories.
RISK_CATEGORIES = (
    "correctness",
    "compatibility",
    "concurrency",
    "security",
    "performance",
    "maintainability",
    "test_coverage",
)

# Severity levels a risk claim must carry, low→high.
RISK_LEVELS = ("low", "medium", "high")

# What a Suspicious Omission is adjacent to (CONTEXT: *Suspicious Omission*) — the
# untouched thing the diff arguably should have changed. ``goal`` is the
# goal-alignment omission (ADR-0010): the stated goal asked for something no
# thread delivers — it therefore requires a non-null ``alignment``. ``other`` is
# the escape hatch so the vocabulary never forces a miscategorisation.
OMISSION_KINDS = ("tests", "callers", "docs", "config", "error_handling", "goal", "other")

# What kind of assertion a claim makes (ADR-0009's L2 vocabulary):
#   behavior — something observably changes ("retries are now exponential")
#   risk     — something could be wrong (carries category + level + reasons)
#   omission — something the diff did NOT change but arguably should have
#   verify   — something the reviewer should check/run (the old Test Checklist items)
CLAIM_KINDS = ("behavior", "risk", "omission", "verify")

# The agent's stated confidence in a claim (ADR-0012: confidence, never a verdict).
CONFIDENCE_LEVELS = ("high", "medium", "low")

# Id shapes: threads are ``t<N>``; claims are ``<thread-id>.c<N>`` (ADR-0012's
# stable claim ids — the keys dispositions attach to, and the cockpit element ids).
_THREAD_ID = re.compile(r"^t\d+$")
_CLAIM_ID_SUFFIX = re.compile(r"^c\d+$")


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
    at original index 2 reports ``threads[2]``, not ``[1]``).
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


# --- Claim / thread validators (ADR-0009's L1/L2) ------------------------------


def _validate_evidence(value: object, loc: str) -> list[AnalysisError]:
    """``evidence``: ≥1 ``{path?, note?}`` refs — a claim must be substantiated.

    ``path`` links the claim to a **changed** file's L3 fragment (a
    ``fragments.json`` entry); ``note`` anchors evidence that has no L3 anchor —
    prose ("no test touches this") and **widened-into** files, which have no diff
    fragment and therefore must never be a ``path``. Each entry needs at least one
    of the two. Whether a path names a real changed file is not checked here: the
    validator is pure (it never sees ``fragments.json``); the authoring contract
    owns that rule.
    """
    objects, errors = _as_objects(value, loc)
    if not errors and not objects:
        errors.append(AnalysisError(loc, "expected at least 1 item(s), got 0"))
    for i, ref in objects:
        ref_loc = f"{loc}[{i}]"
        if "path" not in ref and "note" not in ref:
            errors.append(AnalysisError(ref_loc, "must carry a path and/or a note"))
            continue
        if "path" in ref:
            errors.extend(_require_str(ref["path"], f"{ref_loc}.path"))
        if "note" in ref:
            errors.extend(_require_str(ref["note"], f"{ref_loc}.note"))
    return errors


def _validate_claim(
    claim: Mapping[str, object], loc: str, thread_id: str, seen_ids: dict[str, str]
) -> list[AnalysisError]:
    """One L2 claim: the assertion a reviewer judges (ADR-0009, ADR-0012).

    The contract: a stable id (``<thread-id>.c<N>`` — the disposition key and the
    cockpit element id), a kind from the closed vocabulary, the agent's confidence,
    at least one challenge question (what makes a claim auditable instead of a
    verdict), and at least one evidence reference. Risk claims additionally carry
    ``category`` + ``level``; ``level`` means risk severity and is risk-only.
    """
    errors: list[AnalysisError] = []

    claim_id = claim.get("id")
    errors.extend(_require_str(claim_id, f"{loc}.id"))
    if isinstance(claim_id, str) and claim_id.strip():
        prefix, dot, suffix = claim_id.partition(".")
        if prefix != thread_id or dot != "." or not _CLAIM_ID_SUFFIX.match(suffix):
            errors.append(
                AnalysisError(
                    f"{loc}.id",
                    f"must be '{thread_id}.c<N>' (its thread's id + '.c<N>'), got {claim_id!r}",
                )
            )
        elif claim_id in seen_ids:
            errors.append(AnalysisError(f"{loc}.id", f"duplicate id {claim_id!r}"))
        else:
            seen_ids[claim_id] = loc

    kind = claim.get("kind")
    errors.extend(_require_enum(kind, f"{loc}.kind", CLAIM_KINDS))
    errors.extend(_require_str(claim.get("summary"), f"{loc}.summary"))
    if "detail" in claim:
        errors.extend(_require_str(claim["detail"], f"{loc}.detail", allow_empty=True))
    errors.extend(_require_enum(claim.get("confidence"), f"{loc}.confidence", CONFIDENCE_LEVELS))
    errors.extend(
        _require_str_list(claim.get("challenge_questions"), f"{loc}.challenge_questions", min_len=1)
    )
    errors.extend(_validate_evidence(claim.get("evidence"), f"{loc}.evidence"))

    # category: required on risk claims, optional framing elsewhere; always vocabulary.
    if kind == "risk" and "category" not in claim:
        errors.append(AnalysisError(f"{loc}.category", "required on a risk claim"))
    if "category" in claim:
        errors.extend(_require_enum(claim["category"], f"{loc}.category", RISK_CATEGORIES))

    # level: risk severity — required on risk claims, meaningless (so rejected) elsewhere.
    if kind == "risk":
        errors.extend(_require_enum(claim.get("level"), f"{loc}.level", RISK_LEVELS))
    elif "level" in claim:
        errors.append(AnalysisError(f"{loc}.level", "only a risk claim carries a level"))

    # omission_kind: what the omission is adjacent to — omission claims only.
    if "omission_kind" in claim:
        if kind == "omission":
            errors.extend(
                _require_enum(claim["omission_kind"], f"{loc}.omission_kind", OMISSION_KINDS)
            )
        else:
            errors.append(
                AnalysisError(
                    f"{loc}.omission_kind", "only an omission claim carries omission_kind"
                )
            )

    return errors


def _validate_threads(value: object) -> tuple[list[AnalysisError], list[str], list[str]]:
    """``threads``: ≥1 narrative threads in descent order (ADR-0009's L1).

    Thread order *is* the Review Route — the recommended reading order. Each thread
    carries a stable id (``t<N>``), a title, a summary, the changed files it covers
    (``paths``, may be empty for a purely-adjacent thread), and ≥1 claims.

    Returns ``(errors, thread_ids, goal_omission_locs)``: the well-formed thread ids
    in analysis order (what ``alignment`` must partition) and the locations of claims
    carrying ``omission_kind: "goal"`` (which only a non-null ``alignment`` permits).
    """
    objects, errors = _as_objects(value, "threads")
    if not errors and not objects:
        errors.append(AnalysisError("threads", "expected at least 1 item(s), got 0"))

    seen_ids: dict[str, str] = {}
    thread_ids: list[str] = []
    goal_omission_locs: list[str] = []
    for i, thread in objects:
        loc = f"threads[{i}]"
        thread_id = thread.get("id")
        errors.extend(_require_str(thread_id, f"{loc}.id"))
        tid = thread_id if isinstance(thread_id, str) else ""
        if tid.strip():
            if not _THREAD_ID.match(tid):
                errors.append(AnalysisError(f"{loc}.id", f"must be 't<N>', got {tid!r}"))
            elif tid in seen_ids:
                errors.append(AnalysisError(f"{loc}.id", f"duplicate id {tid!r}"))
            else:
                seen_ids[tid] = loc
                thread_ids.append(tid)

        errors.extend(_require_str(thread.get("title"), f"{loc}.title"))
        errors.extend(_require_str(thread.get("summary"), f"{loc}.summary"))
        errors.extend(_require_str_list(thread.get("paths"), f"{loc}.paths"))

        claims, claim_errors = _as_objects(thread.get("claims"), f"{loc}.claims")
        errors.extend(claim_errors)
        if not claim_errors and not claims:
            errors.append(AnalysisError(f"{loc}.claims", "expected at least 1 item(s), got 0"))
        for j, claim in claims:
            claim_loc = f"{loc}.claims[{j}]"
            errors.extend(_validate_claim(claim, claim_loc, tid, seen_ids))
            if claim.get("omission_kind") == "goal":
                goal_omission_locs.append(claim_loc)

    return errors, thread_ids, goal_omission_locs


def _validate_alignment(
    value: object, thread_ids: Sequence[str], goal_omission_locs: Sequence[str]
) -> list[AnalysisError]:
    """``alignment``: the goal↔implementation partition (ADR-0010), or ``null``.

    With a stated goal, every thread is accounted for: it either **serves the
    goal** (``serves_goal``) or is a **drive-by** (``drive_by``) — the two lists
    partition the thread ids (each exactly once, none missing, none unknown).
    What the goal asked for that no thread delivers is not listed here: it lives
    as an ``omission`` claim with ``omission_kind: "goal"`` on its thread, so a
    disposition can attach to it like any other claim (ADR-0012).

    ``null`` means no stated goal was found (``context.json``'s ``goal`` is
    null); nothing can then be unserved, so goal-kind omission claims are
    rejected — an inferred intent is never measured like a stated goal.
    """
    if value is None:
        return [
            AnalysisError(
                f"{loc}.omission_kind",
                "'goal' requires a non-null alignment — with no stated goal, "
                "nothing can be goal-unserved",
            )
            for loc in goal_omission_locs
        ]
    if not isinstance(value, Mapping):
        return [AnalysisError("alignment", f"expected an object or null, got {_typename(value)}")]

    errors: list[AnalysisError] = []
    lists: dict[str, list[str]] = {}
    for key in ("serves_goal", "drive_by"):
        raw = value.get(key)
        if key not in value:
            errors.append(AnalysisError(f"alignment.{key}", "required key is missing"))
            lists[key] = []
            continue
        errors.extend(_require_str_list(raw, f"alignment.{key}"))
        if isinstance(raw, Sequence) and not isinstance(raw, str | bytes):
            lists[key] = [item for item in raw if isinstance(item, str)]
        else:
            lists[key] = []

    known = set(thread_ids)
    placed: dict[str, str] = {}
    for key, ids in lists.items():
        for i, tid in enumerate(ids):
            loc = f"alignment.{key}[{i}]"
            if tid not in known:
                errors.append(AnalysisError(loc, f"unknown thread id {tid!r}"))
            elif tid in placed:
                errors.append(AnalysisError(loc, f"thread {tid!r} already listed in {placed[tid]}"))
            else:
                placed[tid] = key

    # Coverage is meaningful only once both lists exist and are well-formed;
    # otherwise the structural errors above already say what to fix.
    if not errors:
        missing = [tid for tid in thread_ids if tid not in placed]
        if missing:
            errors.append(
                AnalysisError(
                    "alignment",
                    f"thread(s) {missing} are in neither serves_goal nor drive_by — "
                    "every thread either serves the goal or is a drive-by",
                )
            )
    return errors


def _validate_test_runner(value: object) -> list[AnalysisError]:
    """``test_runner``: ``{runner, runner_evidence?, command?}`` — all nullable.

    The read-only runner detection from step 2. Concrete things to *check* are
    ``verify`` claims on their threads (ADR-0009: the old checklist items became
    claims so dispositions can attach to them, ADR-0012); this block only records
    what runner exists. The runner is *suggested, never executed* (DESIGN).
    """
    if not isinstance(value, Mapping):
        return [AnalysisError("test_runner", f"expected an object, got {_typename(value)}")]
    errors: list[AnalysisError] = []
    for key in ("runner", "runner_evidence", "command"):
        if value.get(key) is not None:
            errors.extend(_require_str(value[key], f"test_runner.{key}"))
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


# Each required *independent* section → its validator. ``threads`` and
# ``alignment`` are validated explicitly in :func:`validate_analysis` — alignment
# is checked against the thread ids threads validation collects.
_SECTION_VALIDATORS = {
    "test_runner": _validate_test_runner,
    "diagrams": _validate_diagrams,
}


def validate_analysis(obj: object) -> list[AnalysisError]:
    """Validate a parsed ``analysis.json``; return every problem (empty == valid).

    Checks the top-level object carries the schema tag, a non-empty ``title`` and
    ``intent_summary`` (L0's fallback read when there is no stated goal,
    ADR-0010), ≥1 threads each with ≥1 substantiated claims (ids well-formed and
    unique — the disposition keys of ADR-0012), the ``alignment`` partition
    measuring the threads against the stated goal (nullable — ``null`` when no
    goal was found), the ``widened_into`` accountability list (ADR-0011: files
    read beyond the diff, possibly empty), the ``test_runner`` block, and
    ``diagrams``. Returns a flat list of :class:`AnalysisError` with ``location``
    paths (e.g. ``threads[0].claims[2].level``) so a malformed file points the
    agent straight at what to fix.
    """
    if not isinstance(obj, Mapping):
        return [AnalysisError("$", f"analysis must be a JSON object, got {_typename(obj)}")]

    errors: list[AnalysisError] = []

    # Pin to the exact supported revision: this validator encodes the 0.2 shape, so
    # an older or unknown tag (e.g. review-analysis/0.1) must fail rather than be
    # validated against rules it does not match. Bump SCHEMA when the shape changes.
    schema = obj.get("schema")
    if schema != SCHEMA:
        errors.append(AnalysisError("schema", f"must be {SCHEMA!r}, got {schema!r}"))

    errors.extend(_require_str(obj.get("title"), "title"))
    errors.extend(_require_str(obj.get("intent_summary"), "intent_summary"))

    # ADR-0011: the analysis states what it widened into. Required so writer and
    # validator cannot diverge; an honest "nothing" is an empty list, never absence.
    if "widened_into" not in obj:
        errors.append(AnalysisError("widened_into", "required section is missing"))
    else:
        errors.extend(_require_str_list(obj["widened_into"], "widened_into"))

    # Threads first: alignment is validated against the thread ids they declare.
    thread_ids: list[str] = []
    goal_omission_locs: list[str] = []
    if "threads" not in obj:
        errors.append(AnalysisError("threads", "required section is missing"))
    else:
        thread_errors, thread_ids, goal_omission_locs = _validate_threads(obj["threads"])
        errors.extend(thread_errors)

    # ADR-0010: required (possibly null) so writer and validator cannot diverge —
    # an analysis must *say* whether it measured the threads against a goal.
    if "alignment" not in obj:
        errors.append(AnalysisError("alignment", "required section is missing"))
    else:
        errors.extend(_validate_alignment(obj["alignment"], thread_ids, goal_omission_locs))

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
