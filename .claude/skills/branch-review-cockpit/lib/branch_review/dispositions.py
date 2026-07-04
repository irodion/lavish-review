"""Reviewer Dispositions — per-claim review state, set by the human alone (ADR-0012).

Each L2 claim carries a disposition — ``unreviewed | verified | concern |
question-open`` — that records what the reviewer actually did with it. The cockpit's
in-page controls emit disposition updates through Lavish's feedback protocol (the
host-seam spike's verified channel: a ``tag: choice`` prompt whose ``Context data:``
block carries ``{kind: "disposition", claim, disposition}``, deduplicated pre-send by
``queueKey``); the answer loop lands the raw poll in ``last-poll.toon``; and this
module is the **deterministic bridge** from that untrusted text to the persisted
store — the agent never hand-parses or hand-writes a disposition (the ADR-0002
posture: reviewer input is data crossing a chokepoint, never text the agent
re-types).

The store (``dispositions.json``) is **run-scoped** like the Q&A transcript: keyed by
the analysis's stable claim ids (``t2.c3``, minted per run — ADR-0012's identity
contract), reset by the collector on regeneration, carried across ``Esc`` /
``/review-resume``. ``unreviewed`` is the default state and is stored as absence:
setting a claim back to ``unreviewed`` removes its entry.

Only the reviewer moves a disposition. The only write path is :func:`apply` /
the ``apply`` CLI, whose input is the reviewer's own queued feedback; there is
deliberately no free-form ``set`` command an agent could use to author review state,
and a ``concern`` is never softened or auto-resolved by anything in this module.

Pure policy (:func:`extract_updates`, :func:`apply_updates`, :func:`progress`) over a
thin I/O shell (:func:`apply`, :func:`main`), like the Config Resolver and the Goal
Evidence resolver.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from branch_review.feedback import LAST_POLL_NAME, Prompt, extract_prompts

# The closed disposition vocabulary (ADR-0012). ``unreviewed`` is the default and is
# represented in the store by absence.
DISPOSITIONS = ("unreviewed", "verified", "concern", "question-open")

DISPOSITIONS_NAME = "dispositions.json"
ANALYSIS_NAME = "analysis.json"

_SCHEMA = "review-dispositions/0.1"

# A claim id as ADR-0012 mints them: thread-scoped, e.g. ``t2.c3``.
_CLAIM_ID = re.compile(r"^t\d+\.c\d+$")

# The structured payload the in-page control attaches (spike #38): the SDK appends
# ``data`` to the prompt text as a ``Context data:`` block holding the JSON.
_CONTEXT_DATA = re.compile(r"Context data:\s*(\{.*\})", re.DOTALL)

# The guaranteed-floor fallback: the control's human-readable prompt line itself.
_PROMPT_LINE = re.compile(
    r"^Disposition set:\s*(t\d+\.c\d+)\s*->\s*(unreviewed|verified|concern|question-open)\b"
)


def parse_disposition_prompt(prompt: Prompt) -> tuple[str, str] | None:
    """Read one poll prompt as a disposition update, or ``None`` if it isn't one.

    Gated on the control channel first: the in-page controls send ``tag: "choice"``
    (the #38 spike's verified contract), so a free-form chat message
    (``tag: "message"``) that merely *says* "Disposition set: …" is never an
    update — it stays a question, is answered in chat, and survives in the baked
    Q&A instead of being filtered out as state. Within the channel, prefers the
    structured ``Context data:`` JSON (``{kind: "disposition", claim,
    disposition}``) and falls back to the control's own prompt line. Both paths are
    strict: the claim id must have the ``t<N>.c<N>`` shape and the disposition must
    be in the closed vocabulary — hostile text that fails either is simply **not a
    disposition**, and text that passes can only ever name an enum value, which is
    inert by construction.
    """
    if prompt.tag != "choice":
        return None
    match = _CONTEXT_DATA.search(prompt.prompt)
    if match:
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            payload = None
        if (
            isinstance(payload, Mapping)
            and payload.get("kind") == "disposition"
            and isinstance(payload.get("claim"), str)
            and _CLAIM_ID.match(payload["claim"])
            and payload.get("disposition") in DISPOSITIONS
        ):
            return payload["claim"], str(payload["disposition"])

    line = _PROMPT_LINE.match(prompt.prompt.strip())
    if line:
        return line.group(1), line.group(2)
    return None


def extract_updates(prompts: Iterable[Prompt]) -> list[tuple[str, str]]:
    """Every disposition update in ``prompts``, in arrival order.

    Order matters: Lavish's ``queueKey`` dedupe collapses same-claim updates queued
    *before* a send, but updates from separate sends arrive as separate prompts —
    the **last one per claim wins** when applied (:func:`apply_updates` folds in
    order).
    """
    updates: list[tuple[str, str]] = []
    for prompt in prompts:
        parsed = parse_disposition_prompt(prompt)
        if parsed is not None:
            updates.append(parsed)
    return updates


def claim_ids(analysis: Mapping[str, object]) -> set[str]:
    """The claim ids the analysis actually minted — the only valid disposition keys."""
    ids: set[str] = set()
    threads = analysis.get("threads")
    if not isinstance(threads, list):
        return ids
    for thread in threads:
        if not isinstance(thread, Mapping):
            continue
        claims = thread.get("claims")
        if not isinstance(claims, list):
            continue
        for claim in claims:
            if isinstance(claim, Mapping) and isinstance(claim.get("id"), str):
                ids.add(claim["id"])
    return ids


def apply_updates(
    current: Mapping[str, str],
    updates: Sequence[tuple[str, str]],
    valid_ids: set[str],
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Fold ``updates`` into ``current``; returns ``(new_state, rejected)``.

    An update whose claim id the analysis never minted is **rejected**, not stored —
    a hostile or stale key cannot grow the store beyond the claims the cockpit
    shows. ``unreviewed`` removes the entry (absence is the default state). Later
    updates for the same claim override earlier ones.
    """
    state = {k: v for k, v in current.items() if k in valid_ids and v in DISPOSITIONS}
    rejected: list[tuple[str, str]] = []
    for claim, disposition in updates:
        if claim not in valid_ids:
            rejected.append((claim, disposition))
            continue
        if disposition == "unreviewed":
            state.pop(claim, None)
        else:
            state[claim] = disposition
    return state, rejected


def progress(
    analysis: Mapping[str, object], dispositions: Mapping[str, str]
) -> list[tuple[str, int, int, int]]:
    """Per-thread ``(thread_id, reviewed, total, concerns)`` — the ADR's progress view."""
    rows: list[tuple[str, int, int, int]] = []
    threads = analysis.get("threads")
    if not isinstance(threads, list):
        return rows
    for thread in threads:
        if not isinstance(thread, Mapping):
            continue
        tid = thread.get("id")
        claims = thread.get("claims")
        if not isinstance(tid, str) or not isinstance(claims, list):
            continue
        ids = [c["id"] for c in claims if isinstance(c, Mapping) and isinstance(c.get("id"), str)]
        reviewed = sum(1 for cid in ids if dispositions.get(cid) is not None)
        concerns = sum(1 for cid in ids if dispositions.get(cid) == "concern")
        rows.append((tid, reviewed, len(ids), concerns))
    return rows


# --- I/O shell ----------------------------------------------------------------


def load_dispositions(path: Path) -> dict[str, str]:
    """Read the store; absent or corrupt resolves to ``{}`` (everything unreviewed).

    Degrade-never-crash, like the bake (ADR-0007): a corrupt store must not block a
    close — the reviewer's page state is re-sent on the next interaction anyway.
    Only well-shaped entries survive the read.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = raw.get("dispositions") if isinstance(raw, Mapping) else None
    if not isinstance(entries, Mapping):
        return {}
    return {
        k: v
        for k, v in entries.items()
        if isinstance(k, str) and _CLAIM_ID.match(k) and v in DISPOSITIONS and v != "unreviewed"
    }


def save_dispositions(path: Path, state: Mapping[str, str]) -> None:
    """Write the store atomically enough for its single-writer life (the loop)."""
    payload = {"schema": _SCHEMA, "dispositions": dict(sorted(state.items()))}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def apply(out_dir: Path) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Apply the most recent poll's disposition updates to the store.

    Reads ``last-poll.toon`` (the raw poll the loop already persisted) and
    ``analysis.json`` (for the valid claim ids), merges, writes
    ``dispositions.json``. Returns ``(applied, rejected)``. Missing poll or
    analysis simply applies nothing — never a failed loop turn.
    """
    toon_path = out_dir / LAST_POLL_NAME
    try:
        toon = toon_path.read_text(encoding="utf-8")
    except OSError:
        return [], []
    updates = extract_updates(extract_prompts(toon))
    if not updates:
        return [], []

    try:
        analysis = json.loads((out_dir / ANALYSIS_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        analysis = {}
    valid = claim_ids(analysis if isinstance(analysis, Mapping) else {})

    store_path = out_dir / DISPOSITIONS_NAME
    state, rejected = apply_updates(load_dispositions(store_path), updates, valid)
    save_dispositions(store_path, state)
    applied = [u for u in updates if u not in rejected]
    return applied, rejected


def main(argv: list[str] | None = None) -> int:
    """CLI for the skill: ``apply`` (from the last poll) and ``show`` (state + progress).

    There is deliberately no ``set`` command: dispositions are reviewer-originated
    (ADR-0012), so the only write path parses the reviewer's own queued feedback.
    """
    parser = argparse.ArgumentParser(
        prog="dispositions",
        description="Apply/inspect reviewer dispositions (reviewer-originated only).",
    )
    parser.add_argument(
        "command", choices=("apply", "show"), help="apply: fold last-poll.toon updates; show: state"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(".review-agent"),
        help="Review artifacts dir (default: .review-agent).",
    )
    args = parser.parse_args(argv)

    if args.command == "apply":
        applied, rejected = apply(args.out)
        for claim, disposition in applied:
            print(f"applied: {claim} -> {disposition}")
        for claim, disposition in rejected:
            print(f"rejected (unknown claim id): {claim} -> {disposition}", file=sys.stderr)
        if not applied and not rejected:
            print("no disposition updates in the last poll")
        return 0

    state = load_dispositions(args.out / DISPOSITIONS_NAME)
    try:
        analysis = json.loads((args.out / ANALYSIS_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        analysis = {}
    for claim, disposition in sorted(state.items()):
        print(f"{claim}: {disposition}")
    if isinstance(analysis, Mapping):
        for tid, reviewed, total, concerns in progress(analysis, state):
            note = f" ({concerns} concern{'s' if concerns != 1 else ''})" if concerns else ""
            print(f"{tid}: {reviewed}/{total} reviewed{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
