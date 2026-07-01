#!/usr/bin/env python3
"""Skill entry point for the Q&A bake at close (issue #9).

Like ``collect_review_context.py`` and ``review_loop.py``, this is a thin shim: the
real, unit-tested logic lives in ``src/branch_review/bake.py`` (the single source of
truth). It puts the repo's ``src/`` on ``sys.path`` itself so the documented command
works in a fresh checkout, with or without an editable install. Standalone packaging
is issue #12.

Usage (run from the repo root being reviewed, at ``/review-close``):

    python3 .../scripts/bake_review.py            # fold qa.jsonl into review.html
    python3 .../scripts/bake_review.py --md        # also emit review.md for a PR

The bake folds ``.review-agent/qa.jsonl`` into ``.review-agent/review.html`` (escaped
via the Escape Boundary, idempotent) and swaps the cockpit to the strict CSP so the
saved file is self-contained — it opens in a plain browser with no Lavish running.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
# .../<repo>/.claude/skills/branch-review-cockpit/scripts/<this file>
_REPO_SRC = _HERE.parents[4] / "src"


def _main() -> int:
    if _REPO_SRC.is_dir() and str(_REPO_SRC) not in sys.path:
        sys.path.insert(0, str(_REPO_SRC))

    from branch_review.bake import main

    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(_main())
