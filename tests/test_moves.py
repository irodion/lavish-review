"""Tests for the deterministic move detector (issue #106).

The detector's contract is a pure function of the changeset's diff bytes: an added line
is a relocation iff its content (whitespace-trimmed, non-blank) also appears as a removed
line somewhere in the changeset. These tests pin the three acceptance cases — pure
relocation, relocation-with-edit, no relocation — plus the decided whitespace/blank rule
and cross-file moves, all order-independent.
"""

from __future__ import annotations

from branch_review.moves import (
    detect_moves,
    moved_content,
    moved_hunk_lines,
    normalize_move_line,
)

# A block of four lines moved verbatim out of one file and into another. In ``a.py`` the
# four lines are removed (a single hunk); in ``b.py`` the same four are added.
_A_REMOVES = "@@ -1,5 +1,1 @@\n keep\n-alpha()\n-beta()\n-gamma()\n-delta()\n"
_B_ADDS = "@@ -1,1 +1,5 @@\n keep\n+alpha()\n+beta()\n+gamma()\n+delta()\n"


def test_normalize_ignores_indentation_keeps_interior() -> None:
    assert normalize_move_line("    alpha()") == "alpha()"
    assert normalize_move_line("alpha()  ") == "alpha()"
    # Interior spacing is significant — a reformat is a real edit, not a move.
    assert normalize_move_line("f( a, b )") != normalize_move_line("f(a, b)")


def test_pure_relocation_dims_both_ends() -> None:
    """A block moved between files: every line is relocated on the remove *and* add side."""
    moved = detect_moves({"a": _A_REMOVES, "b": _B_ADDS})
    # Body positions are 0-based into the lines after the @@ header:
    #   0=" keep", 1..4 = the four moved lines.
    assert moved == {"a": {1: [1, 2, 3, 4]}, "b": {1: [1, 2, 3, 4]}}


def test_relocation_with_edit_leaves_the_edit_at_full_contrast() -> None:
    """A moved block with one line edited: the identical lines dim, the edited one does not."""
    # ``beta()`` becomes ``beta(x)`` on relocation — its content matches nothing removed,
    # so it is genuine drift, not a move. The other three lines still relocate.
    b_edited = "@@ -1,1 +1,5 @@\n keep\n+alpha()\n+beta(x)\n+gamma()\n+delta()\n"
    moved = detect_moves({"a": _A_REMOVES, "b": b_edited})
    assert moved["b"][1] == [1, 3, 4]  # position 2 (beta(x)) is NOT dimmed
    assert moved["a"][1] == [1, 3, 4]  # beta() removed no longer has an added twin


def test_no_relocation_returns_empty() -> None:
    """A changeset that only adds and only deletes distinct content has no moves."""
    added_only = "@@ -1,1 +1,3 @@\n keep\n+brand_new_one()\n+brand_new_two()\n"
    deleted_only = "@@ -1,3 +1,1 @@\n keep\n-gone_one()\n-gone_two()\n"
    assert detect_moves({"a": added_only, "b": deleted_only}) == {}
    assert moved_content([added_only, deleted_only]) == frozenset()


def test_relocation_survives_reindentation() -> None:
    """The same code at a new nesting depth still counts as a move (indentation ignored)."""
    removed = "@@ -1,2 +1,1 @@\n keep\n-value = compute()\n"
    added = "@@ -1,1 +1,2 @@\n keep\n+        value = compute()\n"  # deeper indent
    moved = detect_moves({"a": removed, "b": added})
    assert moved == {"a": {1: [1]}, "b": {1: [1]}}


def test_blank_lines_never_count_as_moves() -> None:
    """A blank line added and removed is not a relocation — too common to be signal."""
    removed = "@@ -1,2 +1,1 @@\n keep\n-\n"
    added = "@@ -1,1 +1,2 @@\n keep\n+\n"
    assert detect_moves({"a": removed, "b": added}) == {}


def test_within_file_relocation() -> None:
    """A block that moves between two hunks of the *same* file dims in both hunks."""
    diff = (
        "@@ -1,3 +1,1 @@\n keep\n-shared_line()\n-second()\n"
        "@@ -20,1 +18,3 @@\n tail\n+shared_line()\n+second()\n"
    )
    moved = detect_moves({"f": diff})
    assert moved == {"f": {1: [1, 2], 2: [1, 2]}}


def test_moved_hunk_lines_is_order_independent() -> None:
    """The verdict depends only on content, not on which file the detector sees first."""
    forward = detect_moves({"a": _A_REMOVES, "b": _B_ADDS})
    reversed_order = detect_moves({"b": _B_ADDS, "a": _A_REMOVES})
    assert forward == reversed_order


def test_moved_hunk_lines_direct_against_a_known_set() -> None:
    """The per-file helper locates exactly the lines whose content is in the moved set."""
    moved = frozenset({"alpha()", "gamma()"})
    # Only alpha() (pos 1) and gamma() (pos 3) are in the set; beta()/delta() are not.
    per_hunk = moved_hunk_lines(_B_ADDS, moved)
    assert per_hunk == {1: [1, 3]}


def test_context_lines_are_never_moves() -> None:
    """A context line (unchanged, ` ` prefix) equal to a moved content is not dimmed."""
    # ``shared()`` is a context line in ``a`` but a genuine add/remove pair across b/c.
    a_context = "@@ -1,2 +1,2 @@\n shared()\n-x()\n+y_new()\n"
    b_removes = "@@ -1,1 +1,0 @@\n-shared()\n"
    c_adds = "@@ -1,0 +1,1 @@\n+shared()\n"
    moved = detect_moves({"a": a_context, "b": b_removes, "c": c_adds})
    # b and c relocate ``shared()``; a's context copy stays untouched.
    assert "a" not in moved
    assert moved["b"] == {1: [0]}
    assert moved["c"] == {1: [0]}
