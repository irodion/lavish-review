#!/usr/bin/env python3
"""Skill entry point for Reviewer Dispositions (issue #42, ADR-0012).

The real, unit-tested logic lives in ``src/branch_review/dispositions.py`` (the
single source of truth). This shim exposes it as the skill's runnable script: the
loop runs ``apply`` after a poll whose prompts carry disposition updates — the
deterministic bridge from the reviewer's untrusted feedback to the persisted
store; the agent never hand-parses or hand-writes a disposition.

``branch_review`` is resolved via ``_bootstrap`` — the repo's ``src/`` in
development, the skill's vendored ``lib/`` when installed (ADR-0013).
"""

from __future__ import annotations

import sys

import _bootstrap


def _main() -> int:
    _bootstrap.ensure_package()

    from branch_review.dispositions import main

    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(_main())
