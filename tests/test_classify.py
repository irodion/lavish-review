"""Tests for the Change Classifier (issue #7).

The classifier is pure policy — no git, no filesystem — so these are plain,
table-driven unit tests: every disposition is covered by a row in
``_DISPOSITION_CASES``, and the rest exercise the changeset guard, the
total-diff downgrade, and config extension/reset.
"""

from __future__ import annotations

import pytest

from branch_review.classify import (
    DEFAULT_MAX_FILE_DIFF_LINES,
    Classification,
    ClassifierConfig,
    Disposition,
    FileStats,
    classify,
    classify_changeset,
    classify_file,
    downgrade_to_listing,
)

_DEFAULT = ClassifierConfig()


# (label, path, stats, expected disposition) — one row per disposition, with
# several rows per omit reason so the precedence and the matchers are pinned.
_DISPOSITION_CASES: list[tuple[str, str, FileStats, Disposition]] = [
    # include-body: ordinary source files of every shape.
    ("normal source", "src/app.py", FileStats(added=10, deleted=2), Disposition.INCLUDE_BODY),
    ("nested source", "a/b/c/widget.ts", FileStats(added=1, deleted=0), Disposition.INCLUDE_BODY),
    ("at the cap", "big.py", FileStats(added=1500, deleted=0), Disposition.INCLUDE_BODY),
    ("binary never caps", "img.bin", FileStats(binary=True), Disposition.INCLUDE_BODY),
    # omit:lockfile — matched by exact basename, anywhere in the tree.
    ("npm lock", "package-lock.json", FileStats(added=900, deleted=5), Disposition.OMIT_LOCKFILE),
    ("uv lock", "uv.lock", FileStats(added=4000, deleted=4000), Disposition.OMIT_LOCKFILE),
    ("nested cargo lock", "crates/x/Cargo.lock", FileStats(added=3), Disposition.OMIT_LOCKFILE),
    ("go sum", "go.sum", FileStats(added=50), Disposition.OMIT_LOCKFILE),
    # omit:excluded — vendored dirs, generated/minified globs, linguist-generated.
    ("node_modules", "node_modules/lib/x.js", FileStats(added=9), Disposition.OMIT_EXCLUDED),
    ("vendored", "vendor/pkg/y.go", FileStats(added=9), Disposition.OMIT_EXCLUDED),
    ("third_party", "third_party/z/a.c", FileStats(added=9), Disposition.OMIT_EXCLUDED),
    ("dist build output", "dist/bundle.js", FileStats(added=9), Disposition.OMIT_EXCLUDED),
    ("minified js", "static/app.min.js", FileStats(added=9), Disposition.OMIT_EXCLUDED),
    ("generated glob", "api/types.generated.ts", FileStats(added=9), Disposition.OMIT_EXCLUDED),
    ("protobuf go", "rpc/svc.pb.go", FileStats(added=9), Disposition.OMIT_EXCLUDED),
    (
        "linguist-generated",
        "schema.py",
        FileStats(added=9, linguist_generated=True),
        Disposition.OMIT_EXCLUDED,
    ),
    # omit:too-large — over the per-file cap and not otherwise classified out.
    ("over cap", "huge.py", FileStats(added=1501, deleted=0), Disposition.OMIT_TOO_LARGE),
    ("over by churn", "churn.py", FileStats(added=800, deleted=800), Disposition.OMIT_TOO_LARGE),
]


@pytest.mark.parametrize(
    ("path", "stats", "expected"),
    [(p, s, e) for _label, p, s, e in _DISPOSITION_CASES],
    ids=[label for label, _p, _s, _e in _DISPOSITION_CASES],
)
def test_classify_dispositions(path: str, stats: FileStats, expected: Disposition) -> None:
    assert classify(path, stats, _DEFAULT) is expected


def test_every_disposition_is_covered() -> None:
    # The acceptance criterion: the table exercises *every* disposition value.
    covered = {expected for _label, _p, _s, expected in _DISPOSITION_CASES}
    assert covered == set(Disposition)


def test_classify_file_carries_a_reason_for_every_omission() -> None:
    # Each omitted disposition must come with a non-empty reason — the cockpit
    # never renders an unexplained "(omitted)".
    for _label, path, stats, expected in _DISPOSITION_CASES:
        result = classify_file(path, stats, _DEFAULT)
        assert result.disposition is expected
        if expected is Disposition.INCLUDE_BODY:
            assert result.reason == ""
            assert result.omitted is False
        else:
            assert result.reason.strip()
            assert result.omitted is True


def test_too_large_reason_names_counts_and_cap() -> None:
    result = classify_file("huge.py", FileStats(added=2000, deleted=100), _DEFAULT)
    assert result.disposition is Disposition.OMIT_TOO_LARGE
    assert "2100" in result.reason  # added + deleted
    assert str(DEFAULT_MAX_FILE_DIFF_LINES) in result.reason


def test_lockfile_outranks_size_cap() -> None:
    # A huge lockfile reads as a lockfile, not as "too large" — the specific
    # reason wins over the generic size cap.
    result = classify_file("package-lock.json", FileStats(added=99999), _DEFAULT)
    assert result.disposition is Disposition.OMIT_LOCKFILE


def test_excluded_outranks_size_cap() -> None:
    result = classify_file("vendor/huge.go", FileStats(added=99999), _DEFAULT)
    assert result.disposition is Disposition.OMIT_EXCLUDED


def test_linguist_generated_outranks_globs_but_reports_attribute() -> None:
    # A normal-looking path flagged generated is excluded, and the reason points at
    # .gitattributes rather than a glob.
    result = classify_file("src/models.py", FileStats(added=5, linguist_generated=True), _DEFAULT)
    assert result.disposition is Disposition.OMIT_EXCLUDED
    assert "linguist-generated" in result.reason


def test_disposition_omits_body_flag() -> None:
    assert Disposition.INCLUDE_BODY.omits_body is False
    assert Disposition.OMIT_LOCKFILE.omits_body is True
    assert Disposition.OMIT_EXCLUDED.omits_body is True
    assert Disposition.OMIT_TOO_LARGE.omits_body is True


# --- Config extension / reset ----------------------------------------------


def test_extra_excludes_extend_builtins() -> None:
    config = ClassifierConfig(extra_excludes=("*.snap", "docs/api/*"))
    # The configured globs now omit...
    assert classify("ui/Button.snap", FileStats(added=3), config) is Disposition.OMIT_EXCLUDED
    assert classify("docs/api/ref.md", FileStats(added=3), config) is Disposition.OMIT_EXCLUDED
    # ...and the built-ins still apply alongside them.
    assert classify("node_modules/x.js", FileStats(added=3), config) is Disposition.OMIT_EXCLUDED


def test_exclude_reset_drops_builtin_globs_but_keeps_lockfiles_and_attributes() -> None:
    config = ClassifierConfig(exclude_reset=True, extra_excludes=("*.snap",))
    # Built-in dir/glob excludes no longer fire under reset...
    assert classify("node_modules/x.js", FileStats(added=3), config) is Disposition.INCLUDE_BODY
    assert classify("dist/bundle.js", FileStats(added=3), config) is Disposition.INCLUDE_BODY
    # ...but the configured exclude does...
    assert classify("ui/a.snap", FileStats(added=3), config) is Disposition.OMIT_EXCLUDED
    # ...and lockfiles + linguist-generated are deliberately *not* reset.
    assert classify("uv.lock", FileStats(added=3), config) is Disposition.OMIT_LOCKFILE
    assert (
        classify("x.py", FileStats(added=3, linguist_generated=True), config)
        is Disposition.OMIT_EXCLUDED
    )


def test_glob_matching_is_case_sensitive_on_every_platform() -> None:
    # fnmatchcase (not fnmatch) keeps glob excludes case-sensitive regardless of OS,
    # so a branch classifies identically on POSIX and Windows. The lowercase form is
    # excluded; an uppercased variant is not silently swept up.
    assert classify("static/app.min.js", FileStats(added=3), _DEFAULT) is Disposition.OMIT_EXCLUDED
    assert classify("static/APP.MIN.JS", FileStats(added=3), _DEFAULT) is Disposition.INCLUDE_BODY


def test_exclude_dir_reason_is_deterministic_when_multiple_match() -> None:
    # A path under two excluded dirs is OMIT_EXCLUDED either way; the human reason is
    # made stable (sorted) so the deterministic context files don't wobble run-to-run.
    config = ClassifierConfig(exclude_dirs=frozenset({"vendor", "third_party", "dist"}))
    result = classify_file("third_party/vendor/lib.js", FileStats(added=3), config)
    assert result.disposition is Disposition.OMIT_EXCLUDED
    assert result.reason == "excluded (in third_party/) — body omitted, stats kept"


def test_custom_caps_are_honored() -> None:
    config = ClassifierConfig(max_file_diff_lines=10)
    assert classify("x.py", FileStats(added=11), config) is Disposition.OMIT_TOO_LARGE
    assert classify("x.py", FileStats(added=10), config) is Disposition.INCLUDE_BODY


# --- Changeset (total-diff) guard ------------------------------------------


def _classified(
    rows: list[tuple[str, FileStats]], config: ClassifierConfig
) -> list[tuple[str, FileStats, Classification]]:
    return [(p, s, classify_file(p, s, config)) for p, s in rows]


def test_changeset_under_total_cap_is_not_too_large() -> None:
    config = ClassifierConfig(max_total_diff_lines=1000)
    rows = _classified([("a.py", FileStats(added=400)), ("b.py", FileStats(added=400))], config)
    result = classify_changeset(rows, config)
    assert result.too_large is False
    assert result.included_changed_lines == 800
    assert result.reason is None


def test_changeset_over_total_cap_is_too_large() -> None:
    config = ClassifierConfig(max_total_diff_lines=1000)
    rows = _classified([("a.py", FileStats(added=600)), ("b.py", FileStats(added=600))], config)
    result = classify_changeset(rows, config)
    assert result.too_large is True
    assert result.included_changed_lines == 1200
    assert result.reason and "1200" in result.reason


def test_changeset_total_ignores_already_omitted_bodies() -> None:
    # Omitted files cost the reader nothing, so they don't count toward the total —
    # a branch that is huge *only* because of a vendored blob is not "too large".
    config = ClassifierConfig(max_total_diff_lines=1000)
    rows = _classified(
        [("vendor/big.js", FileStats(added=50000)), ("small.py", FileStats(added=10))],
        config,
    )
    result = classify_changeset(rows, config)
    assert result.too_large is False
    assert result.included_changed_lines == 10


def test_downgrade_to_listing_only_touches_included_files() -> None:
    reason = "diff too large — file list + stats only"
    included = classify_file("a.py", FileStats(added=5), _DEFAULT)
    assert included.disposition is Disposition.INCLUDE_BODY
    downgraded = downgrade_to_listing(included, reason)
    assert downgraded.disposition is Disposition.OMIT_TOO_LARGE
    assert downgraded.reason == reason

    # An already-omitted file keeps its sharper, more specific reason.
    lock = classify_file("uv.lock", FileStats(added=5), _DEFAULT)
    assert downgrade_to_listing(lock, reason) == lock


def test_filestats_changed_lines() -> None:
    assert FileStats(added=3, deleted=4).changed_lines == 7
    assert FileStats(binary=True).changed_lines == 0
