"""Packaging tripwires (issue #12, ADR-0013).

Three mechanically checked contracts keep the skill installable:

1. **No drift**: every vendored copy inside the skill (``lib/branch_review/``,
   the command/agent templates in ``assets/``) is byte-identical to its source
   of truth. A failure here means "run ``python3 tools/sync_vendored.py``".
2. **Self-contained**: the skill directory, copied alone into a bare repo (what
   ``npx skills add`` does), runs its scripts from the vendored package — no
   dependency on this repo's ``src/``.
3. **One pin**: the Lavish version SKILL.md quotes is the constant the installer
   writes, so docs, config, and invocation can never disagree.
"""

from __future__ import annotations

import filecmp
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from branch_review.install import PINNED_LAVISH_VERSION

_REPO = Path(__file__).resolve().parents[1]
_SKILL = _REPO / ".claude" / "skills" / "branch-review-cockpit"


def _sync_module() -> Any:
    """Import tools/sync_vendored.py by path (tools/ is not a package)."""
    spec = importlib.util.spec_from_file_location(
        "sync_vendored", _REPO / "tools" / "sync_vendored.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- 1. Drift ------------------------------------------------------------------


def test_vendored_copies_match_their_sources() -> None:
    sync = _sync_module()
    assert sync.drift() == [], "vendored copies drifted — run: python3 tools/sync_vendored.py"


def test_mirror_covers_the_whole_package() -> None:
    # Every module in src/branch_review must be part of the mirror — a new module
    # that never reaches the vendored tree would break installed skills silently.
    sync = _sync_module()
    mirrored = {src.name for src, _dst in sync.planned_files()}
    for source in (_REPO / "src" / "branch_review").iterdir():
        if source.is_file() and source.suffix in {".py", ".typed"} or source.name == "py.typed":
            assert source.name in mirrored, f"{source.name} missing from tools/sync_vendored.py"


def test_templates_match_the_live_claude_files() -> None:
    # The installed instances in this repo's .claude/ are the same files the skill
    # ships as templates — reviewers exercise exactly what users will install.
    for name in ("review-branch.md", "review-resume.md", "review-close.md"):
        assert filecmp.cmp(
            _REPO / ".claude" / "commands" / name,
            _SKILL / "assets" / "commands" / name,
            shallow=False,
        ), f"template drift: {name}"
    assert filecmp.cmp(
        _REPO / ".claude" / "agents" / "review-analyst.md",
        _SKILL / "assets" / "agents" / "review-analyst.md",
        shallow=False,
    )


# --- 2. Self-contained -----------------------------------------------------------


@pytest.fixture
def installed_skill(tmp_path: Path) -> Path:
    """The skill directory copied alone into a bare target repo (what skills add does)."""
    target = tmp_path / "target"
    dest = target / ".claude" / "skills" / "branch-review-cockpit"
    dest.parent.mkdir(parents=True)
    shutil.copytree(_SKILL, dest, ignore=shutil.ignore_patterns("__pycache__"))
    return target


def _run_script(target: Path, script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — fixed argv, our own scripts under test
        [
            sys.executable,
            str(target / ".claude" / "skills" / "branch-review-cockpit" / "scripts" / script),
            *args,
        ],
        cwd=target,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_installed_scripts_run_from_the_vendored_package(installed_skill: Path) -> None:
    # No src/ anywhere in the target: the shim must fall back to lib/branch_review.
    result = _run_script(installed_skill, "validate_analysis.py", "--help")
    assert result.returncode == 0, result.stderr
    result = _run_script(installed_skill, "lint_cockpit.py", "--help")
    assert result.returncode == 0, result.stderr


def test_installed_installer_sets_up_a_bare_repo(installed_skill: Path, tmp_path: Path) -> None:
    (installed_skill / ".cursor").mkdir()
    home = tmp_path / "home"
    result = _run_script(installed_skill, "install.py", "--repo", ".", "--home", str(home))
    assert result.returncode == 0, result.stderr
    assert (home / ".review-agent" / "config.yaml").is_file()
    gitignore = (installed_skill / ".gitignore").read_text(encoding="utf-8")
    assert ".review-agent/" in gitignore and ".lavish-axi/" in gitignore
    assert (installed_skill / ".claude" / "commands" / "review-branch.md").is_file()
    assert (installed_skill / ".claude" / "agents" / "review-analyst.md").is_file()
    assert (installed_skill / ".cursor" / "commands" / "review-branch.md").is_file()


# --- 3. One pin ------------------------------------------------------------------


def test_skill_md_quotes_the_pinned_lavish_version() -> None:
    skill_md = (_SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert f"lavish-axi@{PINNED_LAVISH_VERSION}" in skill_md, (
        "SKILL.md's pinned Lavish invocation must match branch_review.install.PINNED_LAVISH_VERSION"
    )
