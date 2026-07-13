#!/usr/bin/env python3
"""Render the collected analysis into a deterministic Review Cockpit."""

from _bootstrap import ensure_package

ensure_package()

from branch_review.render import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
