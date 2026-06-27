"""The Change Classifier — noise control without ever silently hiding a change.

A deep module of pure policy: given a changed file's path and stats, decide its
:class:`Disposition` — whether its diff *body* belongs in the cockpit, and if not,
*why*. The critical invariant (DESIGN, CONTEXT "Suspicious Omission"): only bodies
are ever dropped. Existence and stats are never dropped — every classified-out file
still appears in the cockpit's file list with its status and a human reason, so a
reviewer can always see *that* something changed even when the *what* is omitted.

Four dispositions:

- ``include-body``  — a normal file; its escaped diff fragment is shown.
- ``omit:lockfile`` — a dependency lockfile (``package-lock.json``, ``uv.lock`` …);
  high-churn, low-signal, and its own disposition because the reason is specific.
- ``omit:excluded`` — vendored / generated / build output, or a path the repo or
  ``.gitattributes linguist-generated`` marks as not-for-review.
- ``omit:too-large`` — the file's diff exceeds the per-file line cap, **or** the
  whole changeset exceeds the total cap and falls back to file-list + stats only.

The module makes **no git calls and reads no files** — all I/O lives in the
collector, which feeds in per-file :class:`FileStats` (numstat + the
``linguist-generated`` attribute). That keeps classification a pure, exhaustively
table-testable function: same inputs → same disposition, no environment.

See ``DESIGN.md`` ("Diff collection" → Default excludes / per-file cap / total-diff
guard) and ``CONTEXT.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from fnmatch import fnmatch
from pathlib import PurePosixPath


class Disposition(Enum):
    """What the cockpit does with a changed file's diff body.

    The string values are the stable vocabulary the issue and cockpit speak
    (``include-body``, ``omit:lockfile`` …); compare by identity, render by value.
    """

    INCLUDE_BODY = "include-body"
    OMIT_LOCKFILE = "omit:lockfile"
    OMIT_EXCLUDED = "omit:excluded"
    OMIT_TOO_LARGE = "omit:too-large"

    @property
    def omits_body(self) -> bool:
        """True when this disposition drops the body (everything but include-body)."""
        return self is not Disposition.INCLUDE_BODY


@dataclass(frozen=True)
class FileStats:
    """The per-file facts the classifier needs, gathered deterministically upstream.

    ``added``/``deleted`` are ``git diff --numstat`` line counts; ``changed_lines``
    (their sum) is what the per-file and total caps measure. ``binary`` files have
    no line stats (numstat reports ``-``) and so can never trip a line cap — git
    renders only a one-line "Binary files differ" body. ``linguist_generated``
    mirrors the ``.gitattributes linguist-generated`` attribute the collector reads
    via ``git check-attr``; the classifier honors it but never parses attributes
    itself.
    """

    added: int = 0
    deleted: int = 0
    binary: bool = False
    linguist_generated: bool = False

    @property
    def changed_lines(self) -> int:
        """Total changed lines (added + deleted) — what the line caps measure."""
        return self.added + self.deleted


# --- Built-in policy --------------------------------------------------------
#
# Default excludes per DESIGN. These are intentionally conservative and well-known;
# repo policy (issue #10) layers *additional* excludes on top via
# ``ClassifierConfig.extra_excludes`` and can drop these built-ins with
# ``exclude_reset``. Lockfiles and ``linguist-generated`` stand apart from the
# resettable globs — see ``classify`` for why.

# Dependency lockfiles, matched by exact basename. High-churn, machine-generated,
# almost never read line-by-line in review — but always *listed*, since a surprise
# lockfile change can be a real supply-chain signal.
DEFAULT_LOCKFILES: frozenset[str] = frozenset(
    {
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lockb",
        "Cargo.lock",
        "poetry.lock",
        "Pipfile.lock",
        "uv.lock",
        "pdm.lock",
        "composer.lock",
        "Gemfile.lock",
        "go.sum",
        "flake.lock",
        "mix.lock",
        "gradle.lockfile",
        "packages.lock.json",
    }
)

# Directory names that, appearing anywhere in a path, mark vendored / build trees.
DEFAULT_EXCLUDE_DIRS: frozenset[str] = frozenset(
    {"node_modules", "vendor", "third_party", "dist", "build"}
)

# Basename globs for generated / minified files.
DEFAULT_EXCLUDE_GLOBS: tuple[str, ...] = (
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.generated.*",
    "*.pb.go",
    "*_pb2.py",
    "*_pb2.pyi",
)

# Sensible default caps; repo policy (issue #10) overrides via ``limits.*``.
DEFAULT_MAX_FILE_DIFF_LINES = 1500
DEFAULT_MAX_TOTAL_DIFF_LINES = 25_000


@dataclass(frozen=True)
class ClassifierConfig:
    """Tunable classification policy (repo ``.review-agent.yaml`` populates this).

    ``extra_excludes`` are fnmatch globs matched against the **full POSIX path**
    (e.g. ``docs/api/*.snap``, ``**/__generated__/*``); they *extend* the built-in
    excludes. ``exclude_reset`` drops the built-in dir/glob excludes so only
    ``extra_excludes`` apply — but lockfile detection and ``linguist-generated``
    are deliberately *not* reset: a lockfile is its own disposition and
    ``.gitattributes`` is the repo's own explicit declaration, neither of which a
    looser exclude list should silently re-enable as full bodies.
    """

    max_file_diff_lines: int = DEFAULT_MAX_FILE_DIFF_LINES
    max_total_diff_lines: int = DEFAULT_MAX_TOTAL_DIFF_LINES
    extra_excludes: tuple[str, ...] = ()
    exclude_reset: bool = False
    lockfiles: frozenset[str] = field(default=DEFAULT_LOCKFILES)
    exclude_dirs: frozenset[str] = field(default=DEFAULT_EXCLUDE_DIRS)
    exclude_globs: tuple[str, ...] = field(default=DEFAULT_EXCLUDE_GLOBS)


@dataclass(frozen=True)
class Classification:
    """A :class:`Disposition` plus the human reason shown beside an omitted file.

    ``reason`` is empty for ``include-body`` and a required, non-empty sentence for
    every omission (so the cockpit never renders an unexplained "(omitted)").
    """

    disposition: Disposition
    reason: str = ""

    @property
    def omitted(self) -> bool:
        """True when the body is dropped — i.e. the disposition omits it."""
        return self.disposition.omits_body


def _is_lockfile(name: str, config: ClassifierConfig) -> bool:
    return name in config.lockfiles


def _matched_exclude(path: str, config: ClassifierConfig) -> str | None:
    """The first matching exclude rule for ``path``, or ``None``.

    Returns a short human label of *what* matched (a directory segment, a built-in
    glob, or a configured pattern) so the omission reason can name it.
    """
    posix = PurePosixPath(path)
    name = posix.name
    parts = posix.parts

    if not config.exclude_reset:
        for segment in config.exclude_dirs:
            if segment in parts:
                return f"in {segment}/"
        for pattern in config.exclude_globs:
            if fnmatch(name, pattern):
                return pattern

    # Configured excludes match the full path so a rule can target a subtree.
    for pattern in config.extra_excludes:
        if fnmatch(path, pattern) or fnmatch(name, pattern):
            return pattern
    return None


def classify_file(path: str, stats: FileStats, config: ClassifierConfig) -> Classification:
    """Classify one changed file into a :class:`Classification` (disposition + reason).

    Precedence is by *specificity of the reason*, most specific first:

    1. **lockfile** — exact basename; its own disposition and message.
    2. **linguist-generated** — the repo's explicit ``.gitattributes`` declaration.
    3. **default / configured excludes** — vendored, generated, build, or
       repo-configured paths.
    4. **per-file too-large** — body exceeds the line cap (binary files, having no
       line count, never trip this).
    5. otherwise **include-body**.

    Excludes outrank the size cap on purpose: an excluded lockfile or vendored blob
    should read as "excluded", not "too large", even when it is also huge.
    """
    name = PurePosixPath(path).name

    if _is_lockfile(name, config):
        return Classification(
            Disposition.OMIT_LOCKFILE,
            "dependency lockfile — body omitted, stats kept",
        )

    if stats.linguist_generated:
        return Classification(
            Disposition.OMIT_EXCLUDED,
            "marked linguist-generated in .gitattributes — body omitted, stats kept",
        )

    matched = _matched_exclude(path, config)
    if matched is not None:
        return Classification(
            Disposition.OMIT_EXCLUDED,
            f"excluded ({matched}) — body omitted, stats kept",
        )

    if not stats.binary and stats.changed_lines > config.max_file_diff_lines:
        return Classification(
            Disposition.OMIT_TOO_LARGE,
            f"large change — {stats.changed_lines} changed lines exceed the "
            f"{config.max_file_diff_lines}-line per-file cap; body omitted, stats kept",
        )

    return Classification(Disposition.INCLUDE_BODY)


def classify(path: str, stats: FileStats, config: ClassifierConfig) -> Disposition:
    """The issue-#7 contract: ``classify(path, stats, config) -> Disposition``.

    A thin projection of :func:`classify_file` for callers that only need the
    disposition; use :func:`classify_file` when you also need the reason.
    """
    return classify_file(path, stats, config).disposition


@dataclass(frozen=True)
class ChangesetDisposition:
    """The whole-changeset guard's verdict (the "diff too large" fallback).

    ``too_large`` means the combined body of the would-be-included files exceeds
    :attr:`ClassifierConfig.max_total_diff_lines`; the collector then re-stamps
    every still-included file as :attr:`Disposition.OMIT_TOO_LARGE` with
    :attr:`reason`, leaving a file-list + stats banner instead of a wall of diff —
    a deliberate fallback, never a silent truncation.
    """

    too_large: bool
    included_changed_lines: int
    reason: str | None = None


def classify_changeset(
    classified: list[tuple[str, FileStats, Classification]],
    config: ClassifierConfig,
) -> ChangesetDisposition:
    """Decide whether the whole changeset trips the total-diff guard.

    Sums ``changed_lines`` over only the files that would *include their body* —
    already-omitted files cost the reader nothing, so they don't count toward the
    total. Returns a :class:`ChangesetDisposition`; the collector applies the
    fallback when ``too_large`` is set.
    """
    total = sum(
        stats.changed_lines
        for _path, stats, classification in classified
        if not classification.omitted
    )
    if total > config.max_total_diff_lines:
        return ChangesetDisposition(
            too_large=True,
            included_changed_lines=total,
            reason=(
                f"diff too large — {total} changed lines exceed the "
                f"{config.max_total_diff_lines}-line total cap; "
                "showing the file list + stats only"
            ),
        )
    return ChangesetDisposition(too_large=False, included_changed_lines=total)


def downgrade_to_listing(classification: Classification, reason: str) -> Classification:
    """Re-stamp a would-be-included file as omitted under the total-diff fallback.

    Only an ``include-body`` classification is downgraded to ``omit:too-large``;
    a file already omitted for a more specific reason (lockfile, excluded, per-file
    cap) keeps that reason — the total-diff banner never overwrites a sharper one.
    """
    if classification.omitted:
        return classification
    return replace(classification, disposition=Disposition.OMIT_TOO_LARGE, reason=reason)
