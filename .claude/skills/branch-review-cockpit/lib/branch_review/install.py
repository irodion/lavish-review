"""First-run installer — the setup a skill copy cannot do itself (ADR-0013).

``npx skills add`` installs the skill by **copying its directory** into a target
repo; no SKILL.md platform (Claude Code, Cursor, Codex) and no installer CLI
offers a post-install hook. Everything a working review needs beyond the copy is
therefore done here, once, idempotently:

- **Machine config** (``~/.review-agent/config.yaml``): created with the pinned
  Lavish version so a release can never change behavior mid-review. An existing
  config is **never touched** — it is the developer's, not ours.
- **``.gitignore``**: the two state dirs (``.review-agent/``, ``.lavish-axi/``)
  are appended if missing — generated review state must never be committed.
- **Entry points, per platform**: Claude Code gets the three ``/review-*``
  command files plus the ``review-analyst`` agent definition (the ADR-0011
  isolation boundary); Cursor gets the same three commands under
  ``.cursor/commands/``; Codex gets **no files** — skills are invoked natively
  there (``$``-mention or implicit activation), and its custom prompts are
  user-scoped, which this installer does not write into.

Templates ship inside the skill (``assets/commands/``, ``assets/agents/`` —
kept equality-pinned to this repo's live copies by ``tools/sync_vendored.py``).
An existing target file with local changes is **kept**, reported as a conflict,
and only replaced under ``--force``.

Pure planning (:func:`plan_file`, :func:`plan_gitignore`,
:func:`machine_config_text`, :func:`detect_platforms` over listed dirs) beneath
a thin I/O shell (:func:`install`, :func:`main`), like the Config Resolver.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from branch_review.config import MACHINE_CONFIG_REL
from branch_review.feedback import LAVISH_PKG

# Derived from the one authoritative pin, ``feedback.LAVISH_PKG`` (which drives the
# actual open/loop invocations), so the two can never disagree (ADR-0013). SKILL.md
# quotes it and a test asserts the quote matches; the installer writes it into the
# machine config below.
PINNED_LAVISH_VERSION = LAVISH_PKG.split("@", 1)[1]

# What .gitignore must contain (matching this repo's own entries).
GITIGNORE_HEADER = "# Branch Review Cockpit — generated review state (never committed)"
GITIGNORE_ENTRIES = (".review-agent/", ".lavish-axi/")

PLATFORMS = ("claude", "cursor", "codex")

_COMMANDS = ("review-branch.md", "review-resume.md", "review-close.md")
_AGENT_DEF = "review-analyst.md"


@dataclass(frozen=True)
class Action:
    """One planned/performed installer step, for the summary and for tests."""

    kind: Literal["create", "append", "skip", "conflict", "error"]
    path: Path
    content: str = ""
    reason: str = ""


# --- Pure planning ------------------------------------------------------------


def machine_config_text(*, sessionstart_hook: bool = False) -> str:
    """The machine config the installer creates (strict flat-YAML subset, ADR-0008)."""
    lines = [
        "# Machine-scope review policy — created by the Branch Review Cockpit installer.",
        "# Recognized keys: pause, styling, lavish_version, sessionstart_hook,",
        "# goal_remote_fetch. See the skill's SKILL.md.",
        f"lavish_version: {PINNED_LAVISH_VERSION}",
    ]
    if sessionstart_hook:
        lines.append("sessionstart_hook: true")
    return "\n".join(lines) + "\n"


def plan_gitignore(existing: str | None) -> str | None:
    """The block to append to ``.gitignore``, or ``None`` when nothing is missing."""
    present = {line.strip() for line in existing.splitlines()} if existing else set()
    missing = [entry for entry in GITIGNORE_ENTRIES if entry not in present]
    if not missing:
        return None
    block_lines = [] if GITIGNORE_HEADER in present else [GITIGNORE_HEADER]
    block = "\n".join([*block_lines, *missing]) + "\n"
    if not existing:
        return block
    return ("" if existing.endswith("\n") else "\n") + "\n" + block


def detect_platforms(repo: Path) -> tuple[str, ...]:
    """Which platforms this repo shows evidence of (their config dirs exist)."""
    found = []
    if (repo / ".claude").is_dir():
        found.append("claude")
    if (repo / ".cursor").is_dir():
        found.append("cursor")
    if (repo / ".agents").is_dir() or (repo / ".codex").is_dir():
        found.append("codex")
    return tuple(found)


def plan_file(dst: Path, content: str, existing: str | None, *, force: bool) -> Action:
    """Idempotent copy policy: create, skip identical, keep local changes."""
    if existing is None:
        return Action("create", dst, content)
    if existing == content:
        return Action("skip", dst, reason="already up to date")
    if force:
        return Action("create", dst, content, reason="replaced (--force)")
    return Action("conflict", dst, reason="exists with local changes — kept (re-run with --force)")


def entry_point_targets(repo: Path, platforms: tuple[str, ...]) -> list[tuple[str, str, Path]]:
    """``(template_kind, template_name, destination)`` per selected platform.

    ``codex`` deliberately contributes nothing: skills are invoked natively there,
    and writing into user-scoped prompt directories is not this installer's place.
    """
    targets: list[tuple[str, str, Path]] = []
    if "claude" in platforms:
        targets += [("commands", name, repo / ".claude" / "commands" / name) for name in _COMMANDS]
        targets.append(("agents", _AGENT_DEF, repo / ".claude" / "agents" / _AGENT_DEF))
    if "cursor" in platforms:
        targets += [("commands", name, repo / ".cursor" / "commands" / name) for name in _COMMANDS]
    return targets


def default_skill_dir() -> Path | None:
    """Where the skill lives relative to this module — installed ``lib/`` or dev repo."""
    here = Path(__file__).resolve()
    # Installed: <skill>/lib/branch_review/install.py → parents[2] is the skill root.
    # Development: <repo>/src/branch_review/install.py → parents[2] is the repo root.
    for candidate in (
        here.parents[2],
        here.parents[2] / ".claude" / "skills" / "branch-review-cockpit",
    ):
        if (candidate / "SKILL.md").is_file() and (candidate / "assets").is_dir():
            return candidate
    return None


# --- I/O shell ------------------------------------------------------------------


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def install(
    repo: Path,
    skill_dir: Path,
    *,
    home: Path,
    platforms: tuple[str, ...],
    force: bool = False,
    sessionstart_hook: bool = False,
    dry_run: bool = False,
) -> list[Action]:
    """Plan (and unless ``dry_run``, perform) the full first-run setup."""
    actions: list[Action] = []

    # 1. Machine config — created once, never modified after.
    config_path = home / MACHINE_CONFIG_REL
    if _read(config_path) is None:
        actions.append(
            Action("create", config_path, machine_config_text(sessionstart_hook=sessionstart_hook))
        )
    else:
        reason = "exists — left untouched"
        if sessionstart_hook:
            reason += " (set sessionstart_hook: true there yourself)"
        actions.append(Action("skip", config_path, reason=reason))

    # 2. .gitignore — append only what is missing.
    gitignore = repo / ".gitignore"
    addition = plan_gitignore(_read(gitignore))
    if addition is None:
        actions.append(Action("skip", gitignore, reason="state dirs already ignored"))
    else:
        actions.append(Action("append", gitignore, addition))

    # 3. Per-platform entry points from the skill's shipped templates.
    for kind, name, dst in entry_point_targets(repo, platforms):
        template = skill_dir / "assets" / kind / name
        content = _read(template)
        if content is None:
            # Not a conflict: --force can't fix an incomplete skill copy.
            actions.append(Action("error", dst, reason=f"template missing: {template}"))
            continue
        actions.append(plan_file(dst, content, _read(dst), force=force))

    if not dry_run:
        for action in actions:
            if action.kind == "create":
                action.path.parent.mkdir(parents=True, exist_ok=True)
                action.path.write_text(action.content, encoding="utf-8")
            elif action.kind == "append":
                existing = _read(action.path) or ""
                action.path.write_text(existing + action.content, encoding="utf-8")
    return actions


def main(argv: list[str] | None = None) -> int:
    """CLI: ``install.py [--platforms claude,cursor,codex] [--force] [--dry-run]``."""
    parser = argparse.ArgumentParser(
        prog="install",
        description="First-run setup for the Branch Review Cockpit skill (ADR-0013).",
    )
    parser.add_argument(
        "--repo", type=Path, default=Path.cwd(), help="Target repo root (default: cwd)."
    )
    parser.add_argument(
        "--platforms",
        default=None,
        help="Comma-separated subset of claude,cursor,codex (default: auto-detect).",
    )
    parser.add_argument(
        "--skill-dir", type=Path, default=None, help="Skill directory (default: auto)."
    )
    parser.add_argument("--home", type=Path, default=Path.home(), help=argparse.SUPPRESS)
    parser.add_argument("--force", action="store_true", help="Replace changed entry-point files.")
    parser.add_argument(
        "--sessionstart-hook",
        action="store_true",
        help="Record sessionstart_hook: true in a newly created machine config.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the plan, write nothing.")
    args = parser.parse_args(argv)

    skill_dir = args.skill_dir or default_skill_dir()
    if skill_dir is None or not (skill_dir / "SKILL.md").is_file():
        print("error: cannot locate the skill directory (pass --skill-dir)", file=sys.stderr)
        return 2

    if args.platforms is not None:
        platforms = tuple(p.strip() for p in args.platforms.split(",") if p.strip())
        unknown = [p for p in platforms if p not in PLATFORMS]
        if unknown:
            print(f"error: unknown platform(s): {', '.join(unknown)}", file=sys.stderr)
            return 2
    else:
        platforms = detect_platforms(args.repo)
        if not platforms:
            print(
                "note: no .claude/.cursor/.agents dir found — pass --platforms to "
                "choose; doing the shared setup only."
            )

    actions = install(
        args.repo,
        skill_dir,
        home=args.home,
        platforms=platforms,
        force=args.force,
        sessionstart_hook=args.sessionstart_hook,
        dry_run=args.dry_run,
    )

    verb = "would " if args.dry_run else ""
    for action in actions:
        detail = f" ({action.reason})" if action.reason else ""
        print(f"{verb}{action.kind}: {action.path}{detail}")
    if "codex" in platforms:
        print(
            "codex: no files needed — invoke the skill natively "
            "($-mention branch-review-cockpit or rely on implicit activation)."
        )
    conflicts = [a for a in actions if a.kind == "conflict"]
    if conflicts:
        print(f"{len(conflicts)} file(s) kept with local changes; use --force to replace.")
    print(f"Lavish pinned at {PINNED_LAVISH_VERSION} (machine config overrides).")
    errors = [a for a in actions if a.kind == "error"]
    if errors:
        print(
            f"{len(errors)} template(s) missing — the skill copy is incomplete; "
            "re-install it (npx skills add).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
