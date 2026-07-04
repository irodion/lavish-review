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

import _bootstrap


def _main() -> int:
    _bootstrap.ensure_package()

    from branch_review.install import main

    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(_main())
