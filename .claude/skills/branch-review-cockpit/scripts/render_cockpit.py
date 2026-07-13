#!/usr/bin/env python3
"""Render the collected analysis into a deterministic Review Cockpit."""

from __future__ import annotations

import sys

import _bootstrap


def _main() -> int:
    _bootstrap.ensure_package()

    from branch_review.render import main

    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(_main())
