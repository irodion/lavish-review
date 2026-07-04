"""Goal Evidence — the stated purpose the branch serves (ADR-0010, issue #40).

The cockpit's L0 layer measures the change against the goal it was written for,
so the collector gathers that goal from the best evidence available. Sources by
precedence (ADR-0010):

1. **Explicit argument** — ``/review-branch --goal <issue-ref | file | text>``.
   Always wins and is **never guessed over**: if an explicit issue ref cannot be
   fetched (offline, no ``gh``, remote fetching disabled), the run proceeds with
   *no* goal and a warning — it never falls back to discovered evidence.
2. **Local repo evidence** — issue references discovered in the branch name and
   the branch's commit messages. No network; always attempted. A discovered
   reference is a *pointer*: when it can be resolved through the tracker the
   fetched issue text becomes the goal; when it cannot (offline,
   unauthenticated, disabled, absent), the evidence degrades to the text the
   repo itself holds — the first branch commit's message, the closest local
   statement of intent.
3. **Remote tracker evidence** — the referenced GitHub issue/PR via ``gh``,
   attempted **only** when a reference exists (so the default review stays
   network-free) and degrading silently on any failure — a goal fetch is never
   a failed review (the ADR-0006 posture).

**Goal Evidence is untrusted data.** Issue bodies, commit messages, and branch
names are attacker-writable; the resulting text crosses the Escape Boundary
(ADR-0002) before it is rendered, and the parsing here treats every string as
data: extracted references are digits by construction, ``gh`` is invoked with a
fixed argv and ``shell=False``, and nothing from the evidence is ever
shell-interpolated. The goal is also an *unverified claim about intent* — the
analysis measures the change against it; it never treats it as ground truth.

Like the Config Resolver (:mod:`branch_review.config`), the resolution itself —
:func:`resolve_goal` — is pure policy over already-gathered inputs (table-testable,
no filesystem, fetching injected as a callable); :func:`fetch_issue_via_gh` is the
thin I/O shell the collector plugs in.
"""

from __future__ import annotations

import json
import re
import subprocess  # nosec B404 — fixed gh argv, shell=False (see fetch_issue_via_gh)
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

# Where a resolved goal came from — the closed vocabulary ``context.json`` carries
# and L0 attributes (ADR-0010: provenance is always attributed).
GOAL_SOURCES = ("argument", "file", "issue", "commits")

# How long one ``gh`` call may take before the fetch silently degrades. Generous
# for a healthy connection, small enough that an offline run doesn't hang the
# collector on a DNS timeout.
_GH_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class GoalEvidence:
    """One resolved goal: its text, which kind of source produced it, and attribution.

    ``source`` is one of :data:`GOAL_SOURCES`; ``provenance`` is the human-readable
    attribution L0 shows ("issue #40, referenced by the branch name"). Both the text
    and the provenance may embed attacker-writable strings (issue bodies, branch
    names), so they cross the Escape Boundary before rendering.
    """

    text: str
    source: str
    provenance: str


@dataclass(frozen=True)
class IssueRef:
    """A reference to a tracker issue/PR: a number (with optional repo) or a URL."""

    number: int
    repo: str | None = None  # ``owner/name`` for a cross-repo ref; None = this repo
    url: str | None = None  # set when the ref was given as a full GitHub URL
    is_pr: bool = False  # a /pull/ URL — fetched via ``gh pr view``

    def display(self) -> str:
        """The short human form used in provenance ("#40", "owner/repo#40", the URL)."""
        if self.url is not None:
            return self.url
        if self.repo is not None:
            return f"{self.repo}#{self.number}"
        return f"#{self.number}"


# Explicit-argument ref shapes. The repo part must start alphanumeric so a hostile
# value can never reach ``gh`` looking like a flag (``--repo -evil`` is refused by
# the pattern, not by runtime luck).
_ARG_REF = re.compile(r"^(?:([A-Za-z0-9][\w.-]*/[\w.-]+))?#(\d+)$")
_URL_REF = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+/(issues|pull)/(\d+)(?:[/?#].*)?$")

# Discovered refs. In commit messages the GitHub convention is ``#N``; in a branch
# name the convention is a delimited number segment (``feat/40-goal-evidence``,
# ``40-fix``) — a digit run glued to letters (``v2``) does not match.
_MESSAGE_REF = re.compile(r"#(\d+)\b")
_BRANCH_REF = re.compile(r"(?:^|[/_-])(\d{1,6})(?:[-_]|$)")


def parse_goal_argument_ref(value: str) -> IssueRef | None:
    """Parse an explicit ``--goal`` value as an issue ref, or ``None`` if it isn't one.

    Recognized: ``#40``, ``owner/repo#40``, and full GitHub issue/PR URLs. Anything
    else is a file path or literal text — the caller's next guesses.
    """
    match = _ARG_REF.match(value.strip())
    if match:
        return IssueRef(number=int(match.group(2)), repo=match.group(1))
    url_match = _URL_REF.match(value.strip())
    if url_match:
        return IssueRef(
            number=int(url_match.group(2)),
            url=value.strip(),
            is_pr=url_match.group(1) == "pull",
        )
    return None


def discover_issue_refs(branch: str, messages: Sequence[str]) -> list[IssueRef]:
    """Issue refs named by local repo evidence, strongest first, deduplicated.

    The branch name leads (the branch-per-issue convention makes it the deliberate
    signal), then ``#N`` references from commit messages in commit order. All
    discovered refs are same-repo by construction — discovery never invents a
    cross-repo or URL target, so the later fetch can only ask ``gh`` about the
    repo under review.
    """
    seen: set[int] = set()
    refs: list[IssueRef] = []
    for source in (_BRANCH_REF.findall(branch), *(_MESSAGE_REF.findall(m) for m in messages)):
        for num_text in source:
            number = int(num_text)
            if number not in seen:
                seen.add(number)
                refs.append(IssueRef(number=number))
    return refs


def _from_commits(commits: Sequence[tuple[str, str]]) -> GoalEvidence | None:
    """The local-text fallback: the first (oldest) branch commit's message.

    The commit that opened the branch is the repo's own closest statement of why
    the branch exists. Honest but weak evidence — the provenance says exactly
    what it is so the reviewer can weigh it.
    """
    for sha, message in commits:
        if message.strip():
            return GoalEvidence(
                text=message.strip(),
                source="commits",
                provenance=f"first branch commit {sha} (its message; no tracker evidence)",
            )
    return None


def resolve_goal(
    *,
    argument: str | None,
    branch: str,
    commits: Sequence[tuple[str, str]],
    remote_enabled: bool,
    fetch_issue: Callable[[IssueRef], str | None],
    read_file: Callable[[str], str | None],
) -> tuple[GoalEvidence | None, list[str]]:
    """Resolve the Goal Evidence for one run; returns ``(goal_or_none, warnings)``.

    Pure policy: ``commits`` is the branch's ``(short_sha, full_message)`` list
    oldest-first, ``fetch_issue`` / ``read_file`` are injected shells (each returns
    text or ``None`` on any failure — they never raise). Warnings are for the
    explicit-argument path only: an explicit ref that cannot be honored is said
    out loud, never silently substituted (ADR-0010: never guessed over). The
    discovery path degrades silently by design.
    """
    if argument is not None and argument.strip():
        return _resolve_argument(argument.strip(), remote_enabled, fetch_issue, read_file)

    branch_refs = discover_issue_refs(branch, [])
    refs = discover_issue_refs(branch, [message for _sha, message in commits])
    if remote_enabled:
        for ref in refs:
            text = fetch_issue(ref)
            if text is not None and text.strip():
                where = "the branch name" if ref in branch_refs else "a commit message"
                return (
                    GoalEvidence(
                        text=text.strip(),
                        source="issue",
                        provenance=f"issue {ref.display()}, referenced by {where}",
                    ),
                    [],
                )
    return _from_commits(commits), []


def _resolve_argument(
    argument: str,
    remote_enabled: bool,
    fetch_issue: Callable[[IssueRef], str | None],
    read_file: Callable[[str], str | None],
) -> tuple[GoalEvidence | None, list[str]]:
    """The explicit ``--goal`` path: issue ref, then file, then literal text."""
    ref = parse_goal_argument_ref(argument)
    if ref is not None:
        if not remote_enabled:
            return None, [
                f"--goal names {ref.display()} but remote goal fetching is disabled by "
                "config (goal_remote_fetch: false); proceeding with no stated goal."
            ]
        text = fetch_issue(ref)
        if text is None or not text.strip():
            return None, [
                f"--goal names {ref.display()} but it could not be fetched (offline, "
                "unauthenticated, or no gh); proceeding with no stated goal."
            ]
        kind = "pull request" if ref.is_pr else "issue"
        return (
            GoalEvidence(
                text=text.strip(),
                source="issue",
                provenance=f"{kind} {ref.display()} (named by --goal, fetched via gh)",
            ),
            [],
        )

    file_text = read_file(argument)
    if file_text is not None:
        if not file_text.strip():
            return None, [f"--goal file {argument!r} is empty; proceeding with no stated goal."]
        return (
            GoalEvidence(
                text=file_text.strip(),
                source="file",
                provenance=f"file {argument} (named by --goal)",
            ),
            [],
        )

    return (
        GoalEvidence(
            text=argument,
            source="argument",
            provenance="provided literally by --goal",
        ),
        [],
    )


# --- I/O shell ----------------------------------------------------------------


def read_goal_file(value: str) -> str | None:
    """Read an explicit ``--goal`` file argument; ``None`` when it isn't a readable file.

    ``None`` is how :func:`resolve_goal` distinguishes "not a file — treat the
    argument as literal text" from a file's contents; an unreadable-but-existing
    file degrades the same way (the literal-text fallback then carries the path
    string, which the warning-free explicit path accepts as the user's own words).
    """
    path = Path(value).expanduser()
    try:
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def fetch_issue_via_gh(ref: IssueRef, cwd: Path | str | None = None) -> str | None:
    """Fetch an issue/PR's title + body through ``gh``; ``None`` on any failure.

    Fixed argv, ``shell=False``, values validated by the ref patterns above — the
    reference components are digits / a pattern-checked repo / an https URL, so
    nothing here can be shell- or flag-injected (ADR-0010's argv-never-shell rule).
    Every failure mode — no ``gh`` on PATH, unauthenticated, offline, a timeout,
    an unknown issue, malformed output — degrades to ``None``: a goal fetch never
    fails the review (ADR-0006 posture).
    """
    command = "pr" if ref.is_pr else "issue"
    target = ref.url if ref.url is not None else str(ref.number)
    argv = ["gh", command, "view", target, "--json", "title,body"]
    if ref.repo is not None:
        argv += ["--repo", ref.repo]
    try:
        proc = subprocess.run(  # nosec B603 B607
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=_GH_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    title = payload.get("title")
    body = payload.get("body")
    if not isinstance(title, str) or not isinstance(body, str):
        return None
    return f"{title}\n\n{body}".strip() or None
