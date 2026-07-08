"""The Analysis Schema Validator — a deep module guarding ``analysis.json`` (issues #6, #39, #84).

``analysis.json`` is the change narrator's structured intermediate reasoning about
the diff (CONTEXT: *Analysis*): the substrate the Review Cockpit is authored from
and the substrate the feedback loop answers from (ADR-0001). Since ADR-0016 the
shape is **step-centric**: the changeset is decomposed into narrative **Threads**
(the feature, the drive-by refactor, the config churn), each carrying the **Review
Steps** that guide a reviewer through it — one guided stop each, not a finding.
Every step carries a **Behavior Impact** (did behavior change here, or was code only
moved?), a **why_now** (why it sits at this point on the Review Route), the agent's
**confidence**, its **review_prompts** (the comparisons the reviewer should make),
and **evidence references** into the diff. The cockpit's L1/L2/L3 layers are
authored straight from this structure, so its correctness depends on the substrate
being well-formed: a step with no evidence, an id that can't anchor a disposition
(ADR-0012), a ``behavior-change`` step with no prompt to compare against would all
surface as a broken or misleading layer.

Deep module (a simple surface over fussy internals): the only entry point is
:func:`validate_analysis`, which returns a list of :class:`AnalysisError` — empty
means the file is structurally sound. It mirrors the Cockpit Linter
(:mod:`branch_review.lint`): a tripwire that *refuses* a malformed analysis, never
one that edits it. It validates **structure, types, vocabulary, and id integrity**,
not editorial quality — whether a step *narrates* well is the narrator's job;
whether it is *shaped right* is this module's.

The canonical vocabularies (:data:`IMPACTS`, :data:`CONFIDENCE_LEVELS`) live here as
the single source of truth the SKILL guidance and the cockpit share. Risk levels,
omission kinds, and the old claim ``kind`` set are gone from the default substrate
(ADR-0016): the narrator narrates, and hunting re-enters only through an opt-in
Focus Lens — never as a default schema surface. See ``CONTEXT.md``, ``DESIGN.md``,
and ADR-0009/0010/0011/0012/0016.

Since ADR-0010 the analysis also carries ``alignment`` — the goal↔implementation
partition: which threads serve the stated goal and which are drive-bys. ``null``
when no goal was found. Goal-unserved work is no longer a schema kind; it surfaces
as an Attention Note at L0 (ADR-0016).
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
# 0.4 is the Change Narrator shape (ADR-0016): threads > steps > evidence, replacing
# 0.3's threads > claims > evidence. The break is clean — this validator encodes only
# 0.4, so a 0.3 (or older) document is refused with a located error, not silently
# revalidated under rules it predates. There is no dual-schema path.
SCHEMA = "review-analysis/0.4"

# The Behavior Impact a step may carry (ADR-0016's L2 vocabulary) — a closed set so
# the cockpit can badge deterministically and derive a thread's character from its
# steps, and so the narrator cannot coin ad-hoc labels:
#   behavior-change     — user/API/runtime/config/persistence/error/security/perf
#                         behavior changed
#   behavior-preserving — refactor/relocation/extraction that appears intended to
#                         preserve behavior (the *expensive* label — earn it)
#   test-change         — tests added/removed/re-aimed, with the behavior they document
#   mechanical-change   — generated files, lockfiles, vendored code, formatting, build
#   unknown-impact      — the narrator can't honestly tell without more context
IMPACTS = (
    "behavior-change",
    "behavior-preserving",
    "test-change",
    "mechanical-change",
    "unknown-impact",
)

# The impacts where the reviewer has a concrete comparison to make, so ≥1
# ``review_prompt`` is required: what changed (behavior-change), what allegedly did
# not (behavior-preserving — the preservation check), and what is unresolved
# (unknown-impact — name the missing context). Prompts are optional on test-change
# and mechanical-change, where a forced prompt would only breed boilerplate.
PROMPT_REQUIRED_IMPACTS = frozenset({"behavior-change", "behavior-preserving", "unknown-impact"})

# The agent's stated confidence in a step (ADR-0012: confidence, never a verdict).
CONFIDENCE_LEVELS = ("high", "medium", "low")

# Keys that must never ride on an Attention Note. An Attention Note is a muted,
# secondary aside (ADR-0016) — no severity, no category, no level: those are the
# issue-finder attributes that return only through an opt-in Focus Lens, never as a
# default surface. Rejecting them keeps hunting from creeping back into the spine.
_FORBIDDEN_NOTE_KEYS = ("severity", "category", "level")

# Id shapes: threads are ``t<N>``; steps are ``<thread-id>.s<N>`` (the stable ids a
# disposition attaches to and the cockpit element ids — ADR-0012/0016).
_THREAD_ID = re.compile(r"^t\d+$")
_STEP_ID_SUFFIX = re.compile(r"^s\d+$")

# A step's ``relates_to`` links, deferred for id-integrity checking until every step
# id is known: ``(location, own step id, [(index, target id), …])`` — the targets are
# the salvaged strings from :func:`_as_strings`, indices preserved so a bad target
# still locates correctly.
_PendingRelates = list[tuple[str, str | None, list[tuple[int, str]]]]


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
    """``value`` must be a list of non-empty strings with at least ``min_len`` items.

    The errors-only view of :func:`_as_strings`, for callers that just want to reject
    a malformed list and don't need the salvaged strings back.
    """
    return _as_strings(value, location, min_len=min_len)[1]


def _require_enum(value: object, location: str, allowed: Sequence[str]) -> list[AnalysisError]:
    """``value`` must be one of ``allowed`` (a closed vocabulary)."""
    if value not in allowed:
        shown = value if isinstance(value, str) else _typename(value)
        return [AnalysisError(location, f"must be one of {list(allowed)}, got {shown!r}")]
    return []


def _forbid_keys(
    mapping: Mapping[str, object], keys: Sequence[str], location: str, reason: str
) -> list[AnalysisError]:
    """None of ``keys`` may appear on ``mapping`` — the negative of ``_require_*``.

    Lets a section validator state what must be *absent* as declaratively as it
    states what must be present: a derived or lens-gated attribute that has no place
    on this object (a thread's ``impact``, a note's ``severity``). ``reason`` is the
    shared explanation; each offending key is located as ``<location>.<key>``.
    """
    return [AnalysisError(f"{location}.{key}", reason) for key in keys if key in mapping]


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


def _as_strings(
    value: object, location: str, *, min_len: int = 0
) -> tuple[list[tuple[int, str]], list[AnalysisError]]:
    """Coerce ``value`` to ``(original_index, non-empty str)`` pairs, reporting bad shapes.

    The string sibling of :func:`_as_objects`: a non-list is reported; a non-string or
    empty-string item is reported and skipped, but the surviving strings keep their
    real indices so a later integrity pass (``relates_to`` targets, ``alignment``
    thread ids) locates an error correctly. Callers that only need the errors use the
    :func:`_require_str_list` view.
    """
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return [], [AnalysisError(location, f"expected a list, got {_typename(value)}")]
    items = list(value)
    if len(items) < min_len:
        return [], [
            AnalysisError(location, f"expected at least {min_len} item(s), got {len(items)}")
        ]
    strings: list[tuple[int, str]] = []
    errors: list[AnalysisError] = []
    for i, item in enumerate(items):
        if isinstance(item, str) and item.strip():
            strings.append((i, item))
        else:
            errors.extend(_require_str(item, f"{location}[{i}]"))
    return strings, errors


# --- Step / thread validators (ADR-0009's L1/L2, ADR-0016's step substrate) ----


def _validate_evidence(value: object, loc: str) -> list[AnalysisError]:
    """``evidence``: ≥1 ``{path?, hunk?, note?}`` refs — a step must be substantiated.

    ``path`` links the step to a **changed** file's L3 fragment (a
    ``fragments.json`` entry); ``note`` anchors evidence that has no L3 anchor —
    prose ("no test touches this") and **widened-into** files, which have no diff
    fragment and therefore must never be a ``path``. Each entry needs at least one
    of the two.

    ``hunk`` (ADR-0014) narrows a ``path`` ref to the exact hunk that substantiates
    the step: a **1-based** index into that file's hunk sequence, read from the
    manifest's per-file hunk index. It only makes sense **on a ``path`` ref** — a
    ``note`` has no diff fragment to anchor into — and must be a positive integer.
    Whether the index actually names an existing hunk (its upper bound) is **not**
    checked here: the validator is pure (it never sees ``fragments.json``); that
    resolution belongs to the Cockpit Linter's anchor rule, exactly as whether a
    ``path`` names a real changed file does.
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
        if "hunk" in ref:
            errors.extend(_validate_hunk(ref["hunk"], ref, f"{ref_loc}.hunk"))
    return errors


def _validate_hunk(value: object, ref: Mapping[str, object], loc: str) -> list[AnalysisError]:
    """A ``{path}`` ref's optional ``hunk``: a 1-based hunk index (ADR-0014)."""
    errors: list[AnalysisError] = []
    # A hunk anchors into a diff fragment, which only a ``path`` ref has — a
    # ``note``-only ref (prose, a widened-in file) has nothing to anchor to.
    if "path" not in ref:
        errors.append(
            AnalysisError(loc, "only a path ref may carry a hunk (a note has no diff fragment)")
        )
    # ``bool`` is an ``int`` subclass — exclude it so ``true``/``false`` isn't a "1"/"0".
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(AnalysisError(loc, f"expected a positive integer, got {_typename(value)}"))
    elif value < 1:
        errors.append(AnalysisError(loc, f"must be a 1-based hunk index (>= 1), got {value}"))
    return errors


def _validate_attention_notes(value: object, loc: str) -> list[AnalysisError]:
    """``attention_notes``: optional muted asides — ``{text, evidence?}`` only (ADR-0016).

    A note is *narration from the negative side* (an untested behavior change, a
    goal gap), not an adjudicated finding: it carries no severity, no category, no
    level, no disposition of its own. Those hunting attributes are lens-gated, so a
    note that carries one is rejected — that is what keeps issue-finding out of the
    default spine. ``text`` is required; ``evidence`` (if present) is validated like
    any step's evidence.
    """
    objects, errors = _as_objects(value, loc)
    for i, note in objects:
        note_loc = f"{loc}[{i}]"
        errors.extend(_require_str(note.get("text"), f"{note_loc}.text"))
        if "evidence" in note:
            errors.extend(_validate_evidence(note["evidence"], f"{note_loc}.evidence"))
        errors.extend(
            _forbid_keys(
                note,
                _FORBIDDEN_NOTE_KEYS,
                note_loc,
                "an attention note carries no severity, category, or level — those are "
                "lens-gated hunting attributes, not a default surface (ADR-0016)",
            )
        )
    return errors


def _validate_step(
    step: Mapping[str, object],
    loc: str,
    thread_id: str,
    step_ids_seen: set[str],
    pending_relates: _PendingRelates,
) -> list[AnalysisError]:
    """One L2 Review Step: a guided stop on the walkthrough (ADR-0009/0012/0016).

    The contract: a stable id (``<thread-id>.s<N>`` — the disposition key and the
    cockpit element id), a Behavior Impact from the closed vocabulary, the agent's
    confidence, a ``why_now`` (why the step sits here on the route), at least one
    evidence reference, and — where the reviewer has a comparison to make
    (behavior-change / behavior-preserving / unknown-impact) — at least one
    ``review_prompt``. ``relates_to`` links (validated for id integrity after every
    step is known) and ``attention_notes`` are optional.
    """
    errors: list[AnalysisError] = []

    step_id = step.get("id")
    errors.extend(_require_str(step_id, f"{loc}.id"))
    if isinstance(step_id, str) and step_id.strip():
        prefix, dot, suffix = step_id.partition(".")
        if prefix != thread_id or dot != "." or not _STEP_ID_SUFFIX.match(suffix):
            errors.append(
                AnalysisError(
                    f"{loc}.id",
                    f"must be '{thread_id}.s<N>' (its thread's id + '.s<N>'), got {step_id!r}",
                )
            )
        elif step_id in step_ids_seen:
            errors.append(AnalysisError(f"{loc}.id", f"duplicate id {step_id!r}"))
        else:
            step_ids_seen.add(step_id)

    impact = step.get("impact")
    errors.extend(_require_enum(impact, f"{loc}.impact", IMPACTS))
    errors.extend(_require_str(step.get("summary"), f"{loc}.summary"))
    if "detail" in step:
        errors.extend(_require_str(step["detail"], f"{loc}.detail", allow_empty=True))
    errors.extend(_require_enum(step.get("confidence"), f"{loc}.confidence", CONFIDENCE_LEVELS))
    errors.extend(_require_str(step.get("why_now"), f"{loc}.why_now"))

    # review_prompts: required (≥1) where the reviewer has a comparison to make;
    # optional on test-change / mechanical-change, but well-formed if present.
    if impact in PROMPT_REQUIRED_IMPACTS:
        errors.extend(
            _require_str_list(step.get("review_prompts"), f"{loc}.review_prompts", min_len=1)
        )
    elif "review_prompts" in step:
        errors.extend(_require_str_list(step["review_prompts"], f"{loc}.review_prompts"))

    errors.extend(_validate_evidence(step.get("evidence"), f"{loc}.evidence"))

    if "attention_notes" in step:
        errors.extend(_validate_attention_notes(step["attention_notes"], f"{loc}.attention_notes"))

    # relates_to: shape-checked now, id-integrity after all steps are collected (a
    # link may point forward into a later thread, so the full id set must exist first).
    # The salvaged (index, id) pairs carry forward so the integrity pass locates a bad
    # target without re-checking shapes this pass already reported.
    if "relates_to" in step:
        targets, target_errors = _as_strings(step["relates_to"], f"{loc}.relates_to")
        errors.extend(target_errors)
        own = step_id if isinstance(step_id, str) else None
        pending_relates.append((f"{loc}.relates_to", own, targets))

    return errors


def _validate_relates_to(
    pending_relates: _PendingRelates, step_ids_seen: set[str]
) -> list[AnalysisError]:
    """``relates_to`` id integrity: each target is a real, other step id (ADR-0016).

    Runs after every thread is walked, so a step may relate forward to a later
    thread's step. A dangling id (no such step) and a self-reference (a step relating
    to itself) are both rejected — the Stage renders these as one-click jumps, so a
    bad id would resolve to nothing. Targets arrive as salvaged ``(index, id)`` pairs
    (:func:`_as_strings`), so this pass only checks membership, not shape.
    """
    errors: list[AnalysisError] = []
    for loc, own_id, targets in pending_relates:
        for i, target in targets:
            target_loc = f"{loc}[{i}]"
            if target == own_id:
                errors.append(
                    AnalysisError(target_loc, f"a step cannot relate to itself ({target!r})")
                )
            elif target not in step_ids_seen:
                errors.append(AnalysisError(target_loc, f"unknown step id {target!r}"))
    return errors


def _validate_threads(value: object) -> tuple[list[AnalysisError], list[str]]:
    """``threads``: ≥1 narrative threads in descent order (ADR-0009's L1).

    Thread order *is* the Review Route — the recommended reading order. Each thread
    carries a stable id (``t<N>``), a title, a summary, the changed files it covers
    (``paths``, may be empty for a purely-adjacent thread), and ≥1 steps. A thread
    carries **no authored impact** — its character is derived from its steps
    (ADR-0016), so an ``impact`` key on a thread is rejected.

    Returns ``(errors, thread_ids)``: the well-formed thread ids in analysis order
    (what ``alignment`` must partition).
    """
    objects, errors = _as_objects(value, "threads")
    if not errors and not objects:
        errors.append(AnalysisError("threads", "expected at least 1 item(s), got 0"))

    # Two id sets, each with one purpose: thread ids (the ``thread_ids`` list below,
    # which also carries their order for ``alignment``) and step ids (``step_ids_seen``,
    # which serves both duplicate detection and ``relates_to`` integrity). Thread ids
    # (``t<N>``) and step ids (``t<N>.s<N>``) can never collide, so the two namespaces
    # stay cleanly separate.
    step_ids_seen: set[str] = set()
    pending_relates: _PendingRelates = []
    thread_ids: list[str] = []
    for i, thread in objects:
        loc = f"threads[{i}]"
        thread_id = thread.get("id")
        errors.extend(_require_str(thread_id, f"{loc}.id"))
        tid = thread_id if isinstance(thread_id, str) else ""
        if tid.strip():
            if not _THREAD_ID.match(tid):
                errors.append(AnalysisError(f"{loc}.id", f"must be 't<N>', got {tid!r}"))
            elif tid in thread_ids:
                errors.append(AnalysisError(f"{loc}.id", f"duplicate id {tid!r}"))
            else:
                thread_ids.append(tid)

        errors.extend(_require_str(thread.get("title"), f"{loc}.title"))
        errors.extend(_require_str(thread.get("summary"), f"{loc}.summary"))
        errors.extend(_require_str_list(thread.get("paths"), f"{loc}.paths"))

        # A thread's impact is derived, never authored (ADR-0016) — reject it.
        errors.extend(
            _forbid_keys(
                thread,
                ("impact",),
                loc,
                "a thread carries no authored impact — thread character is derived from "
                "its steps' Behavior Impact (ADR-0016)",
            )
        )

        steps, step_errors = _as_objects(thread.get("steps"), f"{loc}.steps")
        errors.extend(step_errors)
        if not step_errors and not steps:
            errors.append(AnalysisError(f"{loc}.steps", "expected at least 1 item(s), got 0"))
        for j, step in steps:
            step_loc = f"{loc}.steps[{j}]"
            errors.extend(_validate_step(step, step_loc, tid, step_ids_seen, pending_relates))

    errors.extend(_validate_relates_to(pending_relates, step_ids_seen))
    return errors, thread_ids


def _validate_alignment(value: object, thread_ids: Sequence[str]) -> list[AnalysisError]:
    """``alignment``: the goal↔implementation partition (ADR-0010), or ``null``.

    With a stated goal, every thread is accounted for: it either **serves the
    goal** (``serves_goal``) or is a **drive-by** (``drive_by``) — the two lists
    partition the thread ids (each exactly once, none missing, none unknown).
    What the goal asked for that no thread delivers is not listed here: since
    ADR-0016 it surfaces as an Attention Note at L0, not a schema kind.

    ``null`` means no stated goal was found (``context.json``'s ``goal`` is null);
    there is then nothing to measure the threads against.
    """
    if value is None:
        return []
    if not isinstance(value, Mapping):
        return [AnalysisError("alignment", f"expected an object or null, got {_typename(value)}")]

    errors: list[AnalysisError] = []
    lists: dict[str, list[str]] = {}
    for key in ("serves_goal", "drive_by"):
        if key not in value:
            errors.append(AnalysisError(f"alignment.{key}", "required key is missing"))
            lists[key] = []
            continue
        salvaged, key_errors = _as_strings(value.get(key), f"alignment.{key}")
        errors.extend(key_errors)
        lists[key] = [tid for _, tid in salvaged]

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

    The read-only runner detection from step 2. It only records what runner exists;
    the runner is *suggested, never executed* (DESIGN). Concrete things to check are
    review_prompts on their steps now, not a separate checklist (ADR-0016).
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
    ADR-0010), ≥1 threads each with ≥1 substantiated steps (ids well-formed and
    unique — the disposition keys of ADR-0012; ``relates_to`` links resolving to
    real steps), the ``alignment`` partition measuring the threads against the
    stated goal (nullable — ``null`` when no goal was found), the ``widened_into``
    accountability list (ADR-0011: files read beyond the diff, possibly empty), the
    ``test_runner`` block, and ``diagrams``. Returns a flat list of
    :class:`AnalysisError` with ``location`` paths (e.g.
    ``threads[0].steps[2].review_prompts``) so a malformed file points the narrator
    straight at what to fix.
    """
    if not isinstance(obj, Mapping):
        return [AnalysisError("$", f"analysis must be a JSON object, got {_typename(obj)}")]

    errors: list[AnalysisError] = []

    # Pin to the exact supported revision: this validator encodes the 0.4 shape, so
    # an older or unknown tag (e.g. review-analysis/0.3) must fail rather than be
    # validated against rules it does not match. The break is clean — no dual path.
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
    if "threads" not in obj:
        errors.append(AnalysisError("threads", "required section is missing"))
    else:
        thread_errors, thread_ids = _validate_threads(obj["threads"])
        errors.extend(thread_errors)

    # ADR-0010: required (possibly null) so writer and validator cannot diverge —
    # an analysis must *say* whether it measured the threads against a goal.
    if "alignment" not in obj:
        errors.append(AnalysisError("alignment", "required section is missing"))
    else:
        errors.extend(_validate_alignment(obj["alignment"], thread_ids))

    for name, validator in _SECTION_VALIDATORS.items():
        if name not in obj:
            errors.append(AnalysisError(name, "required section is missing"))
            continue
        errors.extend(validator(obj[name]))

    return errors


def step_ids(analysis: object) -> list[str]:
    """Every step id declared in an analysis, in document order (duplicates kept).

    The set the Cockpit Linter (:mod:`branch_review.lint`) checks the authored DOM
    against — the L2 panels that must each carry a live-evidence seam, and no others.
    Deliberately **tolerant**: it walks the same ``threads[].steps[].id`` path
    :func:`validate_analysis` guards but skips anything malformed rather than raising,
    so a caller that lints an already-validated analysis gets its ids and one that
    passes a rough draft still gets whatever ids are present. Order and duplicates are
    preserved so the linter can report a repeated id itself.
    """
    ids: list[str] = []
    threads = analysis.get("threads") if isinstance(analysis, Mapping) else None
    if not isinstance(threads, list):
        return ids
    for thread in threads:
        if not isinstance(thread, Mapping):
            continue
        steps = thread.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if isinstance(step, Mapping) and isinstance(step.get("id"), str):
                ids.append(step["id"])
    return ids


def main(argv: list[str] | None = None) -> int:
    """CLI: validate an ``analysis.json`` file; exit non-zero on any problem.

    The skill runs this after the narrator authors ``analysis.json`` and before it
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
