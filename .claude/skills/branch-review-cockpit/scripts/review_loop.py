#!/usr/bin/env python3
"""Skill entry point for the interactive feedback loop (issue #5).

Like ``collect_review_context.py`` and ``lint_cockpit.py``, this is a thin shim:
the real, unit-tested logic lives in ``src/branch_review/feedback.py`` (the single
source of truth). It puts the repo's ``src/`` on ``sys.path`` itself so the
documented commands work in a fresh checkout, with or without an editable install.
Standalone packaging is issue #12.

Usage (run from the repo root being reviewed):

    python3 .../scripts/review_loop.py poll                 # enter / re-attach the loop
    python3 .../scripts/review_loop.py reply                # show answer, log, re-block
    python3 .../scripts/review_loop.py end                  # /review-close

The answer for ``reply`` is read from ``.review-agent/agent-reply.txt`` — never a
shell argument — so untrusted browser feedback can never construct a command.
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

    from branch_review.feedback import main

    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(_main())
