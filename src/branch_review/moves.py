"""Deterministic move detection — dimming relocated-but-identical diff lines (issue #106).

On refactor-heavy branches most reading time goes to code that only *moved*: a
200-line block lifted from one file into another, byte-identical but for its new
indentation. This module classifies, per line of each included hunk, whether an added
line is a relocation of a removed line elsewhere in the same changeset (in the spirit of
``git diff --color-moved=dimmed-zebra``), so the renderer can dim those lines and let the
few genuinely-changed lines inside a move pop out — mechanizing ADR-0016's "earn the
``behavior-preserving`` label" rule as a *visual* verify-then-skim.

The classification is **structural data, not prose**: it never crosses the Escape
Boundary and never touches the narrator's analysis. The collector runs the detection over
the whole changeset and records each hunk's relocated body-line positions in
``fragments.json``; the renderer relays them onto the hunk sections and the client diff
rebuild dims the matching rows. Detection finding nothing degrades to today's rendering
exactly (no ``moved`` key is written).

The identity rule
-----------------
Two lines are "the same" for move purposes iff their content — the text *after* the
unified-diff ``+``/``-`` marker — is equal once **leading and trailing whitespace is
stripped** (:func:`normalize_move_line`). This deliberately ignores indentation, which
almost always changes when a block is relocated (a different nesting depth), while keeping
*interior* whitespace significant — a reformatted line (``f( a,b )`` → ``f(a, b)``) is a
real edit, so it stays at full contrast rather than being dimmed as a move. A line that is
**blank after stripping never counts as a move**: blank lines are far too common to be
meaningful relocations, and dimming them would add noise without signal. The rule errs
toward *not* dimming when unsure — a missed dim only forgoes a convenience, whereas a
wrong dim invites skimming past a genuine change.

An added line is a relocation iff its normalized content also appears as a removed line
*somewhere in the changeset*, and vice versa — set membership across the union of all
included hunks (cross-file moves included). This is simpler than git's block matching and
can, like git's own short-line handling, dim a lone common line (a bare ``return``) that
happens to be both added and removed; the legend names the effect and the dimming is
reversible emphasis, so the reviewer can always verify. Determinism is total: the verdict
is a pure function of the diff bytes, independent of file or hunk ordering.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping

from branch_review.escape import hunk_body_lines, iter_hunks

# The whitespace/identity rule, in one sentence — the single source the module docstring,
# the renderer's legend tooltip, and any documentation reuse share, so the decision is
# stated identically wherever a reviewer meets it.
MOVE_IDENTITY_RULE = (
    "Lines count as relocated when identical after trimming leading/trailing whitespace "
    "(indentation is ignored; interior spacing and blank lines are not)."
)

# The reviewer-facing legend explaining the dimming — trusted tool prose (never narrator
# text), rendered by :mod:`branch_review.render` above the L3 evidence when any hunk moved.
MOVE_LEGEND = (
    "Dimmed lines are relocated unchanged from elsewhere in this diff — verify the move "
    "once, then skim, so genuine edits stand out."
)


def normalize_move_line(content: str) -> str:
    """The move-identity key for a diff line's content (its text after the +/- marker).

    Strips leading and trailing whitespace so a relocation that re-indents still matches;
    interior whitespace is left significant so a reformat is not mistaken for a move. The
    caller passes the content *without* the ``+``/``-`` prefix.
    """
    return content.strip()


def _classified_lines(hunk_text: str) -> Iterator[tuple[int, str, str]]:
    """Yield ``(position, marker, key)`` for each non-blank ``+``/``-`` line of a hunk body.

    ``position`` is the 0-based index into :func:`branch_review.escape.hunk_body_lines` (the
    body-line model the renderer's ``lines`` count and the client diff rebuild share, so a
    ``position`` here is the exact row ``data-moved`` dims), ``marker`` is ``"+"`` or ``"-"``,
    and ``key`` is the move-identity of the line's content (:func:`normalize_move_line`),
    never blank. The single place the split → marker-filter → normalize walk lives, so the
    added/removed set builder and the per-hunk locator classify lines identically by
    construction rather than by two copies kept in sync.
    """
    for position, line in enumerate(hunk_body_lines(hunk_text)):
        marker = line[:1]
        if marker not in ("+", "-"):
            continue
        key = normalize_move_line(line[1:])
        if key:
            yield position, marker, key


def _added_removed(diff_text: str) -> tuple[set[str], set[str]]:
    """One file's normalized non-blank ``(added, removed)`` line contents.

    Blank-after-strip lines are dropped by :func:`_classified_lines` (never movable), so
    they can neither seed nor satisfy a match.
    """
    added: set[str] = set()
    removed: set[str] = set()
    for _index, hunk_text in iter_hunks(diff_text):
        for _position, marker, key in _classified_lines(hunk_text):
            (added if marker == "+" else removed).add(key)
    return added, removed


def moved_content(diffs: Iterable[str]) -> frozenset[str]:
    """The changeset-wide set of normalized contents that are both added and removed.

    A content in this set was removed in one place and added in another — a relocation.
    Computed over the union of every included file's hunks, so a block moved *between*
    files is detected. Blank lines are excluded by :func:`_added_removed`.
    """
    added: set[str] = set()
    removed: set[str] = set()
    for diff_text in diffs:
        file_added, file_removed = _added_removed(diff_text)
        added |= file_added
        removed |= file_removed
    return frozenset(added & removed)


def moved_hunk_lines(diff_text: str, moved: frozenset[str]) -> dict[int, list[int]]:
    """Per-hunk relocated body-line positions for one file, given the changeset ``moved`` set.

    Returns ``{hunk_1based_index: [body_line_position, ...]}`` — a body-line position is a
    0-based index into :func:`branch_review.escape.hunk_body_lines` (the body-line model the
    renderer's ``lines`` count and the client diff rebuild share, so the renderer can address
    the exact row). A position is included when its line is a non-blank ``+``/``-`` whose
    normalized content is in ``moved``. Only hunks with at least one relocated line appear,
    so a moveless file yields ``{}`` and the manifest gains no ``moved`` keys (degrade to
    today exactly).
    """
    result: dict[int, list[int]] = {}
    for index, hunk_text in iter_hunks(diff_text):
        positions = [pos for pos, _marker, key in _classified_lines(hunk_text) if key in moved]
        if positions:
            result[index] = positions
    return result


def detect_moves(diffs: Mapping[str, str]) -> dict[str, dict[int, list[int]]]:
    """Classify relocated lines across a whole changeset of per-file diffs.

    ``diffs`` maps an opaque per-file key (the collector's fragment id) to that file's
    unified diff. Returns ``{key: {hunk_index: [body_positions]}}`` carrying only the
    files and hunks that have at least one relocated line — a moveless changeset returns
    ``{}``. The two-pass shape (build the changeset ``moved`` set, then locate it per
    hunk) is what makes a cross-file relocation dim on *both* ends.
    """
    moved = moved_content(diffs.values())
    if not moved:
        return {}
    classified: dict[str, dict[int, list[int]]] = {}
    for key, diff_text in diffs.items():
        per_hunk = moved_hunk_lines(diff_text, moved)
        if per_hunk:
            classified[key] = per_hunk
    return classified
