#!/usr/bin/env python3
"""Skill entry point for live evidence injection (issue #43).

The real, unit-tested logic lives in ``src/branch_review/evidence.py`` (the single
source of truth). This shim exposes it as the skill's runnable script: when a loop
answer *is* new evidence the page should keep, the agent writes the raw body to a
file and runs this — the fragment is escaped, the post-injection page is linted,
and only then is anything written. A failure writes nothing and the answer
degrades to chat.

``branch_review`` is resolved via ``_bootstrap`` — the repo's ``src/`` in
development, the skill's vendored ``lib/`` when installed (ADR-0013).
"""

from __future__ import annotations

import sys

import _bootstrap


def _main() -> int:
    _bootstrap.ensure_package()

    from branch_review.evidence import main

    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(_main())
