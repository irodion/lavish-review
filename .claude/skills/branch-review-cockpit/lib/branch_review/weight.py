"""Derived reading weight — a per–Review Step reading-cost signal (issue #100).

A reviewer deciding whether to enter a step, or how to budget a sitting, has no cost
signal from the Map alone: every dot looks the same whether the stop is a three-line
tweak or a five-hundred-line contract rewrite. This module derives a **reading weight**
— an estimated number of lines to read — for a step, a thread, and the whole Review
Route, from the evidence the step already cites. It is **derived at render time, never
authored** (the derived-over-authored principle, ADR-0016): the isolated narrator never
sees or writes a weight, so a weight can never bias the analysis, and the same collected
fragments always produce the same weight (deterministic, unit-tested here).

The contribution rule, per evidence ref:

* **Hunk-anchored ref** (``{path, hunk}``) — contributes that hunk's line count, read
  off the hunk header in the fragments manifest (``@@ -a,b +c,d @@`` → ``max(b, d)``, the
  larger of the two sides; a missing count means the single-line form and counts as 1).
  This is a precise, per-hunk signal: the step points at an exact region, so it is sized
  by exactly that region.
* **File-level ref** (``{path}`` with no ``hunk``) — contributes the file's changed-line
  count (``added + deleted``), **capped at** :data:`FILE_LEVEL_CAP`. A file-level ref is
  imprecise (it points at the whole file, not a region), and an unbounded whole-file
  citation of a large or generated file would swamp the route estimate the way a precise
  hunk citation legitimately should not. The cap makes a whole-file citation count as
  *some* reading without letting it dominate.
* **Note-only ref** (``{note}`` with no ``path``) — contributes nothing and marks the
  step's weight :attr:`~StepWeight.approximate`: it is prose evidence with nothing to
  size.

Two degrade-gracefully cases both mark the weight approximate rather than crash or
mislead (acceptance criterion): an **omitted-body** file cited at file level (its body
is gone, but its ``added``/``deleted`` stats survive the omission, so it is still sized
from those and flagged), and a hunk header that cannot be parsed (contributes nothing,
flagged). :attr:`~StepWeight.approximate` means the shown number is a **floor** — there
is evidence the derivation could not size precisely.

The time heuristic is stated wherever a rollup is shown (never a bare number the reviewer
cannot recalibrate): weight in lines ÷ :data:`LINES_PER_MINUTE` at reading pace.

Pure policy, no I/O — :mod:`branch_review.render` owns the HTML representation and calls
in here for every number and label it shows.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

# Reading pace for the route/thread time estimate. Stated in the cockpit next to every
# derived minute figure so the reviewer can recalibrate to their own pace — a guided
# review skims narrated change rather than inspecting cold code, so this sits well above
# a deep-inspection rate. The exact value is a heuristic, not a measurement.
LINES_PER_MINUTE = 25

# The ceiling on a single file-level ref's contribution (see the module docstring): a
# whole-file citation is imprecise, so it counts as bounded reading rather than its full
# — possibly enormous — changed-line count.
FILE_LEVEL_CAP = 40

# A unified-diff hunk header: ``@@ -a[,b] +c[,d] @@``. The two optional groups are the
# old-side and new-side line counts; when a side omits its count it is the single-line
# form (count 1). Searched (not fully matched) so it is found inside the manifest's
# marker-wrapped, escaped ``header_html`` — the digits survive escaping untouched.
_HUNK_HEADER_RE = re.compile(r"@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")


@dataclass(frozen=True)
class StepWeight:
    """A derived reading weight: an estimated line count and whether it is a floor.

    ``approximate`` is set when some cited evidence could not be sized precisely
    (note-only evidence, an omitted-body file, or an unparseable hunk header). A true
    value means ``lines`` under-counts — the reviewer should read it as "at least this".
    """

    lines: int
    approximate: bool


def _int(value: object) -> int:
    """A non-negative int from a manifest field, or 0 (``bool`` is not an int here)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(value, 0)


def _parse_hunk_counts(header_html: str) -> tuple[int, int] | None:
    """``(old_count, new_count)`` from a hunk header, or ``None`` if unparseable.

    An omitted count is git's single-line hunk form, so it resolves to 1.
    """
    match = _HUNK_HEADER_RE.search(header_html)
    if match is None:
        return None
    old = int(match.group(1)) if match.group(1) is not None else 1
    new = int(match.group(2)) if match.group(2) is not None else 1
    return old, new


def hunk_line_count(entry: Mapping[str, object], index: object) -> int | None:
    """Lines in the ``index``-th hunk of a file entry (``max`` of the two sides), or None.

    ``None`` — the hunk is absent from the manifest, or its header cannot be parsed —
    is the caller's signal to flag the step's weight approximate.
    """
    hunks = entry.get("hunks")
    if not isinstance(hunks, list):
        return None
    for hunk in hunks:
        if not isinstance(hunk, Mapping):
            continue
        hunk_index = hunk.get("index")
        if isinstance(hunk_index, bool) or hunk_index != index:
            continue
        header = hunk.get("header_html")
        if not isinstance(header, str):
            return None
        counts = _parse_hunk_counts(header)
        return None if counts is None else max(counts)
    return None


def file_change_size(entry: Mapping[str, object]) -> int:
    """A file's changed-line count (``added + deleted``) from its surviving stats.

    These stats survive body omission (the classifier keeps them), so an omitted-body
    file is still sizeable at file level — just flagged approximate by the caller.
    """
    return _int(entry.get("added")) + _int(entry.get("deleted"))


def file_ref_weight(entry: Mapping[str, object]) -> int:
    """A file-level ref's bounded contribution: changed lines, capped at the ceiling."""
    return min(file_change_size(entry), FILE_LEVEL_CAP)


def step_weight(
    evidence: object, files_by_path: Mapping[str, Mapping[str, object]]
) -> StepWeight:
    """The derived reading weight for one step, summed over its evidence refs.

    Duplicate refs (the same hunk, or the same file at file level, cited twice) are
    counted once. A ref whose path is not in the manifest, or whose hunk cannot be
    sized, contributes nothing and marks the result approximate — a floor, never a
    fabricated number.
    """
    total = 0
    approximate = False
    seen: set[tuple[str, object]] = set()
    refs = evidence if isinstance(evidence, Sequence) and not isinstance(evidence, str) else []
    for ref in refs:
        if not isinstance(ref, Mapping):
            continue
        path = ref.get("path")
        if not isinstance(path, str):
            # Note-only (or malformed) evidence: nothing to size, but its presence
            # means the step's real reading load is larger than what we counted.
            if isinstance(ref.get("note"), str):
                approximate = True
            continue
        entry = files_by_path.get(path)
        if entry is None:
            approximate = True
            continue
        if "hunk" in ref:
            key: tuple[str, object] = (path, ("hunk", ref["hunk"]))
            if key in seen:
                continue
            seen.add(key)
            count = hunk_line_count(entry, ref["hunk"])
            if count is None:
                approximate = True
            else:
                total += count
        else:
            key = (path, "file")
            if key in seen:
                continue
            seen.add(key)
            if entry.get("omitted") is True:
                # Body gone; sized from surviving stats only, so flag it a floor.
                approximate = True
            total += file_ref_weight(entry)
    return StepWeight(lines=total, approximate=approximate)


def rollup(weights: Iterable[StepWeight]) -> StepWeight:
    """Combine step weights into a thread- or route-level total (approximate if any is)."""
    total = 0
    approximate = False
    for weight in weights:
        total += weight.lines
        approximate = approximate or weight.approximate
    return StepWeight(lines=total, approximate=approximate)


def reading_minutes(lines: int) -> int:
    """Whole minutes to read ``lines`` at :data:`LINES_PER_MINUTE` (0 for nothing)."""
    if lines <= 0:
        return 0
    return math.ceil(lines / LINES_PER_MINUTE)


def lines_label(weight: StepWeight) -> str:
    """A compact lines label: ``24 lines`` (sized) or ``~24 lines`` (approximate floor)."""
    unit = "line" if weight.lines == 1 else "lines"
    prefix = "~" if weight.approximate else ""
    return f"{prefix}{weight.lines} {unit}"


def minutes_label(lines: int) -> str:
    """A compact time label at reading pace: ``~5 min``, or ``<1 min`` for a tiny load."""
    minutes = reading_minutes(lines)
    return f"~{minutes} min" if minutes >= 1 else "<1 min"
