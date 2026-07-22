"""Narrated-hunk coverage — what fraction of the diff the narration accounts for (issue #104).

"16/16 steps reviewed" can still mean a fifth of the diff was ever pointed at: a step's
disposition attests the reviewer read *that step*, not that the narration reaches every
changed hunk. This module derives, deterministically from the reverse narration index
(``render._narration_index``) and the fragments manifest, how many of the changed hunks a
Review Step actually anchors — and collects the ones no step does, grouped by file, for the
generated **Un-narrated changes** queue. Like the reading weight beside it
(:mod:`branch_review.weight`), it is **derived at render time, never authored** (the
derived-over-authored principle, ADR-0016): the isolated narrator never sees or writes a
coverage number, and the same collected fragments always produce the same coverage.

The counting rule — the explicit decision issue #104 makes:

* A hunk counts as **narrated** only when a Review Step's evidence anchors that *exact*
  hunk (a ``{path, hunk}`` ref — the reverse index's ``by_hunk``). This is the headline
  figure: precise, and **never inflated**.
* A **file-level** citation (``{path}`` with no ``hunk``) narrates the file *broadly*. It
  does **not** count any one of that file's hunks as narrated — that would overstate
  per-hunk precision — so those hunks stay individually un-narrated (exactly as their #103
  margin already reads). Instead it is reported **distinctly** as **file-blanket** coverage
  over the file's un-narrated hunks, so a reviewer sweeping the queue sees which bare hunks
  at least fall under a whole-file citation.
* An **omitted-body** file carries no hunks (its body is gone; the classifier keeps only
  its stats), so it contributes nothing to the total and can **never** be counted as
  narrated. It stays reported by existence + stats in the L3 files section (nothing-hidden),
  never here.

So the changed hunks partition cleanly: ``narrated`` (hunk-anchored) + ``un-narrated``
(``total − narrated``); and of the un-narrated, ``blanket`` fall under a file-level
citation and the rest are wholly bare. The headline is ``narrated / total``; the un-narrated
count matches the #103 per-hunk ``un-narrated`` markers exactly (every bare hunk shows one
and appears in the queue); the file-blanket count is the distinct refinement.

Pure policy, no I/O — :mod:`branch_review.render` owns the HTML representation and the
``by_hunk``/``by_file`` index it feeds in; this module owns every number and grouping it
shows, and :func:`COVERAGE_RULE` is the one home for the rule statement the UI prints.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

# The one-sentence statement of the counting rule, printed in the cockpit (the acceptance
# criterion: the file-level rule is *stated in the UI*). Kept here so the rule and its
# derivation live in one module and the renderer/tests quote a single source.
COVERAGE_RULE = (
    "A hunk counts as narrated only when a Review Step cites that exact hunk; a file-level "
    "citation narrates a file broadly and is noted below, but never counts a hunk as "
    "individually narrated. Omitted-body files carry no hunks and are never counted."
)


@dataclass(frozen=True)
class UnnarratedHunk:
    """One un-narrated hunk of a file — the identifiers the queue links to its L3 location.

    ``index`` is the hunk's 1-based position (its "hunk N" label); ``anchor`` is its
    :func:`branch_review.escape.hunk_anchor_id` element id, the ``#`` target of the queue
    link. The renderer pulls the display header from the manifest entry — this stays pure.
    """

    index: int
    anchor: str


@dataclass(frozen=True)
class UnnarratedFile:
    """A changed file's un-narrated hunks, with any file-level (blanket) narrators.

    ``path`` keys back into the renderer's ``files_by_path`` for the escaped path/anchor/
    stats. ``hunks`` are the file's un-narrated hunks in diff order. ``file_steps`` are the
    step ids that cite the file at *file* level (a ``{path}`` ref) — the blanket narration
    the queue notes beside the file so its bare hunks aren't read as wholly un-accounted-for.
    """

    path: str
    hunks: tuple[UnnarratedHunk, ...]
    file_steps: tuple[str, ...] = ()


@dataclass(frozen=True)
class Coverage:
    """The derived narrated-hunk coverage of a change (issue #104).

    ``total_hunks`` is every hunk in an included (non-omitted) file; ``narrated_hunks`` the
    subset a step anchors directly; ``blanket_hunks`` the un-narrated hunks that fall under
    a file-level citation. ``files`` is the per-file un-narrated grouping (only files with
    ≥1 bare hunk, in manifest order) the Un-narrated changes queue renders.
    """

    total_hunks: int
    narrated_hunks: int
    blanket_hunks: int
    files: tuple[UnnarratedFile, ...] = ()

    @property
    def unnarrated_hunks(self) -> int:
        """Hunks no step anchors — ``total − narrated`` (== the count of #103 markers)."""
        return self.total_hunks - self.narrated_hunks

    @property
    def has_unnarrated(self) -> bool:
        """Whether any hunk is un-narrated — the gate for rendering the queue + its link."""
        return self.unnarrated_hunks > 0

    @property
    def percent_narrated(self) -> int | None:
        """Whole-percent hunk-anchored coverage, or ``None`` when there are no hunks to size.

        ``None`` (not ``0``) for a change with no hunks — a pure-rename/omitted-only range
        has nothing to narrate, so a "0%" would be a slander, not a measurement.
        """
        if self.total_hunks == 0:
            return None
        return round(100 * self.narrated_hunks / self.total_hunks)


def _hunk_anchor(hunk: Mapping[str, object]) -> str:
    anchor = hunk.get("anchor")
    return anchor if isinstance(anchor, str) else ""


def _hunk_index(hunk: Mapping[str, object], fallback: int) -> int:
    index = hunk.get("index")
    if isinstance(index, bool) or not isinstance(index, int):
        return fallback
    return index


def compute_coverage(
    files: Sequence[Mapping[str, object]],
    by_hunk: Mapping[str, Sequence[str]],
    by_file: Mapping[str, Sequence[str]],
) -> Coverage:
    """Derive :class:`Coverage` from the manifest files and the reverse narration index.

    ``files`` is the ordered ``fragments.json`` file list; ``by_hunk`` maps a hunk element
    id to the step ids that anchor it, ``by_file`` a file path to the step ids that cite it
    at file level (both from ``render._narration_index``). The manifest is the source of
    truth for which hunks exist — a stray ``by_hunk`` key that names no manifest hunk is
    simply never counted, so coverage can only ever be driven by real changed hunks.
    """
    total = 0
    narrated = 0
    blanket = 0
    unnarrated_files: list[UnnarratedFile] = []
    for entry in files:
        # An omitted body has no hunks to narrate — its stats show in the L3 files section
        # (nothing-hidden), but it can never be narrated, so it contributes nothing here.
        if entry.get("omitted") is True:
            continue
        hunks = entry.get("hunks")
        if not isinstance(hunks, list):
            continue
        path = entry.get("path")
        if not isinstance(path, str):
            continue
        file_steps = tuple(by_file.get(path, ()))
        bare: list[UnnarratedHunk] = []
        for position, hunk in enumerate(hunks, start=1):
            if not isinstance(hunk, Mapping):
                continue
            anchor = _hunk_anchor(hunk)
            if not anchor:
                continue
            total += 1
            if anchor in by_hunk:
                narrated += 1
            else:
                bare.append(UnnarratedHunk(index=_hunk_index(hunk, position), anchor=anchor))
                if file_steps:
                    blanket += 1
        if bare:
            unnarrated_files.append(
                UnnarratedFile(path=path, hunks=tuple(bare), file_steps=file_steps)
            )
    return Coverage(
        total_hunks=total,
        narrated_hunks=narrated,
        blanket_hunks=blanket,
        files=tuple(unnarrated_files),
    )


def coverage_headline(coverage: Coverage) -> str:
    """``70 of 397 hunks narrated`` — the compact figure L0 and the Map both show.

    Relayed verbatim onto the Map (the renderer stamps it on ``section.l0`` as
    ``data-coverage-label``; the deck reads it, never re-deriving the count — the same
    Python-owned-policy/relay posture as the route budgets). A change with no hunks reads
    an honest ``no hunks to narrate`` rather than a ``0 of 0``.
    """
    if coverage.total_hunks == 0:
        return "no hunks to narrate"
    unit = "hunk" if coverage.total_hunks == 1 else "hunks"
    return f"{coverage.narrated_hunks} of {coverage.total_hunks} {unit} narrated"
