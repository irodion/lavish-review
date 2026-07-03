#!/usr/bin/env python3
"""Skill entry point for live evidence injection (issue #43).

The real, unit-tested logic lives in ``src/branch_review/evidence.py`` (the single
source of truth). This shim exposes it as the skill's runnable script: when a loop
answer *is* new evidence the page should keep, the agent writes the raw body to a
file and runs this — the fragment is escaped, the post-injection page is linted,
and only then is anything written. A failure writes nothing and the answer
degrades to chat.

Like the collector shim, it imports ``branch_review`` from the repo's ``src/``
(put on ``sys.path`` here) so ``python3 <this script>`` works in a fresh checkout.
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

    from branch_review.evidence import main

    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(_main())
