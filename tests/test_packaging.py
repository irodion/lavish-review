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


def test_unowned_destinations_are_never_swept() -> None:
    # The LICENSE lands at the skill ROOT, which the mirror does not own —
    # SKILL.md lives there and must never be reported extraneous (sync() would
    # delete it). Only fully mirror-owned dirs participate in the sweep.
    sync = _sync_module()
    assert any(not owned for _s, _d, _p, owned in sync.MIRRORS)
    _pairs, extraneous = sync._mirror_state()
    assert _SKILL / "SKILL.md" not in extraneous
    assert all(extra.name != "SKILL.md" for extra in extraneous)


def test_mirror_covers_the_whole_package() -> None:
    # Every module in src/branch_review must be part of the mirror — a new module
    # that never reaches the vendored tree would break installed skills silently.
    # Matched on the full planned destination, not the basename, so a same-named
    # file mirrored elsewhere can never satisfy this check.
    sync = _sync_module()
    planned = {dst for _src, dst in sync.planned_files()}
    lib = _SKILL / "lib" / "branch_review"
    for source in (_REPO / "src" / "branch_review").iterdir():
        if source.is_file() and (source.suffix == ".py" or source.name == "py.typed"):
            assert lib / source.name in planned, (
                f"{source.name} missing from tools/sync_vendored.py"
            )


def test_mirror_ships_every_template_the_installer_writes() -> None:
    # The installer (_COMMANDS/_AGENT_DEF), the mirror (MIRRORS), and this test each
    # see the template set from a different vantage point; this cross-check keeps the
    # three from drifting apart. Byte equality of the copies is drift()'s job above.
    from branch_review.install import _AGENT_DEF, _COMMANDS

    sync = _sync_module()
    shipped = {
        dst.relative_to(_SKILL / "assets")
        for _src, dst in sync.planned_files()
        if dst.is_relative_to(_SKILL / "assets")
    }
    needed = {Path("commands") / name for name in _COMMANDS} | {Path("agents") / _AGENT_DEF}
    assert needed <= shipped, f"installer templates missing from the mirror: {needed - shipped}"


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
