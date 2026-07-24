"""Unit tests for the narrated-hunk coverage policy (issue #104).

These pin the counting rule the ticket decides: hunk-anchored refs narrate a hunk; a
file-level ref is counted distinctly as file-blanket, never folded into the hunk figure;
an omitted body carries no hunks and is never counted. The renderer's HTML representation
of these numbers is covered in ``test_render.py``.
"""

from __future__ import annotations

from branch_review.coverage import (
    COVERAGE_RULE,
    Coverage,
    UnnarratedFile,
    UnnarratedHunk,
    compute_coverage,
    coverage_headline,
)


def _file(path: str, hunk_indices: list[int], **extra: object) -> dict[str, object]:
    """A manifest file entry with hunks at the given 1-based indices."""
    return {
        "path": path,
        "id": path.replace("/", "_"),
        "omitted": False,
        "added": 3,
        "deleted": 1,
        "hunks": [{"index": i, "anchor": f"hunk-{path}-{i}"} for i in hunk_indices],
        **extra,
    }


def test_hunk_anchored_refs_are_the_narrated_headline() -> None:
    files = [_file("a.py", [1, 2, 3])]
    by_hunk = {"hunk-a.py-1": ["t1.s1"], "hunk-a.py-3": ["t1.s2"]}
    cov = compute_coverage(files, by_hunk, {})

    assert cov.total_hunks == 3
    assert cov.narrated_hunks == 2  # hunks 1 and 3
    assert cov.unnarrated_hunks == 1  # hunk 2
    assert cov.blanket_hunks == 0
    assert cov.has_unnarrated is True
    # The one bare hunk is grouped under its file, with its anchor for the queue link.
    assert cov.files == (UnnarratedFile("a.py", (UnnarratedHunk(2, "hunk-a.py-2"),), ()),)


def test_file_level_ref_is_counted_distinctly_as_blanket_never_as_hunk_narration() -> None:
    # a.py is cited only at file level: none of its hunks is hunk-anchored, so the headline
    # narrated count stays 0 (precision is never overstated) while every hunk is blanketed.
    files = [_file("a.py", [1, 2])]
    cov = compute_coverage(files, {}, {"a.py": ["t1.s2"]})

    assert cov.narrated_hunks == 0  # the file-level ref never inflates the hunk figure
    assert cov.unnarrated_hunks == 2
    assert cov.blanket_hunks == 2  # both bare hunks fall under the file-level citation
    # The file carries its blanket narrators so the queue can note them beside its hunks.
    assert cov.files[0].file_steps == ("t1.s2",)
    assert [h.index for h in cov.files[0].hunks] == [1, 2]


def test_hunk_and_file_level_refs_on_the_same_file_partition_cleanly() -> None:
    # hunk 1 hunk-anchored; hunks 2 and 3 only under the file-level blanket.
    files = [_file("a.py", [1, 2, 3])]
    cov = compute_coverage(files, {"hunk-a.py-1": ["t1.s1"]}, {"a.py": ["t1.s1"]})

    assert cov.narrated_hunks == 1
    assert cov.unnarrated_hunks == 2
    assert cov.blanket_hunks == 2
    assert [h.index for h in cov.files[0].hunks] == [2, 3]


def test_omitted_body_files_contribute_no_hunks_and_are_never_counted() -> None:
    files = [
        _file("a.py", [1]),
        {
            "path": "uv.lock",
            "id": "uv_lock",
            "omitted": True,
            "reason": "lockfile body omitted",
            "added": 40,
            "deleted": 10,
        },
    ]
    # Even a file-level ref at the omitted file cannot make it count — it has no hunks.
    cov = compute_coverage(files, {"hunk-a.py-1": ["t1.s1"]}, {"uv.lock": ["t1.s3"]})

    assert cov.total_hunks == 1  # only a.py's hunk; the lockfile contributes nothing
    assert cov.narrated_hunks == 1
    assert cov.blanket_hunks == 0
    assert all(f.path != "uv.lock" for f in cov.files)


def test_files_with_no_hunks_do_not_appear_in_the_queue() -> None:
    # A pure rename / mode-only change is an included file with an empty hunk list.
    files = [_file("renamed.py", []), _file("a.py", [1])]
    cov = compute_coverage(files, {}, {})

    assert cov.total_hunks == 1
    assert [f.path for f in cov.files] == ["a.py"]  # renamed.py has nothing bare to list


def test_fully_narrated_change_has_an_empty_queue() -> None:
    files = [_file("a.py", [1, 2])]
    by_hunk = {"hunk-a.py-1": ["t1.s1"], "hunk-a.py-2": ["t1.s1"]}
    cov = compute_coverage(files, by_hunk, {})

    assert cov.unnarrated_hunks == 0
    assert cov.has_unnarrated is False
    assert cov.files == ()
    assert cov.percent_narrated == 100


def test_percent_narrated_is_none_when_there_are_no_hunks() -> None:
    cov = compute_coverage([], {}, {})
    assert cov.total_hunks == 0
    assert cov.percent_narrated is None  # never a slanderous 0% for nothing to narrate
    assert coverage_headline(cov) == "no hunks to narrate"


def test_percent_narrated_rounds() -> None:
    assert Coverage(397, 70).percent_narrated == 18  # 17.6% → 18
    assert Coverage(3, 1).percent_narrated == 33
    assert Coverage(1, 0).percent_narrated == 0


def test_coverage_headline_singular_and_plural() -> None:
    assert coverage_headline(Coverage(1, 1)) == "1 of 1 hunk narrated"
    assert coverage_headline(Coverage(5, 2)) == "2 of 5 hunks narrated"


def test_files_are_grouped_in_manifest_order() -> None:
    files = [_file("z.py", [1]), _file("a.py", [1])]
    cov = compute_coverage(files, {}, {})
    assert [f.path for f in cov.files] == ["z.py", "a.py"]


def test_malformed_hunk_entries_are_skipped_without_inflating_the_total() -> None:
    files = [
        {
            "path": "a.py",
            "omitted": False,
            "hunks": [
                {"index": 1, "anchor": "hunk-a.py-1"},
                {"index": 2},  # no anchor — cannot be linked, so not counted
                "not-a-mapping",
            ],
        }
    ]
    cov = compute_coverage(files, {"hunk-a.py-1": ["t1.s1"]}, {})
    assert cov.total_hunks == 1  # only the well-formed, anchorable hunk
    assert cov.narrated_hunks == 1


def test_the_counting_rule_statement_names_the_three_cases() -> None:
    # The UI quotes COVERAGE_RULE; it must state the hunk rule, the file-level rule, and
    # the omitted-body rule — the three the ticket decides.
    assert "exact hunk" in COVERAGE_RULE
    assert "file-level" in COVERAGE_RULE
    assert "Omitted-body" in COVERAGE_RULE
