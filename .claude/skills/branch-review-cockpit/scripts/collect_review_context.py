#!/usr/bin/env python3
"""Skill entry point for the deterministic Review context collector.

Walking-skeleton (issue #3): the real, unit-tested logic lives in
``src/branch_review/collect.py`` and is the single source of truth. This shim
exposes it as the skill's runnable script and defaults ``--assets-dir`` to the
vendored assets that sit beside this file, so the collector copies
``cockpit.css``/``app.js`` into ``.review-agent/assets/`` for relative reference.

``branch_review`` is resolved via ``_bootstrap`` — the repo's ``src/`` in
development, the skill's vendored ``lib/`` when installed (ADR-0013).
"""

from __future__ import annotations

import sys
from pathlib import Path

import _bootstrap

_ASSETS = Path(__file__).resolve().parent.parent / "assets"


def _main() -> int:
    _bootstrap.ensure_package()

    from branch_review.collect import main

    argv = sys.argv[1:]
    # Default to the vendored assets unless the caller named one — accept both the
    # `--assets-dir VALUE` and `--assets-dir=VALUE` argparse forms.
    if not any(arg == "--assets-dir" or arg.startswith("--assets-dir=") for arg in argv):
        argv = [*argv, "--assets-dir", str(_ASSETS)]
    return main(argv)


if __name__ == "__main__":
    raise SystemExit(_main())
