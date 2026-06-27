"""The Session Evaluator — resume an unfinished Review safely, or regenerate (issue #8).

A reviewer can step away mid-review and come back. To make that safe the skill
persists one ``session.json`` per Review — ``{status, base, branch, head_sha,
started_at}`` — when it opens a cockpit, and consults it the next time
``/review-branch`` runs. This module is the **deep module of pure policy** at the
centre of that lifecycle: given the persisted :class:`Session` and the *current* git
HEAD and branch, :func:`evaluate` decides how the new run relates to the old one.

Four dispositions (the stable vocabulary the issue and SKILL speak):

- ``none``              — nothing to resume: no session, or a finished one. Generate.
- ``fresh``             — an unfinished review for **this** branch at **this** HEAD;
  re-attach without regenerating. Restore is the default.
- ``stale``             — an unfinished review for this branch whose HEAD has since
  advanced. **Regenerate by default** (resume-anyway stays available) — the cockpit
  on disk no longer describes what the branch now is.
- ``different-branch``  — the persisted review belongs to a different branch than the
  one checked out now; it cannot be restored onto this branch. Generate.

Like the Change Classifier (:mod:`branch_review.classify`), :func:`evaluate` makes
**no git calls and reads no files** — the collector / CLI gather the current HEAD and
branch and the persisted session, then feed them in. That keeps the decision a pure,
exhaustively table-testable function: same inputs → same disposition, no environment.
The thin I/O around it — :func:`load_session`, :func:`save_session`,
:func:`session_from_context`, :func:`end_session` — only reads and writes JSON files.

See ``DESIGN.md`` ("Resume + staleness") and ``CONTEXT.md``.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, replace
from enum import Enum
from pathlib import Path

# The persisted session lives beside the cockpit, under the gitignored state dir.
SESSION_NAME = "session.json"

_SCHEMA = "review-session/0.1"


class SessionError(RuntimeError):
    """``session.json`` exists but is malformed (bad JSON or missing/invalid fields).

    A *missing* file is not an error — it simply means "no session" (:func:`load_session`
    returns ``None``). This is raised only when a file is present but cannot be trusted,
    so a caller can choose to treat a corrupt session as "regenerate" rather than crash.
    """


class SessionStatus(Enum):
    """Where a persisted Review is in its lifecycle.

    ``open`` is an **unfinished** review — the one ``/review-branch`` offers to
    restore. ``ended`` is a review the reviewer closed (``/review-close``); it is kept
    on disk for its transcript but is never offered for resume. The string values are
    the stable vocabulary written to ``session.json``; compare by identity, render by
    value.
    """

    OPEN = "open"
    ENDED = "ended"

    @property
    def is_resumable(self) -> bool:
        """True only for an unfinished (``open``) review — what resume reattaches to."""
        return self is SessionStatus.OPEN


class SessionDisposition(Enum):
    """The Session Evaluator's verdict on how a new run relates to the saved one.

    The string values are the vocabulary the issue and SKILL speak (``none``,
    ``fresh``, ``stale``, ``different-branch``). The two properties encode the
    **policy** the cockpit acts on, so it lives here (and is table-tested) rather than
    being re-derived by every caller.
    """

    NONE = "none"
    FRESH = "fresh"
    STALE = "stale"
    DIFFERENT_BRANCH = "different-branch"

    @property
    def offers_restore(self) -> bool:
        """True when a resumable review for *this* branch exists, so restore is offered.

        Both ``fresh`` and ``stale`` have a matching unfinished review to re-attach to;
        ``none`` (nothing to resume) and ``different-branch`` (the review is for another
        branch) do not, so the run just generates.
        """
        return self in (SessionDisposition.FRESH, SessionDisposition.STALE)

    @property
    def restore_is_default(self) -> bool:
        """True only for ``fresh`` — the one case where re-attaching is the safe default.

        On ``stale`` restore is *offered but not default*: the branch advanced since the
        cockpit was generated, so **regenerate is the default** (resume-anyway remains a
        choice). This is the issue's "regenerate-by-default on stale" invariant, encoded
        once. The negation — ``offers_restore and not restore_is_default`` — is exactly
        "regenerate by default, resume available".
        """
        return self is SessionDisposition.FRESH


@dataclass(frozen=True)
class Session:
    """One persisted Review's lifecycle state (the contents of ``session.json``).

    ``base``/``branch``/``head_sha`` mirror the :class:`~branch_review.collect.ReviewContext`
    the cockpit was generated from; ``started_at`` is the ISO-8601 timestamp the review
    began; ``status`` tracks open → ended. ``schema`` versions the on-disk shape so a
    future field change is detectable rather than silently misread.
    """

    schema: str
    status: SessionStatus
    base: str
    branch: str
    head_sha: str
    started_at: str

    def to_json(self) -> str:
        """Serialise to the pretty JSON written to ``session.json`` (status as its value)."""
        data = asdict(self)
        data["status"] = self.status.value
        return json.dumps(data, indent=2) + "\n"

    @classmethod
    def from_mapping(cls, data: object) -> Session:
        """Build a :class:`Session` from parsed JSON, validating shape and status.

        Raises :class:`SessionError` (never ``KeyError``/``ValueError``) on anything
        unexpected, so callers have a single, intentional failure mode for a corrupt
        file. An unknown ``schema`` is tolerated forward-compatibly only insofar as the
        required fields are present and well-typed; the ``status`` must be a known value.
        """
        if not isinstance(data, dict):
            raise SessionError(f"session.json must be a JSON object, got {type(data).__name__}")
        required = ("schema", "status", "base", "branch", "head_sha", "started_at")
        missing = [key for key in required if key not in data]
        if missing:
            raise SessionError(f"session.json missing field(s): {', '.join(missing)}")
        for key in required:
            if not isinstance(data[key], str):
                raise SessionError(f"session.json field {key!r} must be a string")
        try:
            status = SessionStatus(data["status"])
        except ValueError as exc:
            raise SessionError(f"session.json has unknown status {data['status']!r}") from exc
        return cls(
            schema=data["schema"],
            status=status,
            base=data["base"],
            branch=data["branch"],
            head_sha=data["head_sha"],
            started_at=data["started_at"],
        )


def evaluate(
    session: Session | None,
    current_head: str,
    current_branch: str,
) -> SessionDisposition:
    """Decide how a new ``/review-branch`` run relates to the persisted ``session``.

    Precedence, most disqualifying first — each rule assumes the ones above it did not
    fire:

    1. **none** — there is no session, or the saved one is already finished
       (``status`` not resumable). Nothing unfinished to restore; generate.
    2. **different-branch** — the saved review is for another branch than the one
       checked out now. Checked *before* the HEAD comparison: a different branch almost
       always has a different HEAD too, and reporting that as "stale" would wrongly
       imply the *same* review merely advanced.
    3. **stale** — same branch, but HEAD has moved since the cockpit was generated. The
       artifact no longer matches the branch, so **regenerate by default**.
    4. **fresh** — same branch, same HEAD: the cockpit still describes reality, so
       re-attach without regenerating.

    Pure: it inspects only its arguments. ``current_head``/``current_branch`` come from
    the live working tree (``git rev-parse HEAD`` / ``--abbrev-ref HEAD``); the
    collector reports a detached HEAD as its short SHA, so a detached review compares
    consistently here without a special case.
    """
    if session is None or not session.status.is_resumable:
        return SessionDisposition.NONE
    if session.branch != current_branch:
        return SessionDisposition.DIFFERENT_BRANCH
    if session.head_sha != current_head:
        return SessionDisposition.STALE
    return SessionDisposition.FRESH


def end_session(session: Session) -> Session:
    """Return ``session`` transitioned to ``ended`` — the ``/review-close`` step.

    Idempotent: ending an already-ended session yields an equal session. Marking ended
    (rather than deleting the file) keeps the transcript and lets a later run see "a
    finished review for this branch existed" as ``none`` rather than offer a stale one.
    """
    if session.status is SessionStatus.ENDED:
        return session
    return replace(session, status=SessionStatus.ENDED)


def _session_path(target: Path) -> Path:
    """Resolve ``target`` to the ``session.json`` file (accept either the file or its dir)."""
    return target if target.suffix == ".json" else target / SESSION_NAME


def load_session(target: Path) -> Session | None:
    """Load the persisted session from ``target`` (the file or its ``.review-agent/`` dir).

    Returns ``None`` when the file is absent — the ordinary "no prior review" case, not
    an error. Raises :class:`SessionError` when the file is present but unparseable or
    structurally invalid, so a corrupt session is a deliberate, catchable condition
    rather than a crash deep in JSON parsing.
    """
    path = _session_path(target)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SessionError(f"cannot read {path}: {exc}") from exc
    return Session.from_mapping(raw)


def save_session(target: Path, session: Session) -> Path:
    """Write ``session`` to ``target`` (the file or its dir) and return the file path.

    The parent dir is created if needed (the collector normally made it already). UTF-8
    to match every other artifact, so a non-ASCII branch name round-trips.
    """
    path = _session_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(session.to_json(), encoding="utf-8")
    return path


def session_from_context(context_path: Path) -> Session:
    """Build the ``open`` :class:`Session` from a collected ``context.json``.

    The collector already recorded ``base``/``branch``/``head_sha``/``generated_at`` for
    the exact revision the cockpit was authored from, so the session mirrors that rather
    than re-running git — guaranteeing the saved session and the cockpit describe the
    same HEAD. ``started_at`` is the context's ``generated_at`` (the review began when
    the diff was collected). Raises :class:`SessionError` if the context is missing a
    needed field.
    """
    try:
        data = json.loads(context_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SessionError(f"cannot read {context_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SessionError(f"{context_path} must be a JSON object")
    try:
        return Session(
            schema=_SCHEMA,
            status=SessionStatus.OPEN,
            base=data["base"],
            branch=data["branch"],
            head_sha=data["head_sha"],
            started_at=data["generated_at"],
        )
    except KeyError as exc:
        raise SessionError(f"{context_path} missing field {exc.args[0]!r}") from exc


# --- CLI --------------------------------------------------------------------
#
# Three subcommands wire the lifecycle into the skill (SKILL.md): ``evaluate`` runs
# first on ``/review-branch`` to decide restore-vs-regenerate; ``start`` records the
# open session once a cockpit is generated; ``end`` marks it ended on ``/review-close``.
# Git lives only here, in the CLI — never in the pure evaluator above.


def _resolve_out(repo: Path, out: Path | None) -> Path:
    """The state dir holding ``session.json`` — explicit ``--out`` or ``<repo>/.review-agent``."""
    from branch_review.collect import repo_root

    return out if out is not None else repo_root(repo) / ".review-agent"


def _cmd_evaluate(repo: Path, out: Path | None) -> int:
    """Print the Session Evaluator's verdict for the current working tree as JSON.

    The agent reads this at step 0 of ``/review-branch`` and branches on
    ``disposition``. A corrupt ``session.json`` is reported as ``none`` with a ``note``
    (not a crash), so a broken session never blocks a review — it just regenerates.
    """
    from branch_review.collect import current_revision, repo_root

    root = repo_root(repo)
    out_dir = out if out is not None else root / ".review-agent"
    head_sha, branch = current_revision(root)

    note: str | None = None
    try:
        session = load_session(out_dir)
    except SessionError as exc:
        session, note = None, f"ignoring unreadable session.json ({exc}) — will regenerate"

    disposition = evaluate(session, head_sha, branch)
    payload: dict[str, object] = {
        "disposition": disposition.value,
        "offers_restore": disposition.offers_restore,
        "restore_is_default": disposition.restore_is_default,
        "current": {"head_sha": head_sha, "branch": branch},
        "session": None if session is None else json.loads(session.to_json()),
    }
    if note is not None:
        payload["note"] = note
    print(json.dumps(payload, indent=2))
    return 0


def _cmd_start(repo: Path, out: Path | None) -> int:
    """Record the open session from the freshly collected ``context.json``."""
    out_dir = _resolve_out(repo, out)
    session = session_from_context(out_dir / "context.json")
    path = save_session(out_dir, session)
    print(f"Session recorded ({session.status.value}) at {path}")
    print(f"  base={session.base} branch={session.branch} head={session.head_sha[:12]}")
    return 0


def _cmd_end(repo: Path, out: Path | None) -> int:
    """Mark the persisted session ended (``/review-close``); a no-op if none exists."""
    out_dir = _resolve_out(repo, out)
    session = load_session(out_dir)
    if session is None:
        print("No session.json to end.")
        return 0
    ended = end_session(session)
    save_session(out_dir, ended)
    print(f"Session ended for branch={ended.branch}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the skill's ``session.py`` (evaluate / start / end)."""
    parser = argparse.ArgumentParser(
        prog="session",
        description="Persist and evaluate the Review session (resume & staleness, issue #8).",
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repo path (default: cwd).")
    parser.add_argument(
        "--out", type=Path, default=None, help="State dir (default: <repo>/.review-agent)."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("evaluate", help="Decide restore vs regenerate for the current branch.")
    sub.add_parser("start", help="Record the open session from context.json.")
    sub.add_parser("end", help="Mark the session ended (/review-close).")
    args = parser.parse_args(argv)

    # The git layer (:mod:`branch_review.collect`) is imported inside the handlers so
    # the pure evaluator above carries no git dependency; its failure modes are caught
    # here so a bad repo or a missing artifact surfaces as a clean error, not a traceback.
    from branch_review.collect import BaseResolutionError, GitError

    try:
        if args.command == "evaluate":
            return _cmd_evaluate(args.repo, args.out)
        if args.command == "start":
            return _cmd_start(args.repo, args.out)
        return _cmd_end(args.repo, args.out)
    except (SessionError, GitError, BaseResolutionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
