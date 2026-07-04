#!/usr/bin/env python3
"""Skill entry point for the interactive feedback loop (issue #5).

Like ``collect_review_context.py`` and ``lint_cockpit.py``, this is a thin shim:
the real, unit-tested logic lives in ``src/branch_review/feedback.py`` (the single
source of truth). ``branch_review`` is resolved via ``_bootstrap`` — the repo's ``src/`` in
development, the skill's vendored ``lib/`` when installed (ADR-0013).

Usage (run from the repo root being reviewed):

    python3 .../scripts/review_loop.py poll                 # enter / re-attach the loop
    python3 .../scripts/review_loop.py reply                # show answer, log, re-block
    python3 .../scripts/review_loop.py end                  # /review-close

The answer for ``reply`` is read from ``.review-agent/agent-reply.txt`` — never a
shell argument — so untrusted browser feedback can never construct a command.
"""

from __future__ import annotations

import sys

import _bootstrap


def _main() -> int:
    _bootstrap.ensure_package()

    from branch_review.feedback import main

    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(_main())
