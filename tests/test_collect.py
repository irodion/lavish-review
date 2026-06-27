"""Tests for the deterministic Review context collector.

Each test builds a throwaway git repo in a temp dir so the collector runs against
real ``git`` plumbing — the layer it exists to wrap.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
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


def test_detect_base_prefers_remote_default_over_local(repo: Path) -> None:
    # origin/HEAD -> origin/main AND a local `main` both exist. Documented
    # precedence is origin/HEAD first: returning local `main` would risk diffing
    # against a stale base and leaking already-merged commits into the cockpit.
    main_sha = _git(repo, "rev-parse", "main")
    _git(repo, "update-ref", "refs/remotes/origin/main", main_sha)
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    # local `main` is intentionally left in place.

    assert detect_base(repo) == "origin/main"

    context = collect(repo)
    assert context.base == "origin/main"
    assert context.diff_range == "origin/main...HEAD"


def test_detect_base_uses_remote_default_when_no_local(repo: Path) -> None:
    # Simulate a feature-only checkout: origin/HEAD -> origin/main, but no local main.
    main_sha = _git(repo, "rev-parse", "main")
    _git(repo, "update-ref", "refs/remotes/origin/main", main_sha)
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    _git(repo, "checkout", "feature")
    _git(repo, "branch", "-D", "main")

    assert detect_base(repo) == "origin/main"

    # ...and the remote-tracking ref must drive a working collection.
    context = collect(repo)
    assert context.base == "origin/main"
    assert context.diff_range == "origin/main...HEAD"
    assert context.changed_file_count == 1


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


def test_artifacts_are_utf8(repo: Path) -> None:
    _git(repo, "checkout", "feature")
    _commit(repo, "app.py", "x = 'é你好\U0001f600'\n", "feat: café 你好 😀")
    collect(repo)
    out = repo / ".review-agent"

    # Decoding strictly as UTF-8 must succeed and preserve the non-ASCII bytes.
    assert "café 你好 \U0001f600" in (out / "commits.txt").read_text(encoding="utf-8")
    assert "你好\U0001f600" in (out / "diff.patch").read_text(encoding="utf-8")
    assert "你好\U0001f600" in (out / "diff.fragment.html").read_text(encoding="utf-8")


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


def test_skill_shim_runs_without_editable_install(repo: Path, tmp_path: Path) -> None:
    """The documented `python <shim>` works in a fresh checkout (no editable install).

    Run with `-S` so site-packages (where the editable install lives) is not on
    the path — the script only succeeds if its own `sys.path` insertion finds
    `branch_review` under the repo's `src/`.
    """
    project_root = Path(__file__).resolve().parents[1]
    shim = project_root / ".claude/skills/branch-review-cockpit/scripts/collect_review_context.py"
    out = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, "-S", str(shim), "--repo", str(repo), "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": os.environ["PATH"], "HOME": str(repo), **_ENV},
    )

    assert proc.returncode == 0, proc.stderr
    assert (out / "context.json").is_file()
    assert (out / "diff.fragment.html").is_file()
    # Default --assets-dir resolves to the skill's vendored assets and is copied.
    assert (out / "assets" / "cockpit.css").is_file()
    assert (out / "assets" / "app.js").is_file()


def test_skill_shim_honors_assets_dir_equals_form(repo: Path, tmp_path: Path) -> None:
    """`--assets-dir=VALUE` must override the vendored default, not be ignored."""
    project_root = Path(__file__).resolve().parents[1]
    shim = project_root / ".claude/skills/branch-review-cockpit/scripts/collect_review_context.py"
    custom = tmp_path / "custom-assets"
    custom.mkdir()
    (custom / "cockpit.css").write_text("/* custom */")
    (custom / "app.js").write_text("// custom")
    out = tmp_path / "out"

    proc = subprocess.run(
        [
            sys.executable,
            str(shim),
            f"--assets-dir={custom}",
            "--repo",
            str(repo),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": os.environ["PATH"], "HOME": str(repo), **_ENV},
    )

    assert proc.returncode == 0, proc.stderr
    assert (out / "assets" / "cockpit.css").read_text() == "/* custom */"
    assert (out / "assets" / "app.js").read_text() == "// custom"
