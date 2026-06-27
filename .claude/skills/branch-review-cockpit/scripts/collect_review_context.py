#!/usr/bin/env python3
"""Skill entry point for the deterministic Review context collector.

Walking-skeleton (issue #3): the real, unit-tested logic lives in
``src/branch_review/collect.py`` and is the single source of truth. This shim
exposes it as the skill's runnable script and defaults ``--assets-dir`` to the
vendored assets that sit beside this file, so the collector copies
``cockpit.css``/``app.js`` into ``.review-agent/assets/`` for relative reference.

Packaging this into a standalone, dependency-free skill bundle is issue #12; for
the dev-only skeleton it imports ``branch_review`` from the repo's ``src/``, which
it puts on ``sys.path`` itself — so the documented ``python3 <this script>`` works
in a fresh checkout, with or without an editable install.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_ASSETS = _HERE.parent.parent / "assets"
# .../<repo>/.claude/skills/branch-review-cockpit/scripts/<this file>
_REPO_SRC = _HERE.parents[4] / "src"


def _main() -> int:
    if _REPO_SRC.is_dir() and str(_REPO_SRC) not in sys.path:
        sys.path.insert(0, str(_REPO_SRC))

    from branch_review.collect import main

    argv = sys.argv[1:]
    # Default to the vendored assets unless the caller named one — accept both the
    # `--assets-dir VALUE` and `--assets-dir=VALUE` argparse forms.
    if not any(arg == "--assets-dir" or arg.startswith("--assets-dir=") for arg in argv):
        argv = [*argv, "--assets-dir", str(_ASSETS)]
    return main(argv)


if __name__ == "__main__":
    raise SystemExit(_main())
