#!/usr/bin/env python3
"""Skill entry point for the Cockpit Linter — the post-write hardening tripwire.

Like ``collect_review_context.py``, this is a thin shim: the real, unit-tested
logic lives in ``src/branch_review/lint.py`` (the single source of truth). It puts
the repo's ``src/`` on ``sys.path`` itself so the documented
``python3 <this script> .review-agent/review.html`` works in a fresh checkout,
with or without an editable install. Standalone packaging is issue #12.

Exit status: ``0`` clean, ``1`` lint violations, ``2`` the file could not be read.
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

    from branch_review.lint import main

    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(_main())
