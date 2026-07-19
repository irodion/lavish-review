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
    dot_bucket,
    file_change_size,
    file_ref_weight,
    hunk_reading_size,
    lines_label,
    minutes_label,
    reading_minutes,
    rollup,
    step_weight,
    weight_bucket,
)


def _file(
    path: str,
    *,
    added: int = 0,
    deleted: int = 0,
    omitted: bool = False,
    hunks: list[dict[str, object]] | None = None,
) -> dict[str, object]:
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


def _hunk(index: int, header: str, lines: int | None = None) -> dict[str, object]:
    """A manifest hunk entry — header crossed through the Escape Boundary like the real one.

    ``lines`` is the collector's exact per-hunk count (issue #100); omit it to model an
    older manifest that carries only the header (the approximate floor fallback).
    """
    entry: dict[str, object] = {
        "index": index,
        "anchor": f"hunk-x-{index}",
        "header_html": fragment(header),
    }
    if lines is not None:
        entry["lines"] = lines
    return entry


# --- Hunk reading size ---------------------------------------------------------


def test_hunk_reading_size_prefers_the_exact_collector_count() -> None:
    # Exact `lines` wins over the header — and a modify-in-place hunk proves it matters:
    # the header's max(18, 21) = 21 would undercount a 24-line body.
    entry = _file("a.py", hunks=[_hunk(1, "@@ -1,18 +1,21 @@", lines=24)])
    assert hunk_reading_size(entry, 1) == (24, True)


def test_hunk_reading_size_falls_back_to_header_floor_when_no_exact_count() -> None:
    # An older manifest carries only the header → max(18, 21) = 21, flagged inexact.
    entry = _file("a.py", hunks=[_hunk(1, "@@ -1,18 +1,21 @@")])
    assert hunk_reading_size(entry, 1) == (21, False)


def test_hunk_reading_size_header_single_line_form_counts_as_one() -> None:
    assert hunk_reading_size(_file("a.py", hunks=[_hunk(1, "@@ -5 +5 @@")]), 1) == (1, False)


def test_hunk_reading_size_header_new_file_hunk() -> None:
    assert hunk_reading_size(_file("a.py", hunks=[_hunk(1, "@@ -0,0 +1,40 @@")]), 1) == (40, False)


def test_hunk_reading_size_reads_header_with_a_function_heading_suffix() -> None:
    # git appends the enclosing function to the header; parsing must ignore the suffix.
    entry = _file("a.py", hunks=[_hunk(2, "@@ -10,3 +10,7 @@ def handler(self):")])
    assert hunk_reading_size(entry, 2) == (7, False)


def test_hunk_reading_size_zero_exact_count_is_honored() -> None:
    # A valid 0-line hunk (exact) is not confused with "absent" — it returns (0, True).
    entry = _file("a.py", hunks=[_hunk(1, "@@ -1,18 +1,21 @@", lines=0)])
    assert hunk_reading_size(entry, 1) == (0, True)


def test_hunk_reading_size_missing_hunk_is_none() -> None:
    entry = _file("a.py", hunks=[_hunk(1, "@@ -1,2 +1,2 @@", lines=2)])
    assert hunk_reading_size(entry, 3) is None


def test_hunk_reading_size_unparseable_header_and_no_count_is_none() -> None:
    entry = _file("a.py", hunks=[_hunk(1, "@@")])  # the degenerate header form, no lines
    assert hunk_reading_size(entry, 1) is None


def test_hunk_reading_size_no_hunks_key_is_none() -> None:
    assert hunk_reading_size(_file("a.py"), 1) is None


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
    files = {"a.py": _file("a.py", hunks=[_hunk(1, "@@ -1,18 +1,21 @@", lines=24)])}
    assert step_weight([{"path": "a.py", "hunk": 1}], files) == StepWeight(24, False)


def test_step_weight_header_only_hunk_is_a_flagged_floor() -> None:
    # No exact `lines` (older manifest) → the header's max(18, 21) = 21, marked a floor.
    files = {"a.py": _file("a.py", hunks=[_hunk(1, "@@ -1,18 +1,21 @@")])}
    assert step_weight([{"path": "a.py", "hunk": 1}], files) == StepWeight(21, True)


def test_step_weight_from_a_file_ref_is_capped_and_exact() -> None:
    files = {"a.py": _file("a.py", added=6, deleted=2)}
    assert step_weight([{"path": "a.py"}], files) == StepWeight(8, False)


def test_step_weight_note_only_is_zero_and_approximate() -> None:
    assert step_weight([{"note": "poetry.lock churn omitted"}], {}) == StepWeight(0, True)


def test_step_weight_malformed_ref_still_flags_approximate() -> None:
    # A ref with no path and no string note ({} or a non-string note) is unsizable
    # evidence: it must make the total a floor, never a fabricated-exact 0.
    assert step_weight([{}], {}) == StepWeight(0, True)
    assert step_weight([{"note": 123}], {}) == StepWeight(0, True)


def test_step_weight_hunk_with_a_note_is_sized_by_the_hunk_not_flagged() -> None:
    # The example fixture's shape: one ref carrying path + hunk + an explanatory note.
    files = {"a.py": _file("a.py", hunks=[_hunk(1, "@@ -1,4 +1,10 @@", lines=13)])}
    weight = step_weight([{"path": "a.py", "hunk": 1, "note": "the changed handler"}], files)
    assert weight == StepWeight(13, False)


def test_step_weight_omitted_body_file_is_sized_from_stats_and_flagged() -> None:
    files = {"lock": _file("lock", added=20, deleted=10, omitted=True)}
    # 30 changed lines, capped to FILE_LEVEL_CAP is still 30 here, but flagged a floor.
    assert step_weight([{"path": "lock"}], files) == StepWeight(30, True)


def test_step_weight_unknown_path_contributes_nothing_but_flags() -> None:
    assert step_weight([{"path": "ghost.py", "hunk": 1}], {}) == StepWeight(0, True)


def test_step_weight_unparseable_hunk_flags_without_counting() -> None:
    files = {"a.py": _file("a.py", hunks=[_hunk(1, "@@")])}
    assert step_weight([{"path": "a.py", "hunk": 1}], files) == StepWeight(0, True)


def test_step_weight_file_level_ref_superseded_by_a_hunk_ref_to_the_same_file() -> None:
    # A step that cites both the whole file and a specific hunk of it must not count the
    # file's lines twice — the precise hunk supersedes the file-level ref (finding #2).
    files = {"a.py": _file("a.py", added=30, deleted=10, hunks=[_hunk(1, "@@", lines=12)])}
    weight = step_weight([{"path": "a.py"}, {"path": "a.py", "hunk": 1}], files)
    assert weight == StepWeight(12, False)  # only the hunk's 12 lines, not 12 + capped-40


def test_step_weight_sums_multiple_refs_and_dedupes() -> None:
    files = {
        "a.py": _file(
            "a.py",
            hunks=[_hunk(1, "@@ -1,5 +1,5 @@", lines=5), _hunk(2, "@@ -20,2 +20,9 @@", lines=9)],
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


def test_rollup_counts_evidence_re_cited_across_steps_per_visit() -> None:
    # Two steps citing the same hunk each carry its reading load: a route budget sums per
    # step-visit (the reviewer reads it at each stop), so this is by design, not a bug —
    # the cross-step total is not deduped, and it is still exact (not a floor).
    files = {"a.py": _file("a.py", hunks=[_hunk(1, "@@", lines=20)])}
    ref = [{"path": "a.py", "hunk": 1}]
    assert rollup([step_weight(ref, files), step_weight(ref, files)]) == StepWeight(40, False)


def test_reading_minutes_rounds_up_from_the_stated_pace() -> None:
    assert reading_minutes(0) == 0
    assert reading_minutes(1) == 1
    assert reading_minutes(LINES_PER_MINUTE) == 1
    assert reading_minutes(LINES_PER_MINUTE + 1) == 2
    assert reading_minutes(LINES_PER_MINUTE * 4) == 4


# --- Labels --------------------------------------------------------------------


def test_lines_label_marks_lower_bounds_and_unsized_evidence() -> None:
    assert lines_label(StepWeight(1, False)) == "1 line"
    assert lines_label(StepWeight(24, False)) == "24 lines"
    # Approximate but measured → an explicit lower bound, not a vague "~".
    assert lines_label(StepWeight(24, True)) == "≥24 lines"
    # Approximate with nothing measured → "unsized", never "~0 lines" (which reads as
    # negligible when the truth is "could not be sized").
    assert lines_label(StepWeight(0, True)) == "unsized"
    # Exact zero (a step citing no sizeable evidence) is a genuine zero, not unsized.
    assert lines_label(StepWeight(0, False)) == "0 lines"


def test_minutes_label_is_a_rough_estimate_never_a_rounded_up_lower_bound() -> None:
    assert minutes_label(StepWeight(0, False)) == "<1 min"
    assert minutes_label(StepWeight(LINES_PER_MINUTE, False)) == "~1 min"
    assert minutes_label(StepWeight(LINES_PER_MINUTE * 3 + 1, False)) == "~4 min"
    # Approximate stays a rough "~" estimate — never "≥N min", which (with ceil rounding)
    # would round the lower bound upward: 26 lines is ~1.04 min, not "at least 2".
    assert minutes_label(StepWeight(LINES_PER_MINUTE + 1, True)) == "~2 min"
    assert minutes_label(StepWeight(5, True)) == "~1 min"
    # Approximate with nothing measurable → "unknown", not a sub-minute figure.
    assert minutes_label(StepWeight(0, True)) == "unknown"


def test_dot_bucket_never_sizes_an_unsized_stop_as_the_smallest_dot() -> None:
    assert dot_bucket(StepWeight(8, False)) == "w1"
    assert dot_bucket(StepWeight(200, False)) == "w4"
    # Approximate but measured → a real size tier from its floor.
    assert dot_bucket(StepWeight(200, True)) == "w4"
    # An exact zero (a step citing nothing to read) is a genuine smallest dot.
    assert dot_bucket(StepWeight(0, False)) == "w1"
    # A wholly-unsized weight (approximate floor of 0) is "unsized", NOT the w1 smallest —
    # it must not read as the lightest stop when its cost is actually unknown.
    assert dot_bucket(StepWeight(0, True)) == "unsized"


# --- Map-dot size tier (Python-owned policy the Deck JS relays verbatim) -------


def test_weight_bucket_maps_lines_to_a_size_tier() -> None:
    # The boundary policy lives here (unit-tested) rather than in the vendored JS.
    assert weight_bucket(0) == "w1"
    assert weight_bucket(14) == "w1"
    assert weight_bucket(15) == "w2"
    assert weight_bucket(49) == "w2"
    assert weight_bucket(50) == "w3"
    assert weight_bucket(149) == "w3"
    assert weight_bucket(150) == "w4"
    assert weight_bucket(5000) == "w4"
