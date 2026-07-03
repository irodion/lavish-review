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
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from shutil import copy2, rmtree

from branch_review.classify import (
    ChangesetDisposition,
    Classification,
    ClassifierConfig,
    FileStats,
    classify_changeset,
    classify_file,
    downgrade_to_listing,
)
from branch_review.config import ConfigError, resolve_config, resolved_config_dict
from branch_review.escape import (
    FRAGMENTS_DIRNAME,
    build_fragments,
    diff_fragment,
    fragment_index_entry,
    notice_fragment,
)
from branch_review.feedback import RUN_SCOPED_ARTIFACTS

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


def _run_git(args: list[str], cwd: Path, *, check: bool = True, strip: bool = True) -> str:
    """Run ``git <args>`` in ``cwd`` and return its stdout (stripped by default).

    Fixed git argv, ``shell=False``, no user-controlled binary — safe by
    construction; the ``# nosec`` waives Bandit's blanket subprocess warnings.

    ``strip`` trims surrounding whitespace — right for plumbing output (SHAs, refs,
    ``-z`` records the parser re-splits anyway). Pass ``strip=False`` for **diff
    bodies**: ``.strip()`` would eat trailing whitespace on the diff's last line, and
    the cockpit must show the reviewed change byte-for-byte — a trailing-whitespace
    edit is exactly the kind of change a reviewer needs to see, not one the tool
    silently erases.
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
    return proc.stdout.strip() if strip else proc.stdout


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


def current_revision(cwd: Path) -> tuple[str, str]:
    """The working tree's current ``(head_sha, branch)`` — what the Session Evaluator compares.

    ``head_sha`` is the full ``HEAD`` SHA; ``branch`` is the symbolic branch name, or —
    on a detached HEAD, where ``--abbrev-ref`` reports the literal ``HEAD`` — the short
    SHA, so a detached review is identified consistently rather than by a guessed name.
    This is the same pair :func:`_build_context` records into ``context.json`` /
    ``session.json``; both go through here so a generated review and a later staleness
    check read the branch and HEAD identically (issue #8).
    """
    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    if branch == "HEAD":  # detached — identify by short SHA rather than guess a name
        branch = _run_git(["rev-parse", "--short", "HEAD"], cwd)
    head_sha = _run_git(["rev-parse", "HEAD"], cwd)
    return head_sha, branch


def merge_base(base: str, cwd: Path) -> str:
    """The merge-base of ``base`` and ``HEAD`` — the commit the ``base...HEAD`` diff anchors to.

    The three-dot diff the cockpit shows is exactly ``merge_base..HEAD``, so this commit
    (together with ``HEAD``) *is* the identity of the reviewed diff. The Session Evaluator
    compares it (issue #8) so a base that was switched or has advanced under a fixed branch
    HEAD — which silently changes the diff — is caught as stale rather than restored.
    """
    return _run_git(["merge-base", base, "HEAD"], cwd)


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
    head_sha, branch = current_revision(cwd)
    merge_base_sha = merge_base(base, cwd)
    base_sha = _run_git(["rev-parse", base], cwd)
    files = _changed_files(base, cwd)
    context = ReviewContext(
        schema=_SCHEMA,
        base=base,
        base_sha=base_sha,
        branch=branch,
        head_sha=head_sha,
        merge_base=merge_base_sha,
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
    # strip=False: preserve the body verbatim, including trailing whitespace.
    return _run_git(["diff", diff_range, "--", *pathspec], cwd, strip=False)


def _file_stats(diff_range: str, files: list[dict[str, str]], cwd: Path) -> dict[str, FileStats]:
    """Per-file added/deleted line counts from ``git diff --numstat -z``.

    Keyed by the same path ``_changed_files`` uses (the new path for a rename), so a
    stats lookup always lines up with a changed-files record. ``-z`` keeps paths raw
    and switches renames to a ``added<TAB>deleted<TAB>\\0old\\0new`` triple (empty
    path field, then old then new) instead of the line/tab ``{old => new}`` form, so
    the parse is unambiguous. Binary files report ``-``/``-`` and become
    ``FileStats(binary=True)`` with zero counts — they have no line body to cap.
    """
    if not files:
        return {}
    out = _run_git(["diff", "--numstat", "-z", diff_range], cwd)
    tokens = out.split("\0")
    stats: dict[str, FileStats] = {}
    i = 0
    while i < len(tokens) and tokens[i]:  # trailing NUL yields "" → stop
        parts = tokens[i].split("\t")
        if len(parts) != 3:
            break
        added_s, deleted_s, path = parts
        if path == "" and i + 2 < len(tokens):
            # Rename/copy: counts here, old + new path in the next two NUL fields.
            path = tokens[i + 2]
            i += 3
        else:
            i += 1
        binary = added_s == "-" or deleted_s == "-"
        stats[path] = FileStats(
            added=0 if binary else int(added_s),
            deleted=0 if binary else int(deleted_s),
            binary=binary,
        )
    return stats


def _generated_paths(files: list[dict[str, str]], cwd: Path) -> set[str]:
    """Paths the working tree's ``.gitattributes`` marks ``linguist-generated``.

    Asks git itself (``git check-attr -z linguist-generated``) rather than parsing
    ``.gitattributes`` here, so nested and inherited attribute files are honored
    exactly as git resolves them. Output is ``path\\0attr\\0value`` triples; a value
    of ``set`` means generated. Querying by pathname needs no file on disk, so a
    file deleted at HEAD is still classified by the attribute that covers its path.

    ``strip=False``: this stream *starts* with the raw pathname, so a blanket
    ``.strip()`` would eat a leading space from a filename like ``" generated.py"``
    before the parser builds the key — the file would then never match its
    changed-files record and its generated body would slip through. (``--numstat -z``
    starts with the counts, so it has no such failure mode.)
    """
    if not files:
        return set()
    paths = [record["path"] for record in files]
    out = _run_git(["check-attr", "-z", "linguist-generated", "--", *paths], cwd, strip=False)
    tokens = out.split("\0")
    generated: set[str] = set()
    # Walk fixed-width triples; a trailing "" from the final NUL is ignored.
    for i in range(0, len(tokens) - 2, 3):
        path, _attr, value = tokens[i], tokens[i + 1], tokens[i + 2]
        if value == "set":
            generated.add(path)
    return generated


def _write_file_fragments(
    out: Path,
    diff_range: str,
    files: list[dict[str, str]],
    cwd: Path,
    config: ClassifierConfig,
) -> tuple[list[dict[str, object]], ChangesetDisposition]:
    """Write an escaped ``fragments/<id>.html`` per *included* file + the ordered index.

    The substrate the File Walkthrough / Review Route (issue #6) consume, now gated
    by the Change Classifier (issue #7). Each file is classified from its stats: an
    ``include-body`` file gets its hunk escaped independently through the boundary
    and a fragment on disk; an omitted file (lockfile, excluded, generated, or
    over-cap) gets **no** body but still appears in the index with its status, stats,
    and a required reason — **nothing omitted is ever hidden** (DESIGN). A final
    total-diff guard downgrades every still-included file to a stats-only listing
    when the combined body would be too large, so a huge branch degrades to a file
    list + stats banner rather than a silent truncation. The returned records
    preserve ``changed-files.json`` order; the ``fragments/`` dir is rebuilt each run
    so a prior review's files never linger as orphans (**nothing shown that isn't in
    this range**).
    """
    frag_dir = out / FRAGMENTS_DIRNAME
    if frag_dir.exists():
        rmtree(frag_dir)
    frag_dir.mkdir(parents=True, exist_ok=True)

    stats_by_path = _file_stats(diff_range, files, cwd)
    generated = _generated_paths(files, cwd)

    # First pass: classify every file from its (deterministic) stats.
    classified: list[tuple[str, FileStats, Classification]] = []
    for record in files:
        path = record["path"]
        base_stats = stats_by_path.get(path, FileStats())
        stats = replace(base_stats, linguist_generated=True) if path in generated else base_stats
        classified.append((path, stats, classify_file(path, stats, config)))

    # Total-diff guard: if the included bodies together blow the total cap, fall
    # back to a file-list + stats listing — every still-included file is downgraded.
    changeset = classify_changeset(classified, config)
    if changeset.too_large and changeset.reason is not None:
        fallback_reason = changeset.reason
        classified = [
            (path, stats, downgrade_to_listing(classification, fallback_reason))
            for path, stats, classification in classified
        ]

    entries: list[dict[str, object]] = []
    seen: dict[str, str] = {}
    for record, (_path, stats, classification) in zip(files, classified, strict=True):
        omitted = classification.omitted
        entry = fragment_index_entry(
            record,
            omitted=omitted,
            reason=classification.reason or None,
            disposition=classification.disposition.value,
            # Existence and stats are never dropped — only bodies (issue #7).
            stats={"added": stats.added, "deleted": stats.deleted, "binary": stats.binary},
        )
        fid = str(entry["id"])
        if seen.get(fid, record["path"]) != record["path"]:
            raise GitError(f"fragment id collision on {fid!r}: {seen[fid]!r} vs {record['path']!r}")
        seen[fid] = record["path"]
        if not omitted:
            diff_text = _per_file_diff(diff_range, record, cwd)
            (frag_dir / f"{fid}.html").write_text(diff_fragment(diff_text), encoding="utf-8")
        entries.append(entry)
    return entries, changeset


def _reset_run_scoped_artifacts(out_dir: Path) -> None:
    """Delete the prior session's feedback-loop transcript so a regenerated Review is clean.

    ``collect`` runs only when a cockpit is (re)generated — never on a no-regeneration
    resume — so clearing ``qa.jsonl``/``last-poll.toon``/``agent-reply.txt`` here is what
    keeps a stale or different-branch regeneration from folding a previous session's Q&A
    into the new ``review.html``/``review.md`` at close (the bake reads the default
    ``qa.jsonl`` beside the cockpit). A ``fresh`` resume keeps them because it does not
    call ``collect``. ``missing_ok`` so the ordinary "no prior transcript" case is a no-op.
    """
    for name in RUN_SCOPED_ARTIFACTS:
        (out_dir / name).unlink(missing_ok=True)


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
    config: ClassifierConfig | None = None,
    home: Path | None = None,
    now: datetime | None = None,
) -> ReviewContext:
    """Collect the Review context for the current branch into ``out_dir``.

    Resolves the effective policy via the Config Resolver (issue #10):
    ``base`` arg > repo ``.review-agent.yaml`` ``base_branch`` > auto-detect (asking on
    ambiguity), and the repo's ``exclude``/``exclude_reset``/``limits`` fold into the
    Change Classifier policy. Then computes the ``base...HEAD`` diff and writes the
    deterministic context files (plus ``resolved-config.json``, the resolved styling / lens
    / machine settings the skill threads into authoring, linting, and the open/loop steps).
    ``review.html`` is intentionally NOT written here — the agent authors it (ADR-0001).

    An explicit ``config`` overrides the resolved Change Classifier policy (used in tests);
    ``home`` overrides the machine-config location (also for tests). The base still comes
    from the resolver so an explicit ``config`` doesn't disable repo ``base_branch``.
    """
    root = repo_root(cwd)
    resolved = resolve_config(root, arg_base=base, home=home)
    classifier_config = config or resolved.classifier
    resolved_base = resolved.base_branch or detect_base(root)
    out = out_dir or (root / ".review-agent")
    out.mkdir(parents=True, exist_ok=True)

    context, files = _build_context(resolved_base, root, now=now or datetime.now(UTC))
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

    # Classify per-file and resolve the total-diff verdict (issue #7) *before*
    # materializing any whole-diff body. The fallback must suppress the whole-diff
    # artifacts too — not only the per-file fragments — or a huge branch would still
    # dump the entire unified diff into diff.patch / diff.fragment.html. Running this
    # first also means we never build the giant diff we would only throw away.
    fragment_index, changeset = _write_file_fragments(
        out, context.diff_range, files, root, classifier_config
    )

    # Under the total-diff fallback the whole diff degrades to a file-list + stats
    # banner: diff.patch is empty and diff.fragment.html carries the reason instead
    # of the body. Otherwise the diff is shown verbatim (strip=False preserves
    # trailing whitespace; git already newline-terminates it).
    if changeset.too_large:
        diff_text = ""
        diff_html = notice_fragment(changeset.reason or "diff too large — file list + stats only")
    else:
        diff_text = _run_git(["diff", context.diff_range], root, strip=False)
        diff_html = diff_fragment(diff_text)

    # A new generation begins a new session: clear the prior transcript so a stale or
    # different-branch regeneration never bakes an earlier branch's Q&A into the cockpit.
    # Deliberately after EVERY git read of the run (base/diff/log above, the per-file
    # fragment diffs, and the whole-diff body): if any of them fails — a mistyped base,
    # a vanished ref — the existing review's transcript survives so /review-close can
    # still bake the discussion. Only the fragment writes precede this point, and they
    # touch neither the transcript nor the already-authored cockpit.
    _reset_run_scoped_artifacts(out)

    # Always UTF-8 so non-ASCII diffs/paths/messages round-trip deterministically
    # regardless of the platform default encoding, and so the HTML fragment is the
    # UTF-8 the cockpit's <meta charset="utf-8"> promises the browser.
    context_json = json.dumps(asdict(context), indent=2) + "\n"
    files_json = json.dumps(files, indent=2) + "\n"
    # The resolved policy the skill reads back: styling (for the cockpit + lint --styling),
    # the authoring lenses (focus/language_hints), and the machine settings for open/loop.
    resolved_config_json = (
        json.dumps(resolved_config_dict(resolved, base=context.base), indent=2) + "\n"
    )
    (out / "resolved-config.json").write_text(resolved_config_json, encoding="utf-8")
    (out / "context.json").write_text(context_json, encoding="utf-8")
    (out / "changed-files.json").write_text(files_json, encoding="utf-8")
    (out / "diff.patch").write_text(diff_text, encoding="utf-8")
    (out / "diff-stat.txt").write_text(diff_stat + "\n", encoding="utf-8")
    (out / "commits.txt").write_text(commits + "\n", encoding="utf-8")
    (out / "diff.fragment.html").write_text(diff_html, encoding="utf-8")
    (out / "fragments.html").write_text(fragments, encoding="utf-8")
    fragments_json = (
        json.dumps(
            {
                "schema": _FRAGMENTS_SCHEMA,
                "diff_range": context.diff_range,
                # The total-diff guard's verdict (issue #7): when ``too_large`` the
                # cockpit renders a "file list + stats only" banner — the fallback is
                # explicit here, not inferred from every file being omitted.
                "too_large": changeset.too_large,
                "too_large_reason": changeset.reason,
                "included_changed_lines": changeset.included_changed_lines,
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
    except (GitError, BaseResolutionError, FileNotFoundError, ConfigError) as exc:
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
