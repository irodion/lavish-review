"""Deterministic Review context collector — the diff-collection layer of the skill.

Runs ``git`` and the Python standard library only (agent-agnostic): it resolves
the Base, computes the ``merge-base(base, HEAD)...HEAD`` diff, and writes the
deterministic Review context files under ``.review-agent/`` that the agent then
authors the Review Cockpit from. See ``DESIGN.md`` and ``CONTEXT.md``.

All untrusted data (diff bodies, file paths, commit messages, branch names) is
emitted through the deterministic Escape Boundary (:mod:`branch_review.escape`,
ADR-0002) as pre-escaped, marker-delimited fragments — ``diff.fragment.html`` and
``fragments.html`` — that the agent injects verbatim; the agent never
hand-interpolates a raw untrusted string.
"""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 — only fixed git argv, shell=False (see _run_git)
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from shutil import copy2, rmtree

from branch_review.escape import (
    FRAGMENTS_DIRNAME,
    build_fragments,
    diff_fragment,
    fragment_index_entry,
)

# Base auto-detect fallbacks, tried in order when origin/HEAD is absent.
_BASE_CANDIDATES = ("main", "develop", "master")

# Vendored assets the cockpit references by relative path.
_ASSET_NAMES = ("cockpit.css", "app.js")

_SCHEMA = "review-context/0.1-skeleton"
_FRAGMENTS_SCHEMA = "review-fragments/0.1"


class GitError(RuntimeError):
    """A ``git`` invocation failed."""


class BaseResolutionError(RuntimeError):
    """The Base could not be auto-detected; the reviewer must name one."""


def _run_git(args: list[str], cwd: Path, *, check: bool = True) -> str:
    """Run ``git <args>`` in ``cwd`` and return stripped stdout.

    Fixed git argv, ``shell=False``, no user-controlled binary — safe by
    construction; the ``# nosec`` waives Bandit's blanket subprocess warnings.
    """
    # ``-c core.quotePath=false`` keeps non-ASCII paths as raw UTF-8 instead of
    # git's default C-quoted ``"\360\237..."`` octal form, so a changed-files path
    # round-trips verbatim — both as a JSON key the agent reads and as the pathspec
    # the per-file fragment diff (#21) feeds back to ``git diff``.
    # ``--literal-pathspecs`` makes git treat every pathspec argument as an exact
    # filename: a changed file literally named ``:(glob)*.py`` or ``a*.py`` must not
    # be reinterpreted as pathspec magic / a wildcard when fed back to ``git diff``,
    # which would match the wrong files (or none) for its fragment.
    # ``encoding="utf-8"`` decodes stdout/stderr as UTF-8 explicitly rather than via
    # the platform locale, so non-ASCII paths aren't mangled under a C/latin-1 locale.
    proc = subprocess.run(  # nosec B603 B607
        ["git", "--literal-pathspecs", "-c", "core.quotePath=false", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
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

    Prefer the remote default's remote-tracking ref (e.g. ``origin/main``) over a
    same-named local branch. The diff is ``merge-base(base, HEAD)...HEAD``, so a
    stale local ``main`` (behind ``origin/main``) would push the merge-base back
    and surface already-merged base commits in the cockpit as false positives.
    Local ``main``/``develop``/``master`` are only the fallback when there is no
    ``origin/HEAD``.

    Raises :class:`BaseResolutionError` on ambiguity (DESIGN: ask, don't guess).
    """
    head = _run_git(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"], cwd, check=False)
    if head:
        # Documented precedence: origin/HEAD first. Keep its remote-tracking ref
        # (origin/main) rather than the local branch of the same name, falling
        # through only if the remote default somehow doesn't resolve.
        remote_default = head.removeprefix("refs/remotes/")
        if _ref_exists(remote_default, cwd):
            return remote_default
    for candidate in _BASE_CANDIDATES:
        if _ref_exists(candidate, cwd):
            return candidate
    raise BaseResolutionError(
        "Could not auto-detect the Base (no origin/HEAD and no main/develop/master). "
        "Re-run with an explicit base, e.g. `/review-branch <base>`."
    )


def _changed_files(base: str, cwd: Path) -> list[dict[str, str]]:
    """Parse ``git diff --name-status -z base...HEAD`` into status/path records.

    NUL-delimited (``-z``) so paths round-trip verbatim. In the default line/tab
    format git C-quotes any path containing a tab or newline **regardless of**
    ``core.quotePath`` (those bytes would otherwise corrupt the format), which would
    break both this parse and the later path-scoped per-file diff — the file's body
    would silently vanish from the walkthrough (violating "nothing omitted is ever
    hidden"). With ``-z`` the status and each path are separate NUL-terminated
    fields and paths are emitted raw.
    """
    out = _run_git(["diff", "--name-status", "-z", f"{base}...HEAD"], cwd)
    tokens = out.split("\0")
    files: list[dict[str, str]] = []
    i = 0
    while i < len(tokens) and tokens[i]:  # final NUL yields a trailing "" → stop
        status = tokens[i]
        # Rename/copy carries two NUL-separated paths (old, new); else just one.
        if status.startswith(("R", "C")) and i + 2 < len(tokens):
            files.append({"status": status, "path": tokens[i + 2], "old_path": tokens[i + 1]})
            i += 3
        elif i + 1 < len(tokens):
            files.append({"status": status, "path": tokens[i + 1]})
            i += 2
        else:
            break
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


def _build_context(
    base: str, cwd: Path, *, now: datetime
) -> tuple[ReviewContext, list[dict[str, str]]]:
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
    return context, files


def _per_file_diff(diff_range: str, record: dict[str, str], cwd: Path) -> str:
    """The unified diff for a single changed file (path-scoped ``git diff``).

    Scoping by pathspec gives an authoritative path→hunk association — no fragile
    re-parsing of the combined diff to guess file boundaries. For a rename/copy we
    pass **both** the old and new path so git emits the rename header and any
    content delta together.
    """
    old_path = record.get("old_path")
    pathspec = [old_path, record["path"]] if old_path is not None else [record["path"]]
    return _run_git(["diff", diff_range, "--", *pathspec], cwd)


def _write_file_fragments(
    out: Path, diff_range: str, files: list[dict[str, str]], cwd: Path
) -> list[dict[str, object]]:
    """Write one escaped ``fragments/<id>.html`` per changed file + the ordered index.

    The substrate the File Walkthrough / Review Route (issue #6) consume: each
    file's hunk is escaped independently through the boundary, keyed by a
    traversal-safe id, and the returned records preserve ``changed-files.json``
    order so the agent can walk files in a deliberate route. The ``fragments/``
    dir is rebuilt from scratch each run so a prior review's files never linger as
    orphans in the new index (**nothing shown that isn't in this range**).
    """
    frag_dir = out / FRAGMENTS_DIRNAME
    if frag_dir.exists():
        rmtree(frag_dir)
    frag_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, object]] = []
    seen: dict[str, str] = {}
    for record in files:
        entry = fragment_index_entry(record)  # body omitted/reason is issue #7's call
        fid = str(entry["id"])
        if seen.get(fid, record["path"]) != record["path"]:
            raise GitError(f"fragment id collision on {fid!r}: {seen[fid]!r} vs {record['path']!r}")
        seen[fid] = record["path"]
        diff_text = _per_file_diff(diff_range, record, cwd)
        (frag_dir / f"{fid}.html").write_text(diff_fragment(diff_text), encoding="utf-8")
        entries.append(entry)
    return entries


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

    context, files = _build_context(resolved_base, root, now=now or datetime.now(UTC))
    diff_text = _run_git(["diff", context.diff_range], root)
    diff_stat = _run_git(["diff", "--stat", context.diff_range], root)
    commits = _run_git(["log", "--oneline", f"{resolved_base}..HEAD"], root)
    commit_lines = commits.splitlines()

    # Untrusted data crosses the Escape Boundary here: the diff and the
    # path/commit/branch fragments are pre-escaped and marker-delimited so the
    # agent injects them verbatim and the Cockpit Linter can prove they are safe.
    fragments = build_fragments(
        branch=context.branch,
        base=context.base,
        head_sha=context.head_sha,
        changed_file_count=context.changed_file_count,
        files=files,
        commit_lines=commit_lines,
    )

    # Always UTF-8 so non-ASCII diffs/paths/messages round-trip deterministically
    # regardless of the platform default encoding, and so the HTML fragment is the
    # UTF-8 the cockpit's <meta charset="utf-8"> promises the browser.
    context_json = json.dumps(asdict(context), indent=2) + "\n"
    files_json = json.dumps(files, indent=2) + "\n"
    (out / "context.json").write_text(context_json, encoding="utf-8")
    (out / "changed-files.json").write_text(files_json, encoding="utf-8")
    (out / "diff.patch").write_text(diff_text + ("\n" if diff_text else ""), encoding="utf-8")
    (out / "diff-stat.txt").write_text(diff_stat + "\n", encoding="utf-8")
    (out / "commits.txt").write_text(commits + "\n", encoding="utf-8")
    (out / "diff.fragment.html").write_text(diff_fragment(diff_text), encoding="utf-8")
    (out / "fragments.html").write_text(fragments, encoding="utf-8")

    # Per-file escaped fragments + ordered index (issue #21) — the File Walkthrough
    # substrate. The whole-diff diff.fragment.html above is preserved unchanged.
    fragment_index = _write_file_fragments(out, context.diff_range, files, root)
    fragments_json = (
        json.dumps(
            {
                "schema": _FRAGMENTS_SCHEMA,
                "diff_range": context.diff_range,
                "files": fragment_index,
            },
            indent=2,
        )
        + "\n"
    )
    (out / "fragments.json").write_text(fragments_json, encoding="utf-8")

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
