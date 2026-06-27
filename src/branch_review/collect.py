"""Deterministic Review context collector — the diff-collection layer of the skill.

Runs ``git`` and the Python standard library only (agent-agnostic): it resolves
the Base, computes the ``merge-base(base, HEAD)...HEAD`` diff, and writes the
deterministic Review context files under ``.review-agent/`` that the agent then
authors the Review Cockpit from. See ``DESIGN.md`` and ``CONTEXT.md``.

Walking-skeleton scope (issue #3): happy-path Base auto-detect, the full diff,
and *basic* inline HTML escaping just sufficient to render. The hardened
deterministic Escape Boundary, strict CSP, and post-write lint land in the
hardening slice (issue #4) and must merge before reviewing any untrusted branch.
"""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 — only fixed git argv, shell=False (see _run_git)
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from shutil import copy2

# Base auto-detect fallbacks, tried in order when origin/HEAD is absent.
_BASE_CANDIDATES = ("main", "develop", "master")

# Vendored assets the cockpit references by relative path.
_ASSET_NAMES = ("cockpit.css", "app.js")

_SCHEMA = "review-context/0.1-skeleton"


class GitError(RuntimeError):
    """A ``git`` invocation failed."""


class BaseResolutionError(RuntimeError):
    """The Base could not be auto-detected; the reviewer must name one."""


def _run_git(args: list[str], cwd: Path, *, check: bool = True) -> str:
    """Run ``git <args>`` in ``cwd`` and return stripped stdout.

    Fixed git argv, ``shell=False``, no user-controlled binary — safe by
    construction; the ``# nosec`` waives Bandit's blanket subprocess warnings.
    """
    proc = subprocess.run(  # nosec B603 B607
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout.strip()


def _ref_exists(ref: str, cwd: Path) -> bool:
    """True if ``ref`` resolves to a commit."""
    proc = subprocess.run(  # nosec B603 B607
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def repo_root(cwd: Path) -> Path:
    """The top level of the git working tree containing ``cwd``."""
    return Path(_run_git(["rev-parse", "--show-toplevel"], cwd))


def detect_base(cwd: Path) -> str:
    """Auto-detect the Base: ``origin/HEAD`` then ``main``/``develop``/``master``.

    Raises :class:`BaseResolutionError` on ambiguity (DESIGN: ask, don't guess).
    """
    head = _run_git(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"], cwd, check=False)
    if head:
        # Prefer the clean local name (e.g. `main`) when it exists; otherwise keep
        # the remote-tracking ref (`origin/main`) so a remote-only default branch —
        # common in feature-only checkouts — still resolves.
        local = head.removeprefix("refs/remotes/origin/")
        if _ref_exists(local, cwd):
            return local
        return head.removeprefix("refs/remotes/")
    for candidate in _BASE_CANDIDATES:
        if _ref_exists(candidate, cwd):
            return candidate
    raise BaseResolutionError(
        "Could not auto-detect the Base (no origin/HEAD and no main/develop/master). "
        "Re-run with an explicit base, e.g. `/review-branch <base>`."
    )


def _changed_files(base: str, cwd: Path) -> list[dict[str, str]]:
    """Parse ``git diff --name-status base...HEAD`` into status/path records."""
    out = _run_git(["diff", "--name-status", f"{base}...HEAD"], cwd)
    files: list[dict[str, str]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        status = parts[0]
        if status.startswith(("R", "C")) and len(parts) >= 3:
            files.append({"status": status, "path": parts[2], "old_path": parts[1]})
        elif len(parts) >= 2:
            files.append({"status": status, "path": parts[1]})
    return files


@dataclass(frozen=True)
class ReviewContext:
    """Metadata describing one collected Review (written to ``context.json``)."""

    schema: str
    base: str
    base_sha: str
    branch: str
    head_sha: str
    merge_base: str
    diff_range: str
    generated_at: str
    changed_file_count: int
    is_empty: bool


def _build_context(base: str, cwd: Path, *, now: datetime) -> tuple[ReviewContext, str]:
    """Resolve revs for ``base...HEAD`` and the Branch Under Review."""
    if not _ref_exists(base, cwd):
        raise BaseResolutionError(f"Base ref {base!r} does not resolve to a commit.")
    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    if branch == "HEAD":  # detached — identify by short SHA rather than guess a name
        branch = _run_git(["rev-parse", "--short", "HEAD"], cwd)
    merge_base = _run_git(["merge-base", base, "HEAD"], cwd)
    head_sha = _run_git(["rev-parse", "HEAD"], cwd)
    base_sha = _run_git(["rev-parse", base], cwd)
    files = _changed_files(base, cwd)
    context = ReviewContext(
        schema=_SCHEMA,
        base=base,
        base_sha=base_sha,
        branch=branch,
        head_sha=head_sha,
        merge_base=merge_base,
        diff_range=f"{base}...HEAD",
        generated_at=now.isoformat(),
        changed_file_count=len(files),
        is_empty=not files,
    )
    return context, json.dumps(files, indent=2) + "\n"


def _diff_fragment(diff_text: str) -> str:
    """A render-safe ``<pre>`` fragment of the unified diff.

    Skeleton-grade escaping (``html.escape``) — enough to render attacker text as
    text. The hardened Escape Boundary (issue #4) supersedes this.
    """
    body = escape(diff_text) if diff_text else "(no changes in this range)"
    return f'<pre class="diff">{body}</pre>\n'


def copy_assets(assets_dir: Path, dest_dir: Path) -> list[str]:
    """Copy the vendored cockpit assets into ``dest_dir`` for relative reference."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in _ASSET_NAMES:
        source = assets_dir / name
        if not source.is_file():
            raise FileNotFoundError(f"Vendored asset missing: {source}")
        copy2(source, dest_dir / name)
        copied.append(name)
    return copied


def collect(
    cwd: Path,
    *,
    base: str | None = None,
    out_dir: Path | None = None,
    assets_dir: Path | None = None,
    now: datetime | None = None,
) -> ReviewContext:
    """Collect the Review context for the current branch into ``out_dir``.

    Resolves the Base (explicit ``base`` wins over auto-detect), computes the
    ``base...HEAD`` diff, and writes the deterministic context files. ``review.html``
    is intentionally NOT written here — the agent authors it (ADR-0001).
    """
    root = repo_root(cwd)
    resolved_base = base or detect_base(root)
    out = out_dir or (root / ".review-agent")
    out.mkdir(parents=True, exist_ok=True)

    context, files_json = _build_context(resolved_base, root, now=now or datetime.now(UTC))
    diff_text = _run_git(["diff", context.diff_range], root)
    diff_stat = _run_git(["diff", "--stat", context.diff_range], root)
    commits = _run_git(["log", "--oneline", f"{resolved_base}..HEAD"], root)

    # Always UTF-8 so non-ASCII diffs/paths/messages round-trip deterministically
    # regardless of the platform default encoding, and so the HTML fragment is the
    # UTF-8 the cockpit's <meta charset="utf-8"> promises the browser.
    context_json = json.dumps(asdict(context), indent=2) + "\n"
    (out / "context.json").write_text(context_json, encoding="utf-8")
    (out / "changed-files.json").write_text(files_json, encoding="utf-8")
    (out / "diff.patch").write_text(diff_text + ("\n" if diff_text else ""), encoding="utf-8")
    (out / "diff-stat.txt").write_text(diff_stat + "\n", encoding="utf-8")
    (out / "commits.txt").write_text(commits + "\n", encoding="utf-8")
    (out / "diff.fragment.html").write_text(_diff_fragment(diff_text), encoding="utf-8")

    if assets_dir is not None:
        copy_assets(assets_dir, out / "assets")

    return context


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the skill's ``collect_review_context.py``."""
    parser = argparse.ArgumentParser(
        prog="collect_review_context",
        description="Collect the deterministic Review context (Base diff) into .review-agent/.",
    )
    parser.add_argument("base", nargs="?", help="Base to compare against (default: auto-detect).")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repo path (default: cwd).")
    parser.add_argument(
        "--out", type=Path, default=None, help="Output dir (default: <repo>/.review-agent)."
    )
    parser.add_argument(
        "--assets-dir",
        type=Path,
        default=None,
        help="Vendored assets dir to copy into <out>/assets.",
    )
    args = parser.parse_args(argv)

    try:
        context = collect(args.repo, base=args.base, out_dir=args.out, assets_dir=args.assets_dir)
    except (GitError, BaseResolutionError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    out = args.out or (repo_root(args.repo) / ".review-agent")
    print(f"Review context written to {out}")
    print(f"  base={context.base} branch={context.branch} files={context.changed_file_count}")
    if context.is_empty:
        print("  (no changes in range — nothing to review)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
