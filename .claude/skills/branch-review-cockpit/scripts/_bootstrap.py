"""Locate the ``branch_review`` package for the skill's script shims (ADR-0013).

The skill runs in two homes:

- **Development** — this repo, where ``src/branch_review/`` is the single source
  of truth (the tested code). Recognized not by ``src/`` alone (a target repo
  could have its own ``src/branch_review``) but by the sync tool sitting beside
  it — ``tools/sync_vendored.py`` only exists in the lavish-review repo.
- **Installed** — the skill directory copied verbatim into another repo by
  ``npx skills add``, where the package is the skill's own vendored
  ``lib/branch_review/`` (kept equality-pinned to ``src/`` by
  ``tests/test_packaging.py``).

Shims import this module (the scripts directory is on ``sys.path`` when a shim
is executed as a file) and call :func:`ensure_package` before importing
``branch_review``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
# .../<repo>/.claude/skills/branch-review-cockpit/scripts/_bootstrap.py
_SKILL = _HERE.parents[1]
_REPO = _HERE.parents[4]


def package_root() -> Path | None:
    """The directory to put on ``sys.path``: dev ``src/``, else vendored ``lib/``."""
    dev_src = _REPO / "src"
    if (dev_src / "branch_review").is_dir() and (_REPO / "tools" / "sync_vendored.py").is_file():
        return dev_src
    vendored = _SKILL / "lib"
    if (vendored / "branch_review").is_dir():
        return vendored
    return None


def ensure_package() -> None:
    """Make ``branch_review`` importable, or fail with a message that names the fix."""
    root = package_root()
    if root is None:
        raise SystemExit(
            "error: cannot find the branch_review package — expected the repo's "
            "src/branch_review (development) or the skill's lib/branch_review "
            "(installed; re-install with `npx skills add`)."
        )
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
