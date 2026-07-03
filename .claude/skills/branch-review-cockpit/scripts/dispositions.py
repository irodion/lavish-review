#!/usr/bin/env python3
"""Skill entry point for Reviewer Dispositions (issue #42, ADR-0012).

The real, unit-tested logic lives in ``src/branch_review/dispositions.py`` (the
single source of truth). This shim exposes it as the skill's runnable script: the
loop runs ``apply`` after a poll whose prompts carry disposition updates — the
deterministic bridge from the reviewer's untrusted feedback to the persisted
store; the agent never hand-parses or hand-writes a disposition.

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

    from branch_review.dispositions import main

    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(_main())
