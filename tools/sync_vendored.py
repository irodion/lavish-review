#!/usr/bin/env python3
"""Refresh the skill's vendored copies from their sources (ADR-0013).

The skill directory is copied verbatim into other repos by ``npx skills add``,
so everything a review run needs must live inside it:

- ``lib/branch_review/``            ← ``src/branch_review/`` (the tested package)
- ``assets/commands/*.md``          ← ``.claude/commands/review-*.md``
- ``assets/agents/review-analyst.md`` ← ``.claude/agents/review-analyst.md``

``src/`` and ``.claude/`` remain the single sources of truth for development;
this tool makes the vendored copies exact, deterministically (extraneous files
in the destinations are removed). ``tests/test_packaging.py`` fails whenever the
copies drift — the fix it names is running this tool.

Usage: ``python3 tools/sync_vendored.py`` (add ``--check`` to report drift
without writing, exit 1 if any).
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / ".claude" / "skills" / "branch-review-cockpit"

# (source, destination, glob) — every pair is mirrored exactly.
MIRRORS: tuple[tuple[Path, Path, str], ...] = (
    (REPO / "src" / "branch_review", SKILL / "lib" / "branch_review", "*.py"),
    (REPO / "src" / "branch_review", SKILL / "lib" / "branch_review", "py.typed"),
    (REPO / ".claude" / "commands", SKILL / "assets" / "commands", "review-*.md"),
    (REPO / ".claude" / "agents", SKILL / "assets" / "agents", "review-analyst.md"),
)


def planned_files() -> list[tuple[Path, Path]]:
    """Every (source_file, destination_file) pair the mirror comprises."""
    pairs: list[tuple[Path, Path]] = []
    for src_dir, dst_dir, pattern in MIRRORS:
        for src in sorted(src_dir.glob(pattern)):
            if src.is_file():
                pairs.append((src, dst_dir / src.name))
    return pairs


def _mirror_state() -> tuple[list[tuple[Path, Path, bool]], list[Path]]:
    """The mirror's bookkeeping, computed once for both :func:`drift` and :func:`sync`.

    Returns ``(pairs, extraneous)``: every ``(src, dst, up_to_date)`` mirror pair,
    and every file sitting in a destination directory that no pair accounts for.
    """
    pairs: list[tuple[Path, Path, bool]] = []
    expected: dict[Path, set[str]] = {}
    for src, dst in planned_files():
        expected.setdefault(dst.parent, set()).add(dst.name)
        pairs.append((src, dst, dst.is_file() and filecmp.cmp(src, dst, shallow=False)))
    extraneous = [
        extra
        for dst_dir, names in expected.items()
        if dst_dir.is_dir()
        for extra in sorted(dst_dir.iterdir())
        if extra.is_file() and extra.name not in names
    ]
    return pairs, extraneous


def drift() -> list[str]:
    """Human-readable differences between sources and vendored copies."""
    pairs, extraneous = _mirror_state()
    problems: list[str] = []
    for src, dst, up_to_date in pairs:
        if not dst.is_file():
            problems.append(f"missing: {dst.relative_to(REPO)}")
        elif not up_to_date:
            problems.append(f"stale: {dst.relative_to(REPO)} != {src.relative_to(REPO)}")
    problems += [f"extraneous: {extra.relative_to(REPO)}" for extra in extraneous]
    return problems


def sync() -> int:
    """Make the vendored copies exact; returns the number of files written/removed."""
    pairs, extraneous = _mirror_state()
    changed = 0
    for src, dst, up_to_date in pairs:
        if not up_to_date:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            changed += 1
    for extra in extraneous:
        extra.unlink()
        changed += 1
    for _src_dir, dst_dir, _pattern in MIRRORS:
        pycache = dst_dir / "__pycache__"
        if pycache.is_dir():
            shutil.rmtree(pycache)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report drift, write nothing")
    args = parser.parse_args(argv)

    if args.check:
        problems = drift()
        for problem in problems:
            print(problem, file=sys.stderr)
        if problems:
            print("run: python3 tools/sync_vendored.py", file=sys.stderr)
        return 1 if problems else 0

    changed = sync()
    print(f"vendored copies in sync ({changed} file(s) updated)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
