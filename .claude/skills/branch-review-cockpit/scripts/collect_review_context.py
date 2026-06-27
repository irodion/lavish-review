#!/usr/bin/env python3
"""Skill entry point for the deterministic Review context collector.

Walking-skeleton (issue #3): the real, unit-tested logic lives in
``src/branch_review/collect.py`` and is the single source of truth. This shim
exposes it as the skill's runnable script and defaults ``--assets-dir`` to the
vendored assets that sit beside this file, so the collector copies
``cockpit.css``/``app.js`` into ``.review-agent/assets/`` for relative reference.

Packaging this into a standalone, dependency-free skill bundle is issue #12; for
the dev-only skeleton it runs from a repo with ``branch_review`` importable
(``pip install -e .``).
"""

from __future__ import annotations

import sys
from pathlib import Path

_ASSETS = Path(__file__).resolve().parent.parent / "assets"


def _main() -> int:
    from branch_review.collect import main

    argv = sys.argv[1:]
    if not any(arg == "--assets-dir" for arg in argv):
        argv = [*argv, "--assets-dir", str(_ASSETS)]
    return main(argv)


if __name__ == "__main__":
    raise SystemExit(_main())
