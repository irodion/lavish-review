#!/usr/bin/env python3
"""Skill entry point for session lifecycle — resume & staleness (issue #8).

Like ``collect_review_context.py`` and ``review_loop.py``, this is a thin shim: the
real, unit-tested logic lives in ``src/branch_review/session.py`` (the single source
of truth). ``branch_review`` is resolved via ``_bootstrap`` — the repo's ``src/`` in
development, the skill's vendored ``lib/`` when installed (ADR-0013).

Usage (run from the repo root being reviewed):

    python3 .../scripts/session.py evaluate   # restore vs regenerate (step 0)
    python3 .../scripts/session.py start       # record the open session (after open)
    python3 .../scripts/session.py resume      # bump the recap resume signal (/review-resume)
    python3 .../scripts/session.py end         # mark ended (/review-close)

``evaluate`` prints JSON the agent branches on; it never regenerates or opens
anything itself — it only reports how the current branch relates to any saved review.
"""

from __future__ import annotations

import sys

import _bootstrap


def _main() -> int:
    _bootstrap.ensure_package()

    from branch_review.session import main

    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(_main())
