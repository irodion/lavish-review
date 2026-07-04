#!/usr/bin/env python3
"""Skill entry point for the first-run installer (issue #12, ADR-0013).

The real, unit-tested logic lives in ``src/branch_review/install.py`` (the single
source of truth). Run once after ``npx skills add`` copies the skill into a repo:
it creates the machine config with the pinned Lavish version, gitignores the two
state dirs, and writes the per-platform entry points (Claude Code commands + the
review-analyst agent definition, Cursor commands; Codex needs no files). It is
idempotent and never overwrites an existing machine config or a locally changed
entry-point file (``--force`` replaces the latter).

``branch_review`` is resolved via ``_bootstrap`` — the repo's ``src/`` in
development, the skill's vendored ``lib/`` when installed (ADR-0013).
"""

from __future__ import annotations

import sys
from pathlib import Path

import _bootstrap

_SKILL = Path(__file__).resolve().parent.parent


def _main() -> int:
    _bootstrap.ensure_package()

    from branch_review.install import main

    argv = sys.argv[1:]
    # This shim knows the skill root exactly — pass it down (the module's own
    # default_skill_dir() stays as the fallback for direct invocation). Accept
    # both the `--skill-dir VALUE` and `--skill-dir=VALUE` argparse forms.
    if not any(arg == "--skill-dir" or arg.startswith("--skill-dir=") for arg in argv):
        argv = [*argv, "--skill-dir", str(_SKILL)]
    return main(argv)


if __name__ == "__main__":
    raise SystemExit(_main())
