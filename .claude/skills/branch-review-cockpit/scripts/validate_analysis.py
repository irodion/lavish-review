#!/usr/bin/env python3
"""Skill entry point for the Analysis Schema Validator (issue #6).

The real, unit-tested logic lives in ``src/branch_review/analysis.py`` (the single
source of truth). This shim exposes it as the skill's runnable script: the agent
runs it on ``.review-agent/analysis.json`` after authoring the analysis and before
authoring the cockpit, so a malformed analysis is caught and fixed first.

``branch_review`` is resolved via ``_bootstrap`` — the repo's ``src/`` in
development, the skill's vendored ``lib/`` when installed (ADR-0013).
"""

from __future__ import annotations

import sys

import _bootstrap


def _main() -> int:
    _bootstrap.ensure_package()

    from branch_review.analysis import main

    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(_main())
