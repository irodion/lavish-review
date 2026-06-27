#!/usr/bin/env python3
"""Skill entry point for session lifecycle — resume & staleness (issue #8).

Like ``collect_review_context.py`` and ``review_loop.py``, this is a thin shim: the
real, unit-tested logic lives in ``src/branch_review/session.py`` (the single source
of truth). It puts the repo's ``src/`` on ``sys.path`` itself so the documented
commands work in a fresh checkout, with or without an editable install. Standalone
packaging is issue #12.

Usage (run from the repo root being reviewed):

    python3 .../scripts/session.py evaluate   # restore vs regenerate (step 0)
    python3 .../scripts/session.py start       # record the open session (after open)
    python3 .../scripts/session.py end         # mark ended (/review-close)

``evaluate`` prints JSON the agent branches on; it never regenerates or opens
anything itself — it only reports how the current branch relates to any saved review.
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

    from branch_review.session import main

    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(_main())
