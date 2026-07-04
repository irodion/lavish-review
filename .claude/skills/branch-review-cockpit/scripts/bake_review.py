#!/usr/bin/env python3
"""Skill entry point for the Q&A bake at close (issue #9).

Like ``collect_review_context.py`` and ``review_loop.py``, this is a thin shim: the
real, unit-tested logic lives in ``src/branch_review/bake.py`` (the single source of
truth). ``branch_review`` is resolved via ``_bootstrap`` — the repo's ``src/`` in
development, the skill's vendored ``lib/`` when installed (ADR-0013).

Usage (run from the repo root being reviewed, at ``/review-close``):

    python3 .../scripts/bake_review.py            # fold qa.jsonl into review.html
    python3 .../scripts/bake_review.py --md        # also emit review.md for a PR

The bake folds ``.review-agent/qa.jsonl`` into ``.review-agent/review.html`` (escaped
via the Escape Boundary, idempotent) and swaps the cockpit to the strict CSP so the
saved file is self-contained — it opens in a plain browser with no Lavish running.
"""

from __future__ import annotations

import sys

import _bootstrap


def _main() -> int:
    _bootstrap.ensure_package()

    from branch_review.bake import main

    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(_main())
