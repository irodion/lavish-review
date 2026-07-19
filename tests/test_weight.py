"""Unit tests for the derived reading-weight module (issue #100).

Same inputs → same outputs: every case here pins a deterministic weight derived from a
fragments-manifest entry, so a change to the contribution rule is a visible test edit.
"""

from __future__ import annotations

from branch_review.escape import fragment
from branch_review.weight import (
    FILE_LEVEL_CAP,
    LINES_PER_MINUTE,
    StepWeight,
    file_change_size,
    file_ref_weight,
    hunk_line_count,
    lines_label,
    minutes_label,
    reading_minutes,
    rollup,
    step_weight,
)


def _file(path: str, *, added: int = 0, deleted: int = 0, omitted: bool = False, hunks=None):
    """A fragments-manifest file entry, shaped like the collector's output."""
    entry: dict[str, object] = {
        "path": path,
        "added": added,
        "deleted": deleted,
        "omitted": omitted,
    }
    if hunks is not None:
        entry["hunks"] = hunks
    return entry


def _hunk(index: int, header: str):
    """A manifest hunk entry — header crossed through the Escape Boundary like the real one."""
    return {"index": index, "anchor": f"hunk-x-{index}", "header_html": fragment(header)}


# --- Hunk line counts ----------------------------------------------------------


def test_hunk_line_count_reads_the_larger_side_of_the_header() -> None:
    entry = _file("a.py", hunks=[_hunk(1, "@@ -1,18 +1,21 @@")])
    assert hunk_line_count(entry, 1) == 21


def test_hunk_line_count_single_line_form_counts_as_one() -> None:
    entry = _file("a.py", hunks=[_hunk(1, "@@ -5 +5 @@")])
    assert hunk_line_count(entry, 1) == 1


def test_hunk_line_count_new_file_hunk() -> None:
    entry = _file("a.py", hunks=[_hunk(1, "@@ -0,0 +1,40 @@")])
    assert hunk_line_count(entry, 1) == 40


def test_hunk_line_count_reads_header_with_a_function_heading_suffix() -> None:
    # git appends the enclosing function to the header; parsing must ignore the suffix.
    entry = _file("a.py", hunks=[_hunk(2, "@@ -10,3 +10,7 @@ def handler(self):")])
    assert hunk_line_count(entry, 2) == 7


def test_hunk_line_count_missing_hunk_is_none() -> None:
    entry = _file("a.py", hunks=[_hunk(1, "@@ -1,2 +1,2 @@")])
    assert hunk_line_count(entry, 3) is None


def test_hunk_line_count_unparseable_header_is_none() -> None:
    entry = _file("a.py", hunks=[_hunk(1, "@@")])  # the degenerate header form
    assert hunk_line_count(entry, 1) is None


def test_hunk_line_count_no_hunks_key_is_none() -> None:
    assert hunk_line_count(_file("a.py"), 1) is None


# --- File-level contribution ---------------------------------------------------


def test_file_change_size_sums_added_and_deleted() -> None:
    assert file_change_size(_file("a.py", added=12, deleted=3)) == 15


def test_file_change_size_tolerates_missing_and_bool_stats() -> None:
    assert file_change_size({"path": "a.py"}) == 0
    # A stray bool must not be read as int 1 (bool is an int subclass in Python).
    assert file_change_size({"path": "a.py", "added": True, "deleted": 5}) == 5


def test_file_ref_weight_caps_large_files() -> None:
    assert file_ref_weight(_file("gen.py", added=900, deleted=100)) == FILE_LEVEL_CAP
    assert file_ref_weight(_file("small.py", added=6, deleted=2)) == 8


# --- Step weight ---------------------------------------------------------------


def test_step_weight_from_a_hunk_ref_is_exact() -> None:
    files = {"a.py": _file("a.py", hunks=[_hunk(1, "@@ -1,18 +1,21 @@")])}
    assert step_weight([{"path": "a.py", "hunk": 1}], files) == StepWeight(21, False)


def test_step_weight_from_a_file_ref_is_capped_and_exact() -> None:
    files = {"a.py": _file("a.py", added=6, deleted=2)}
    assert step_weight([{"path": "a.py"}], files) == StepWeight(8, False)


def test_step_weight_note_only_is_zero_and_approximate() -> None:
    assert step_weight([{"note": "poetry.lock churn omitted"}], {}) == StepWeight(0, True)


def test_step_weight_hunk_with_a_note_is_sized_by_the_hunk_not_flagged() -> None:
    # The example fixture's shape: one ref carrying path + hunk + an explanatory note.
    files = {"a.py": _file("a.py", hunks=[_hunk(1, "@@ -1,4 +1,10 @@")])}
    weight = step_weight([{"path": "a.py", "hunk": 1, "note": "the changed handler"}], files)
    assert weight == StepWeight(10, False)


def test_step_weight_omitted_body_file_is_sized_from_stats_and_flagged() -> None:
    files = {"lock": _file("lock", added=20, deleted=10, omitted=True)}
    # 30 changed lines, capped to FILE_LEVEL_CAP is still 30 here, but flagged a floor.
    assert step_weight([{"path": "lock"}], files) == StepWeight(30, True)


def test_step_weight_unknown_path_contributes_nothing_but_flags() -> None:
    assert step_weight([{"path": "ghost.py", "hunk": 1}], {}) == StepWeight(0, True)


def test_step_weight_unparseable_hunk_flags_without_counting() -> None:
    files = {"a.py": _file("a.py", hunks=[_hunk(1, "@@")])}
    assert step_weight([{"path": "a.py", "hunk": 1}], files) == StepWeight(0, True)


def test_step_weight_sums_multiple_refs_and_dedupes() -> None:
    files = {
        "a.py": _file(
            "a.py", hunks=[_hunk(1, "@@ -1,5 +1,5 @@"), _hunk(2, "@@ -20,2 +20,9 @@")]
        ),
        "b.py": _file("b.py", added=4, deleted=0),
    }
    evidence = [
        {"path": "a.py", "hunk": 1},  # 5
        {"path": "a.py", "hunk": 2},  # 9
        {"path": "a.py", "hunk": 1},  # duplicate — not counted again
        {"path": "b.py"},  # 4
        {"path": "b.py"},  # duplicate file-level — not counted again
    ]
    assert step_weight(evidence, files) == StepWeight(18, False)


def test_step_weight_handles_non_list_evidence() -> None:
    assert step_weight(None, {}) == StepWeight(0, False)
    assert step_weight("nope", {}) == StepWeight(0, False)


# --- Rollups and time ----------------------------------------------------------


def test_rollup_sums_and_is_approximate_if_any_part_is() -> None:
    assert rollup([StepWeight(10, False), StepWeight(5, False)]) == StepWeight(15, False)
    assert rollup([StepWeight(10, False), StepWeight(5, True)]) == StepWeight(15, True)
    assert rollup([]) == StepWeight(0, False)


def test_reading_minutes_rounds_up_from_the_stated_pace() -> None:
    assert reading_minutes(0) == 0
    assert reading_minutes(1) == 1
    assert reading_minutes(LINES_PER_MINUTE) == 1
    assert reading_minutes(LINES_PER_MINUTE + 1) == 2
    assert reading_minutes(LINES_PER_MINUTE * 4) == 4


# --- Labels --------------------------------------------------------------------


def test_lines_label_pluralizes_and_marks_approximate() -> None:
    assert lines_label(StepWeight(1, False)) == "1 line"
    assert lines_label(StepWeight(24, False)) == "24 lines"
    assert lines_label(StepWeight(24, True)) == "~24 lines"
    assert lines_label(StepWeight(0, True)) == "~0 lines"


def test_minutes_label_floors_below_a_minute() -> None:
    assert minutes_label(0) == "<1 min"
    assert minutes_label(LINES_PER_MINUTE) == "~1 min"
    assert minutes_label(LINES_PER_MINUTE * 3 + 1) == "~4 min"
