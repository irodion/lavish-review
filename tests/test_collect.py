"""Tests for the deterministic Review context collector.

Each test builds a throwaway git repo in a temp dir so the collector runs against
real ``git`` plumbing — the layer it exists to wrap.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from branch_review.collect import (
    BaseResolutionError,
    collect,
    copy_assets,
    detect_base,
)

_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": os.environ["PATH"], "HOME": str(repo), **_ENV},
    )
    return proc.stdout.strip()


def _commit(repo: Path, name: str, content: str, message: str) -> None:
    (repo / name).write_text(content)
    _git(repo, "add", name)
    _git(repo, "commit", "-m", message)


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Path]:
    """A repo with a `main` base commit and a `feature` branch with one change."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _commit(root, "app.py", "x = 1\n", "base: initial")
    _git(root, "checkout", "-b", "feature")
    _commit(root, "app.py", "x = 2\n", "feat: bump x")
    yield root


def test_detect_base_falls_back_to_main(repo: Path) -> None:
    assert detect_base(repo) == "main"


def test_detect_base_raises_when_no_candidate(tmp_path: Path) -> None:
    root = tmp_path / "orphan"
    root.mkdir()
    _git(root, "init", "-b", "trunk")
    _commit(root, "f.txt", "hi\n", "only commit")
    with pytest.raises(BaseResolutionError):
        detect_base(root)


def test_collect_writes_context_and_files(repo: Path) -> None:
    context = collect(repo)
    out = repo / ".review-agent"

    assert context.base == "main"
    assert context.branch == "feature"
    assert context.changed_file_count == 1
    assert not context.is_empty

    for name in (
        "context.json",
        "changed-files.json",
        "diff.patch",
        "diff-stat.txt",
        "commits.txt",
        "diff.fragment.html",
    ):
        assert (out / name).is_file(), name

    written = json.loads((out / "context.json").read_text())
    assert written["base"] == "main"
    assert written["branch"] == "feature"
    assert written["diff_range"] == "main...HEAD"
    assert written["schema"].startswith("review-context/")

    files = json.loads((out / "changed-files.json").read_text())
    assert files == [{"status": "M", "path": "app.py"}]

    assert "x = 2" in (out / "diff.patch").read_text()
    assert "feat: bump x" in (out / "commits.txt").read_text()


def test_explicit_base_overrides_autodetect(repo: Path) -> None:
    # A second base whose merge-base with HEAD is the same initial commit.
    _git(repo, "branch", "develop", "main")
    context = collect(repo, base="develop")
    assert context.base == "develop"
    assert context.diff_range == "develop...HEAD"


def test_diff_fragment_escapes_untrusted_content(repo: Path) -> None:
    _git(repo, "checkout", "feature")
    _commit(repo, "evil.py", "# <script>alert(1)</script>\n", "feat: add xss bait")
    collect(repo)
    fragment = (repo / ".review-agent" / "diff.fragment.html").read_text()

    assert "<script>alert(1)</script>" not in fragment
    assert "&lt;script&gt;" in fragment
    assert fragment.startswith('<pre class="diff">')


def test_empty_range_is_marked(repo: Path) -> None:
    _git(repo, "checkout", "main")
    _git(repo, "branch", "-D", "feature")
    _git(repo, "checkout", "-b", "noop")  # no new commits → empty range vs main
    context = collect(repo)
    assert context.is_empty
    assert context.changed_file_count == 0
    assert "no changes" in (repo / ".review-agent" / "diff.fragment.html").read_text()


def test_collect_copies_assets(repo: Path, tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "cockpit.css").write_text("/* css */")
    (assets / "app.js").write_text("// js")

    collect(repo, assets_dir=assets)
    dest = repo / ".review-agent" / "assets"
    assert (dest / "cockpit.css").read_text() == "/* css */"
    assert (dest / "app.js").read_text() == "// js"


def test_copy_assets_missing_source_raises(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        copy_assets(empty, tmp_path / "dest")
