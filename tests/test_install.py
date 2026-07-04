"""Tests for the first-run installer (issue #12, ADR-0013).

The pure planning policy (config text, gitignore additions, platform detection,
the idempotent copy rule) is table-driven; the :func:`~branch_review.install.install`
shell runs against real files in ``tmp_path``. The properties the ADR requires are
pinned directly: idempotent re-runs, an existing machine config is never touched,
locally changed entry points are kept unless ``--force``, and Codex gets no files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from branch_review.config import load_config_file, resolve
from branch_review.install import (
    GITIGNORE_ENTRIES,
    GITIGNORE_HEADER,
    PINNED_LAVISH_VERSION,
    Action,
    detect_platforms,
    entry_point_targets,
    install,
    machine_config_text,
    main,
    plan_file,
    plan_gitignore,
)

_SKILL = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "branch-review-cockpit"


# --- Pure planning ------------------------------------------------------------


def test_machine_config_round_trips_through_the_strict_loader(tmp_path: Path) -> None:
    # What the installer writes must be exactly what the resolver reads (ADR-0008's
    # strict subset): the pin survives as a *string*, never a mangled number.
    path = tmp_path / "config.yaml"
    path.write_text(machine_config_text(), encoding="utf-8")
    resolved = resolve(machine=load_config_file(path))
    assert resolved.lavish_version == PINNED_LAVISH_VERSION
    assert resolved.sessionstart_hook is False

    path.write_text(machine_config_text(sessionstart_hook=True), encoding="utf-8")
    assert resolve(machine=load_config_file(path)).sessionstart_hook is True


@pytest.mark.parametrize(
    ("existing", "expect_none", "expect_header"),
    [
        (None, False, True),  # no .gitignore at all → header + both entries
        ("", False, True),
        ("node_modules/\n", False, True),  # unrelated content → full block appended
        (f"{GITIGNORE_HEADER}\n.review-agent/\n", False, False),  # header there → no dup
        (".review-agent/\n.lavish-axi/\n", True, False),  # complete → nothing to do
    ],
)
def test_plan_gitignore(existing: str | None, expect_none: bool, expect_header: bool) -> None:
    addition = plan_gitignore(existing)
    if expect_none:
        assert addition is None
        return
    assert addition is not None
    assert (GITIGNORE_HEADER in addition) == expect_header
    combined = (existing or "") + addition
    for entry in GITIGNORE_ENTRIES:
        assert entry in combined.splitlines()


def test_plan_gitignore_appends_only_missing_entries() -> None:
    addition = plan_gitignore(f"{GITIGNORE_HEADER}\n.review-agent/\n")
    assert addition is not None
    assert ".lavish-axi/" in addition
    assert ".review-agent/" not in addition


def test_detect_platforms(tmp_path: Path) -> None:
    assert detect_platforms(tmp_path) == ()
    (tmp_path / ".claude").mkdir()
    assert detect_platforms(tmp_path) == ("claude",)
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".agents").mkdir()
    assert detect_platforms(tmp_path) == ("claude", "cursor", "codex")


def test_plan_file_policy(tmp_path: Path) -> None:
    dst = tmp_path / "cmd.md"
    assert plan_file(dst, "new", None, force=False).kind == "create"
    assert plan_file(dst, "same", "same", force=False).kind == "skip"
    # Local changes are the developer's — kept unless they ask.
    assert plan_file(dst, "new", "edited", force=False).kind == "conflict"
    assert plan_file(dst, "new", "edited", force=True).kind == "create"


def test_entry_point_targets_codex_contributes_nothing(tmp_path: Path) -> None:
    assert entry_point_targets(tmp_path, ("codex",)) == []
    claude = entry_point_targets(tmp_path, ("claude",))
    assert [t[2].name for t in claude] == [
        "review-branch.md",
        "review-resume.md",
        "review-close.md",
        "review-analyst.md",
    ]
    assert all(".claude" in str(t[2]) for t in claude)
    cursor = entry_point_targets(tmp_path, ("cursor",))
    assert len(cursor) == 3  # commands only — no agent registry on Cursor
    assert all(".cursor" in str(t[2]) for t in cursor)


# --- The install shell -----------------------------------------------------------


def _run(tmp_path: Path, **kwargs: object) -> list[Action]:
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    repo.mkdir(exist_ok=True)
    home.mkdir(exist_ok=True)
    return install(
        repo,
        _SKILL,
        home=home,
        platforms=("claude", "cursor"),
        **kwargs,  # type: ignore[arg-type]
    )


def test_install_creates_everything_then_is_idempotent(tmp_path: Path) -> None:
    first = _run(tmp_path)
    created = {a.path.name for a in first if a.kind == "create"}
    assert "config.yaml" in created and "review-analyst.md" in created
    assert (tmp_path / "repo" / ".gitignore").read_text(encoding="utf-8").count(".lavish-axi/") == 1
    assert (tmp_path / "repo" / ".cursor" / "commands" / "review-branch.md").is_file()

    second = _run(tmp_path)
    assert all(a.kind == "skip" for a in second)


def test_install_never_touches_an_existing_machine_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config = home / ".review-agent" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("pause: hold\n", encoding="utf-8")
    _run(tmp_path, force=True, sessionstart_hook=True)
    assert config.read_text(encoding="utf-8") == "pause: hold\n"  # even --force keeps it


def test_install_keeps_local_command_edits_unless_forced(tmp_path: Path) -> None:
    _run(tmp_path)
    cmd = tmp_path / "repo" / ".claude" / "commands" / "review-branch.md"
    cmd.write_text("my local tweak\n", encoding="utf-8")

    actions = _run(tmp_path)
    conflict = next(a for a in actions if a.path == cmd)
    assert conflict.kind == "conflict"
    assert cmd.read_text(encoding="utf-8") == "my local tweak\n"

    _run(tmp_path, force=True)
    assert cmd.read_text(encoding="utf-8") != "my local tweak\n"


def test_install_dry_run_writes_nothing(tmp_path: Path) -> None:
    actions = _run(tmp_path, dry_run=True)
    assert any(a.kind == "create" for a in actions)
    assert not (tmp_path / "repo" / ".gitignore").exists()
    assert not (tmp_path / "home" / ".review-agent").exists()


def test_cli_rejects_unknown_platform_and_missing_skill(tmp_path: Path) -> None:
    assert main(["--platforms", "vscode", "--repo", str(tmp_path)]) == 2
    assert main(["--skill-dir", str(tmp_path / "nope"), "--repo", str(tmp_path)]) == 2


def test_cli_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    code = main(
        [
            "--repo",
            str(repo),
            "--home",
            str(tmp_path / "home"),
            "--skill-dir",
            str(_SKILL),
            "--platforms",
            "claude,codex",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert (repo / ".claude" / "commands" / "review-close.md").is_file()
    assert "codex: no files needed" in captured.out
    assert PINNED_LAVISH_VERSION in captured.out
