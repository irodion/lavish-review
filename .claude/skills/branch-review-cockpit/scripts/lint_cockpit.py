#!/usr/bin/env python3
"""Skill entry point for the Cockpit Linter — the post-write hardening tripwire.

Like ``collect_review_context.py``, this is a thin shim: the real, unit-tested
logic lives in ``src/branch_review/lint.py`` (the single source of truth).
``branch_review`` is resolved via ``_bootstrap`` — the repo's ``src/`` in
development, the skill's vendored ``lib/`` when installed (ADR-0013).

Exit status: ``0`` clean, ``1`` lint violations, ``2`` the file could not be read.
"""

from __future__ import annotations

import sys

import _bootstrap


def _main() -> int:
    _bootstrap.ensure_package()

    from branch_review.lint import main

    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(_main())
