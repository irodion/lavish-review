"""Tests for Goal Evidence resolution (ADR-0010, issue #40).

The resolution is pure policy (:func:`resolve_goal` takes already-gathered inputs and
injected fetch/read callables), so the precedence ladder — explicit argument > discovered
issue ref via the tracker > first commit message > none — is pinned table-style with no
subprocess and no network. The ``gh`` shell is exercised only for its argv construction
guarantees via the ref parsers; its failure modes all reduce to "the callable returned
``None``", which the pure tests cover.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from branch_review.goal import (
    GOAL_SOURCES,
    GoalEvidence,
    IssueRef,
    discover_issue_refs,
    parse_goal_argument_ref,
    read_goal_file,
    resolve_goal,
)

_COMMITS = [
    ("aaa1111", "feat: exponential backoff (#40)\n\nDoubles per attempt, 60s cap."),
    ("bbb2222", "fix: clamp first delay"),
]


def _no_fetch(ref: IssueRef) -> str | None:
    raise AssertionError(f"fetch_issue must not be called (got {ref.display()})")


def _no_read(value: str) -> str | None:
    return None


# --- ref parsing --------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("#40", IssueRef(number=40)),
        (" #40 ", IssueRef(number=40)),
        ("owner/repo#7", IssueRef(number=7, repo="owner/repo")),
        (
            "https://github.com/o/r/issues/12",
            IssueRef(number=12, url="https://github.com/o/r/issues/12"),
        ),
        (
            "https://github.com/o/r/pull/13",
            IssueRef(number=13, url="https://github.com/o/r/pull/13", is_pr=True),
        ),
    ],
)
def test_explicit_ref_shapes_parse(value: str, expected: IssueRef) -> None:
    assert parse_goal_argument_ref(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "fix the retry loop",  # literal text
        "docs/prd.md",  # a path
        "40",  # a bare number is ambiguous — not a ref
        "#40 and #41",  # prose containing refs is text, not a ref
        "-evil/repo#4",  # repo may not start with '-' (can never look like a flag)
        "http://github.com/o/r/issues/12",  # https only
    ],
)
def test_non_refs_do_not_parse(value: str) -> None:
    assert parse_goal_argument_ref(value) is None


def test_discovery_prefers_branch_name_then_commit_order() -> None:
    refs = discover_issue_refs("feat/40-goal-evidence", ["fix #7", "see #40 and #9"])
    assert [r.number for r in refs] == [40, 7, 9]  # deduped, branch first
    assert all(r.repo is None and r.url is None for r in refs)  # same-repo only


@pytest.mark.parametrize("branch", ["main", "v2-migration", "feature/no-number"])
def test_discovery_ignores_branches_without_a_delimited_number(branch: str) -> None:
    assert discover_issue_refs(branch, []) == []


# --- precedence ladder ----------------------------------------------------------


def test_explicit_text_wins_over_everything() -> None:
    goal, warnings = resolve_goal(
        argument="make retries polite to a struggling upstream",
        branch="feat/40-goal-evidence",
        commits=_COMMITS,
        remote_enabled=True,
        fetch_issue=_no_fetch,  # an explicit literal goal must not touch the tracker
        read_file=_no_read,
    )
    assert warnings == []
    assert goal == GoalEvidence(
        text="make retries polite to a struggling upstream",
        source="argument",
        provenance="provided literally by --goal",
    )


def test_explicit_issue_ref_is_fetched_and_attributed() -> None:
    goal, warnings = resolve_goal(
        argument="#40",
        branch="main",
        commits=[],
        remote_enabled=True,
        fetch_issue=lambda ref: f"Goal Evidence ingestion (issue {ref.number})",
        read_file=_no_read,
    )
    assert warnings == []
    assert goal is not None
    assert goal.source == "issue"
    assert "issue #40" in goal.provenance and "--goal" in goal.provenance
    assert goal.text.startswith("Goal Evidence ingestion")


def test_explicit_ref_fetch_failure_warns_and_never_guesses() -> None:
    # ADR-0010: an explicit argument is never guessed over — the rich local
    # evidence (branch ref, commit text) must NOT substitute for a failed --goal.
    goal, warnings = resolve_goal(
        argument="#40",
        branch="feat/40-goal-evidence",
        commits=_COMMITS,
        remote_enabled=True,
        fetch_issue=lambda ref: None,
        read_file=_no_read,
    )
    assert goal is None
    assert len(warnings) == 1 and "#40" in warnings[0]


def test_explicit_ref_with_remote_disabled_warns_and_never_fetches() -> None:
    goal, warnings = resolve_goal(
        argument="#40",
        branch="main",
        commits=[],
        remote_enabled=False,
        fetch_issue=_no_fetch,
        read_file=_no_read,
    )
    assert goal is None
    assert len(warnings) == 1 and "goal_remote_fetch" in warnings[0]


def test_explicit_file_is_read_and_attributed() -> None:
    goal, warnings = resolve_goal(
        argument="docs/prd.md",
        branch="main",
        commits=[],
        remote_enabled=True,
        fetch_issue=_no_fetch,
        read_file=lambda value: "The PRD text.\n" if value == "docs/prd.md" else None,
    )
    assert warnings == []
    assert goal == GoalEvidence(
        text="The PRD text.",
        source="file",
        provenance="file docs/prd.md (named by --goal)",
    )


def test_discovered_ref_resolves_through_the_tracker() -> None:
    goal, warnings = resolve_goal(
        argument=None,
        branch="feat/40-goal-evidence",
        commits=_COMMITS,
        remote_enabled=True,
        fetch_issue=lambda ref: "The issue body" if ref.number == 40 else None,
        read_file=_no_read,
    )
    assert warnings == []
    assert goal is not None
    assert goal.source == "issue"
    assert "issue #40" in goal.provenance and "branch name" in goal.provenance


def test_commit_ref_attribution_names_the_commit_message() -> None:
    goal, _ = resolve_goal(
        argument=None,
        branch="no-number-here",
        commits=_COMMITS,
        remote_enabled=True,
        fetch_issue=lambda ref: "Body" if ref.number == 40 else None,
        read_file=_no_read,
    )
    assert goal is not None and "commit message" in goal.provenance


def test_fetch_failure_degrades_silently_to_first_commit_message() -> None:
    goal, warnings = resolve_goal(
        argument=None,
        branch="feat/40-goal-evidence",
        commits=_COMMITS,
        remote_enabled=True,
        fetch_issue=lambda ref: None,  # offline / no gh / unauthenticated
        read_file=_no_read,
    )
    assert warnings == []  # the discovery path never warns — degrade is by design
    assert goal is not None
    assert goal.source == "commits"
    assert "aaa1111" in goal.provenance
    assert goal.text.startswith("feat: exponential backoff")


def test_remote_disabled_run_is_network_free() -> None:
    # goal_remote_fetch: false — even with refs everywhere, the fetcher is never called.
    goal, warnings = resolve_goal(
        argument=None,
        branch="feat/40-goal-evidence",
        commits=_COMMITS,
        remote_enabled=False,
        fetch_issue=_no_fetch,
        read_file=_no_read,
    )
    assert warnings == []
    assert goal is not None and goal.source == "commits"


def test_no_refs_no_fetch_attempt() -> None:
    # The default review stays network-free unless local evidence names an issue.
    goal, _ = resolve_goal(
        argument=None,
        branch="cleanup",
        commits=[("ccc3333", "chore: tidy imports")],
        remote_enabled=True,
        fetch_issue=_no_fetch,
        read_file=_no_read,
    )
    assert goal is not None and goal.source == "commits"


def test_nothing_found_resolves_to_none() -> None:
    goal, warnings = resolve_goal(
        argument=None,
        branch="cleanup",
        commits=[],
        remote_enabled=True,
        fetch_issue=_no_fetch,
        read_file=_no_read,
    )
    assert goal is None and warnings == []


def test_sources_vocabulary_is_canonical() -> None:
    assert set(GOAL_SOURCES) == {"argument", "file", "issue", "commits"}
    for source in ("argument", "file", "issue", "commits"):
        assert source in GOAL_SOURCES


# --- the file-reading shell -----------------------------------------------------


def test_read_goal_file_reads_and_misses(tmp_path: Path) -> None:
    target = tmp_path / "goal.md"
    target.write_text("Stated goal.\n", encoding="utf-8")
    assert read_goal_file(str(target)) == "Stated goal.\n"
    assert read_goal_file(str(tmp_path / "absent.md")) is None
    assert read_goal_file(str(tmp_path)) is None  # a directory is not a goal file
