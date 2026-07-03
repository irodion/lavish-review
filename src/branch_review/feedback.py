"""The interactive feedback loop — the skill's blocking answer loop (ADR-0003, issue #5).

After the cockpit opens, ``/review-branch`` enters a blocking answer loop driven by
``lavish-axi poll``: the reviewer types a question or annotates an element/line in
the browser, the agent reads the poll output directly (TOON — **no parser**),
answers grounded in the diff and repo, and re-polls with ``--agent-reply`` to show
that answer in the browser chat and wait for the next prompt. See the spike
``docs/spikes/lavish-poll-format.md`` for the verified I/O contract.

This module is the **hardening layer** around that loop. Browser feedback is
*untrusted data*: it is logged and shown, never executed, and never used to build a
shell command. Two properties make that mechanical rather than a matter of agent
discretion:

1. The reviewer-facing answer is delivered to ``lavish-axi`` as an ``argv`` element
   with ``shell=False`` — read from a file (:data:`AGENT_REPLY_NAME`), never
   interpolated into a shell string — so no echoed or annotated content (and no
   special character in the agent's own prose) can ever break out into the command
   line.
2. The Q&A log (:data:`QA_NAME`) is appended here, as well-formed JSON Lines, so the
   agent never hand-builds a log entry around untrusted text.

The agent still reads the poll TOON directly: this wrapper passes ``lavish-axi``'s
stdout through verbatim and **never parses TOON**. For the log it captures the prior
poll's raw output as-is (:data:`LAST_POLL_NAME`), pairing it with the answer.
"""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 — fixed npx/lavish-axi argv, shell=False (see _default_runner)
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# The pinned Lavish-AXI release the whole skill drives by default (matches SKILL.md).
# Centralised here so the loop and the open step never drift. A per-machine
# ``lavish_version`` (issue #10) overrides it per run: :func:`resolve_lavish_pkg` reads
# the collector's ``resolved-config.json`` beside the cockpit and pins that instead.
LAVISH_PKG = "lavish-axi@0.1.31"

# The resolved policy the collector writes beside the cockpit (collect.py); the loop
# reads only its ``lavish_version`` key.
RESOLVED_CONFIG_NAME = "resolved-config.json"

# The canonical cockpit path. Lavish keys a session by this file path, so resume is
# just re-polling the same path — no regeneration (ADR-0003).
DEFAULT_COCKPIT = Path(".review-agent/review.html")

# Files the loop reads and writes, all under the cockpit's own (gitignored) dir.
AGENT_REPLY_NAME = "agent-reply.txt"  # the agent writes its answer here; we read it
QA_NAME = "qa.jsonl"  # the live Q&A transcript, one JSON object per line
LAST_POLL_NAME = "last-poll.toon"  # raw stdout of the most recent poll (the question)

# The run-scoped feedback-loop artifacts: the live transcript, the most recent
# question, and the pending answer. They belong to *one* review session, so a fresh
# generation must clear them — otherwise a prior branch's Q&A would be folded into the
# new cockpit at close (bake reads the default ``qa.jsonl``). The Diff Collector resets
# these when it (re)generates a context; a no-regeneration resume keeps them. Kept here,
# beside the names, so there is one owner of "what a session's transcript comprises".
RUN_SCOPED_ARTIFACTS = (QA_NAME, LAST_POLL_NAME, AGENT_REPLY_NAME)

# Exit codes that mean the answer reached the browser even though the poll did not
# return normally: ``--agent-reply`` POSTs the answer *before* the long-poll begins,
# so a SIGINT (130) / SIGTERM (143) during the wait still delivered it.
_SIGNAL_EXITS = (130, 143)


@dataclass(frozen=True)
class LavishResult:
    """Outcome of one ``lavish-axi`` invocation.

    ``stdout`` is the captured TOON (empty when interrupted before it arrived);
    ``interrupted`` records that the wait was cut short by Ctrl-C / SIGINT — the
    mechanism behind ``Esc``, which Lavish guarantees never loses queued feedback.
    """

    returncode: int
    stdout: str
    interrupted: bool = False


# A runner takes the full argv and returns the result. Injectable so tests drive the
# loop without spawning a real CLI; production uses :func:`_default_runner`.
Runner = Callable[[list[str]], LavishResult]


def _default_runner(argv: list[str]) -> LavishResult:
    """Run ``lavish-axi`` with a fixed argv and ``shell=False`` — the hardening seam.

    stdout is captured (the TOON the agent reads); stderr is inherited so the
    reviewer sees Lavish's live wait banner/heartbeats. ``Esc`` sends SIGINT to the
    shared process group: the child prints its interrupt banner and exits 130 while
    Python raises :class:`KeyboardInterrupt` here — either way we report it
    interrupted and never lose queued feedback.
    """
    try:
        proc = subprocess.run(  # nosec B603 B607 — fixed argv (npx + pinned pkg), shell=False
            argv,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            check=False,
        )
    except KeyboardInterrupt:
        return LavishResult(returncode=130, stdout="", interrupted=True)
    interrupted = proc.returncode in _SIGNAL_EXITS or proc.returncode < 0
    return LavishResult(
        returncode=proc.returncode, stdout=proc.stdout or "", interrupted=interrupted
    )


def resolve_lavish_pkg(cockpit: Path) -> str:
    """The ``lavish-axi`` package spec for this run's cockpit.

    Honors the per-machine ``lavish_version`` (issue #10): the Config Resolver
    validated it and the collector wrote it to ``resolved-config.json`` beside the
    cockpit; a set version pins ``lavish-axi@<version>`` for open/poll/reply/end.
    Absent file, absent/null key, or an unreadable JSON fall back to the bundled
    default — a degraded config must never block the answer loop (an unreadable
    file is warned about on stderr, since the collector just wrote it).
    """
    config_path = cockpit.parent / RESOLVED_CONFIG_NAME
    if not config_path.is_file():
        return LAVISH_PKG
    try:
        resolved = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(
            f"warning: cannot read {config_path} ({exc}); using {LAVISH_PKG}",
            file=sys.stderr,
        )
        return LAVISH_PKG
    version = resolved.get("lavish_version") if isinstance(resolved, dict) else None
    if isinstance(version, str) and version.strip():
        return f"lavish-axi@{version.strip()}"
    return LAVISH_PKG


def _lavish_argv(pkg: str, *args: str) -> list[str]:
    """The argv for one pinned ``lavish-axi`` subcommand, via ``npx -y``."""
    return ["npx", "-y", pkg, *args]


def _was_delivered(result: LavishResult) -> bool:
    """True if the agent-reply reached the browser despite an abnormal exit.

    The POST precedes the long-poll, so a clean exit *or* an interrupt both mean the
    answer was shown; only a fast non-signal error (e.g. the server was unreachable)
    means it was not — and then we must not log a phantom exchange. A signal exit is
    already folded into ``interrupted`` by :func:`_default_runner`.
    """
    return result.returncode == 0 or result.interrupted


def _count_lines(path: Path) -> int:
    """Number of existing records in a JSONL file (0 if absent)."""
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def append_exchange(
    qa_path: Path,
    *,
    feedback_raw: str,
    answer: str,
    now: datetime,
) -> None:
    """Append one Q&A exchange to ``qa.jsonl`` as a single JSON Lines record.

    ``feedback_raw`` is the prior poll's raw TOON (the untrusted question) captured
    verbatim — stored as a JSON string, so it is data, never code. ``ensure_ascii``
    is off so non-ASCII feedback round-trips, matching the UTF-8 the rest of the
    skill writes.
    """
    record = {
        "seq": _count_lines(qa_path) + 1,
        "ts": now.isoformat(),
        "feedback_raw": feedback_raw,
        "answer": answer,
    }
    line = json.dumps(record, ensure_ascii=False)
    with qa_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _emit(stdout: str, *, echo: bool) -> None:
    """Pass the poll's TOON through to our stdout, verbatim, so the agent reads it.

    ``lavish-axi`` already emits newline-terminated TOON, so we add nothing.
    """
    if echo and stdout:
        print(stdout, end="")


def poll(
    cockpit: Path,
    *,
    runner: Runner = _default_runner,
    echo: bool = True,
    pkg: str | None = None,
) -> int:
    """Block on ``lavish-axi poll`` once and surface the result to the agent.

    Used to enter the loop, to re-poll after a ``waiting`` timeout, and to re-attach
    on ``/review-resume``. On a clean return the raw TOON is both echoed (the agent's
    own input) and saved to ``last-poll.toon`` so the next ``reply`` can log the
    question it answers. Nothing is logged here — there is no answer yet.
    """
    out_dir = cockpit.parent
    result = runner(_lavish_argv(pkg or resolve_lavish_pkg(cockpit), "poll", str(cockpit)))
    if result.returncode == 0:
        (out_dir / LAST_POLL_NAME).write_text(result.stdout, encoding="utf-8")
        _emit(result.stdout, echo=echo)
    return result.returncode


def reply(
    cockpit: Path,
    *,
    answer_file: Path | None = None,
    runner: Runner = _default_runner,
    echo: bool = True,
    now: datetime | None = None,
    pkg: str | None = None,
) -> int:
    """Show the agent's answer in the browser, log the exchange, and re-block.

    Reads the answer from ``answer_file`` (default ``agent-reply.txt``) and hands it
    to ``lavish-axi`` as an ``argv`` element — never a shell string — so echoed or
    annotated feedback can never construct a command. ``--agent-reply`` POSTs the
    answer *and* resumes the long-poll in one call, so the returned TOON is the
    *next* question. The exchange (prior question + this answer) is appended to
    ``qa.jsonl`` once the answer is known to have been delivered.
    """
    out_dir = cockpit.parent
    answer_path = answer_file or (out_dir / AGENT_REPLY_NAME)
    try:
        answer = answer_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot read answer file {answer_path}: {exc}", file=sys.stderr)
        return 2
    if not answer.strip():
        print(f"error: answer file {answer_path} is empty", file=sys.stderr)
        return 2

    last_poll_path = out_dir / LAST_POLL_NAME
    # The question being answered: the previous poll's raw output, captured before we
    # overwrite it with the next one below.
    feedback_raw = last_poll_path.read_text(encoding="utf-8") if last_poll_path.is_file() else ""

    result = runner(
        _lavish_argv(
            pkg or resolve_lavish_pkg(cockpit), "poll", str(cockpit), "--agent-reply", answer
        )
    )

    if _was_delivered(result):
        append_exchange(
            out_dir / QA_NAME,
            feedback_raw=feedback_raw,
            answer=answer,
            now=now or datetime.now(UTC),
        )
    if result.returncode == 0:
        last_poll_path.write_text(result.stdout, encoding="utf-8")
        _emit(result.stdout, echo=echo)
    return result.returncode


def end(cockpit: Path, *, runner: Runner = _default_runner, pkg: str | None = None) -> int:
    """End the Lavish session cleanly — the ``/review-close`` control."""
    return runner(_lavish_argv(pkg or resolve_lavish_pkg(cockpit), "end", str(cockpit))).returncode


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the skill's ``review_loop.py``."""
    parser = argparse.ArgumentParser(
        prog="review_loop",
        description="Drive the blocking Lavish answer loop (poll / reply / end).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    poll_p = sub.add_parser("poll", help="Block on poll; surface queued feedback.")
    poll_p.add_argument("file", nargs="?", type=Path, default=DEFAULT_COCKPIT)

    reply_p = sub.add_parser("reply", help="Show the answer, log the exchange, re-block.")
    reply_p.add_argument("file", nargs="?", type=Path, default=DEFAULT_COCKPIT)
    reply_p.add_argument(
        "--answer-file",
        type=Path,
        default=None,
        help=f"Answer to display (default: <cockpit dir>/{AGENT_REPLY_NAME}).",
    )

    end_p = sub.add_parser("end", help="End the Lavish session (/review-close).")
    end_p.add_argument("file", nargs="?", type=Path, default=DEFAULT_COCKPIT)

    # ``required=True`` on the subparsers guarantees one of these three commands.
    args = parser.parse_args(argv)
    if args.command == "poll":
        return poll(args.file)
    if args.command == "reply":
        return reply(args.file, answer_file=args.answer_file)
    return end(args.file)


if __name__ == "__main__":
    raise SystemExit(main())
