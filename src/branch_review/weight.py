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

* **Hunk-anchored ref** (``{path, hunk}``) — contributes that hunk's line count. The
  precise source is the collector's exact per-hunk ``lines`` (the count of rendered
  diff-body lines it measured from the raw bytes). When only the ``@@ -a,b +c,d @@``
  header is available (an older manifest), it falls back to ``max(b, d)`` — a true
  **lower bound**, since a modify-in-place hunk shares context in both sides and the
  header's larger side drops the ``min(added, removed)`` overlap — and flags the weight
  approximate. If the same file is also cited at file level in the same step, the hunk
  refs supersede that file-level ref (below), so the file's lines are never double-counted.
* **File-level ref** (``{path}`` with no ``hunk``) — contributes the file's changed-line
  count (``added + deleted``), **capped at** :data:`FILE_LEVEL_CAP`. A file-level ref is
  imprecise (it points at the whole file, not a region), and an unbounded whole-file
  citation of a large or generated file would swamp the route estimate the way a precise
  hunk citation legitimately should not. The cap makes a whole-file citation count as
  *some* reading without letting it dominate. A file-level ref is **dropped** when the
  same step also cites specific hunks of that file — the hunks are the precise evidence.
* **Note-only ref** (``{note}`` with no ``path``) — contributes nothing and marks the
  step's weight :attr:`~StepWeight.approximate`: it is prose evidence with nothing to
  size.

Two degrade-gracefully cases both mark the weight approximate rather than crash or
mislead (acceptance criterion): an **omitted-body** file cited at file level (its body
is gone, but its ``added``/``deleted`` stats survive the omission, so it is still sized
from those and flagged), and a hunk sized from the header floor rather than an exact
count (as above). :attr:`~StepWeight.approximate` means the shown number is a **floor** —
there is evidence the derivation could not size precisely.

Rollups (:func:`rollup`) sum **per step-visit**: thread and route totals add each step's
own reading load, so evidence re-cited across two steps is counted at each stop the
reviewer visits — a route budget measures reading stops, not unique lines. Within a
single step, exact-duplicate refs collapse and a file-level ref superseded by hunk refs
is dropped, so a step never double-counts its own lines.

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

# Map-dot size tiers (issue #100): a reading weight in lines → one of four width buckets,
# so the Map reads a heavy stop as a longer bar than a trivial one. This boundary policy
# is Python-owned and unit-tested here, like every weight derivation; the vendored Deck JS
# only relays the chosen bucket verbatim onto the dot, exactly as it relays the renderer-
# derived ``data-impact``/``data-disposition``. The dot's *width* is the emphasis, never
# its colour — the judgment-color discipline stays the stylesheet's concern.
_WEIGHT_BUCKET_BOUNDS = ((15, "w1"), (50, "w2"), (150, "w3"))

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


def hunk_reading_size(entry: Mapping[str, object], index: object) -> tuple[int, bool] | None:
    """``(lines, exact)`` for the ``index``-th hunk of a file entry, or ``None``.

    Prefers the collector's exact per-hunk ``lines`` (``exact=True``). Falls back to the
    header's larger side ``max(old, new)`` (``exact=False``) — a true lower bound, since
    a modify-in-place hunk's shared context is dropped by taking the max — so the caller
    flags the weight approximate. ``None`` — the hunk is absent, or has neither a valid
    ``lines`` nor a parseable header — signals the caller to flag it approximate with no
    contribution.
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
        lines = hunk.get("lines")
        if isinstance(lines, int) and not isinstance(lines, bool) and lines >= 0:
            return lines, True
        header = hunk.get("header_html")
        if isinstance(header, str):
            counts = _parse_hunk_counts(header)
            if counts is not None:
                return max(counts), False  # a lower bound — see the docstring
        return None
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


def step_weight(evidence: object, files_by_path: Mapping[str, Mapping[str, object]]) -> StepWeight:
    """The derived reading weight for one step, summed over its evidence refs.

    Duplicate refs (the same hunk, or the same file at file level, cited twice) are
    counted once. A ref whose path is not in the manifest, or whose hunk cannot be
    sized, contributes nothing and marks the result approximate — a floor, never a
    fabricated number.
    """
    total = 0
    approximate = False
    seen_hunks: set[tuple[str, object]] = set()
    seen_files: set[str] = set()
    raw = evidence if isinstance(evidence, Sequence) and not isinstance(evidence, str) else []
    refs = [ref for ref in raw if isinstance(ref, Mapping)]
    # A file cited at hunk level is sized by those hunks; a same-step file-level ref to it
    # is then redundant and would double-count the file's lines, so it is dropped.
    hunk_cited: set[str] = {
        ref["path"] for ref in refs if isinstance(ref.get("path"), str) and "hunk" in ref
    }
    for ref in refs:
        path = ref.get("path")
        if not isinstance(path, str):
            # A ref with no sizeable path — a note-only ref, or a malformed one (``{}`` or
            # a non-string ``note``): its evidence exists but cannot be sized, so the total
            # becomes a floor rather than a fabricated-exact number. (A validated analysis
            # shouldn't carry a malformed ref, but the module's guarantee holds regardless
            # of input.)
            approximate = True
            continue
        entry = files_by_path.get(path)
        if entry is None:
            approximate = True
            continue
        if "hunk" in ref:
            hunk_id = ref["hunk"]
            if isinstance(hunk_id, bool) or not isinstance(hunk_id, int):
                # A hunk id is an int (analysis schema 0.4). A malformed one — a str, or
                # an unhashable list/dict that would crash the seen-set — can't be sized or
                # safely keyed, so it degrades to an approximate floor rather than raising.
                approximate = True
                continue
            hunk_key = (path, hunk_id)
            if hunk_key in seen_hunks:
                continue
            seen_hunks.add(hunk_key)
            result = hunk_reading_size(entry, hunk_id)
            if result is None:
                approximate = True
            else:
                lines, exact = result
                total += lines
                if not exact:
                    approximate = True
        else:
            if path in hunk_cited or path in seen_files:
                continue  # superseded by this step's hunk refs, or an already-counted file
            seen_files.add(path)
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
    """A compact lines label that never understates unmeasured evidence.

    Exact → ``24 lines``. Approximate with a measured floor → an explicit lower bound
    ``≥24 lines``. Approximate with **nothing** measured (note-only/unsized evidence, a
    floor of 0) → ``unsized`` — never ``~0 lines``, which would read as "negligible" when
    the truth is "we could not size it".
    """
    if weight.approximate and weight.lines == 0:
        return "unsized"
    unit = "line" if weight.lines == 1 else "lines"
    prefix = "≥" if weight.approximate else ""
    return f"{prefix}{weight.lines} {unit}"


def minutes_label(weight: StepWeight) -> str:
    """A compact reading-time label at reading pace that never fakes a sub-minute budget.

    Always a rough ``~5 min`` (``<1 min`` for a genuinely tiny load), or ``unknown`` when
    nothing could be measured — no time is estimated for evidence that carries no
    measurable lines. Deliberately **not** a strict ``≥`` bound even for an approximate
    weight: the minutes come from dividing the (integer, floor-honest) line count by an
    approximate pace and rounding *up* (:func:`reading_minutes`), so a ``≥`` claim would
    round the lower bound the wrong way (26 lines is ~1.04 min, not "at least 2"). The
    line count carries the honest ``≥`` floor; the time stays an estimate.
    """
    if weight.approximate and weight.lines == 0:
        return "unknown"
    minutes = reading_minutes(weight.lines)
    return f"~{minutes} min" if minutes >= 1 else "<1 min"


def weight_bucket(lines: int) -> str:
    """The Map-dot size tier (``w1``..``w4``) for a reading weight of ``lines``.

    See :data:`_WEIGHT_BUCKET_BOUNDS`: the renderer stamps this on the step so the Deck
    JS relays it verbatim onto the dot — the size policy is owned and tested here, not
    re-derived in the vendored script.
    """
    for bound, bucket in _WEIGHT_BUCKET_BOUNDS:
        if lines < bound:
            return bucket
    return "w4"


def dot_bucket(weight: StepWeight) -> str:
    """The Map-dot class for a step: its size tier, or ``unsized`` when the weight is an
    approximate floor of 0.

    A wholly-unsized stop (note-only/unsized evidence) has ``lines == 0``, which would
    otherwise size to ``w1`` — the *smallest* dot — reading as "trivial" exactly when the
    renderer is labelling its cost unknown. ``unsized`` gets a distinct stylesheet
    treatment instead, so the Map never shows an unmeasured stop as the lightest one.
    """
    if weight.approximate and weight.lines == 0:
        return "unsized"
    return weight_bucket(weight.lines)
