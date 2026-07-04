#!/usr/bin/env python3
"""Skill entry point for read-only test-runner detection (issue #6).

The real, unit-tested logic lives in ``src/branch_review/runners.py`` (the single
source of truth). This shim exposes it as the skill's runnable script: the agent
runs it to learn which test runner the repo uses and prints the suggestion as JSON
for the Test Checklist. It is **read-only** — it inspects marker files only and
never executes a test command.

``branch_review`` is resolved via ``_bootstrap`` — the repo's ``src/`` in
development, the skill's vendored ``lib/`` when installed (ADR-0013).
"""

from __future__ import annotations

import sys

import _bootstrap


def _main() -> int:
    _bootstrap.ensure_package()

    from branch_review.runners import main

    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(_main())
