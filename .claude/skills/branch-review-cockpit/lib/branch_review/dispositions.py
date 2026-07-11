"""Reviewer Dispositions — per-step review state, set by the human alone (ADR-0012/0016).

Each L2 Review Step carries a disposition — ``unreviewed | looks-right | concern |
follow-up | skipped`` — that records what the reviewer actually did with it
(ADR-0016's five-state judgment vocabulary: ``looks-right`` attests comprehension
with no objection, ``follow-up`` is a still-open question, and ``skipped`` is a
*deliberate, attributed* act — distinct from ``unreviewed`` absence — so the baked
account reports honest coverage rather than an unfinished review). The cockpit's
in-page controls emit disposition updates through Lavish's feedback protocol (the
host-seam spike's verified channel: a ``tag: choice`` prompt whose ``Context data:``
block carries ``{kind: "disposition", step, disposition}``, deduplicated pre-send by
``queueKey``); the answer loop lands the raw poll in ``last-poll.toon``; and this
module is the **deterministic bridge** from that untrusted text to the persisted
store — the agent never hand-parses or hand-writes a disposition (the ADR-0002
posture: reviewer input is data crossing a chokepoint, never text the agent
re-types).

The store (``dispositions.json``) is **run-scoped** like the Q&A transcript: keyed by
the analysis's stable step ids (``t2.s3``, minted per run — ADR-0012's identity
contract), reset by the collector on regeneration, carried across ``Esc`` /
``/review-resume``. ``unreviewed`` is the default state and is stored as absence:
setting a step back to ``unreviewed`` removes its entry; every *other* state —
including ``skipped`` — persists, so a deliberate skip survives resume and is
distinguishable from a step never looked at.

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

from branch_review.analysis import step_ids as _analysis_step_ids
from branch_review.feedback import LAST_POLL_NAME, Prompt, extract_prompts

# The closed disposition vocabulary (ADR-0012, reframed by ADR-0016). ``unreviewed`` is
# the default and is represented in the store by absence; every other state persists.
DISPOSITIONS = ("unreviewed", "looks-right", "concern", "follow-up", "skipped")

DISPOSITIONS_NAME = "dispositions.json"
ANALYSIS_NAME = "analysis.json"

_SCHEMA = "review-dispositions/0.2"

# A step id as ADR-0016 mints them: thread-scoped, e.g. ``t2.s3``.
_STEP_ID = re.compile(r"^t\d+\.s\d+$")

# The structured payload the in-page control attaches (spike #38): the SDK appends
# ``data`` to the prompt text as a ``Context data:`` block holding the JSON.
_CONTEXT_DATA = re.compile(r"Context data:\s*(\{.*\})", re.DOTALL)

# The guaranteed-floor fallback: the control's human-readable prompt line itself. The
# state alternation is built from :data:`DISPOSITIONS` so the fallback and the
# structured-JSON path (which validates against ``DISPOSITIONS`` directly) can never
# drift to accept different vocabularies.
_VOCAB_ALTERNATION = "|".join(re.escape(d) for d in DISPOSITIONS)
_PROMPT_LINE = re.compile(rf"^Disposition set:\s*(t\d+\.s\d+)\s*->\s*({_VOCAB_ALTERNATION})\b")


def parse_disposition_prompt(prompt: Prompt) -> tuple[str, str] | None:
    """Read one poll prompt as a disposition update, or ``None`` if it isn't one.

    Gated on the control channel first: the in-page controls send ``tag: "choice"``
    (the #38 spike's verified contract), so a free-form chat message
    (``tag: "message"``) that merely *says* "Disposition set: …" is never an
    update — it stays a question, is answered in chat, and survives in the baked
    Q&A instead of being filtered out as state. Within the channel, prefers the
    structured ``Context data:`` JSON (``{kind: "disposition", step,
    disposition}``) and falls back to the control's own prompt line. Both paths are
    strict: the step id must have the ``t<N>.s<N>`` shape and the disposition must
    be in the closed vocabulary — hostile text that fails either (including the
    retired ``t<N>.c<N>`` claim ids and ``verified``/``question-open`` values) is
    simply **not a disposition**, and text that passes can only ever name an enum
    value, which is inert by construction.
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
            and isinstance(payload.get("step"), str)
            and _STEP_ID.match(payload["step"])
            and payload.get("disposition") in DISPOSITIONS
        ):
            return payload["step"], str(payload["disposition"])

    line = _PROMPT_LINE.match(prompt.prompt.strip())
    if line:
        return line.group(1), line.group(2)
    return None


def extract_updates(prompts: Iterable[Prompt]) -> list[tuple[str, str]]:
    """Every disposition update in ``prompts``, in arrival order.

    Order matters: Lavish's ``queueKey`` dedupe collapses same-step updates queued
    *before* a send, but updates from separate sends arrive as separate prompts —
    the **last one per step wins** when applied (:func:`apply_updates` folds in
    order).
    """
    updates: list[tuple[str, str]] = []
    for prompt in prompts:
        parsed = parse_disposition_prompt(prompt)
        if parsed is not None:
            updates.append(parsed)
    return updates


def step_ids(analysis: Mapping[str, object]) -> set[str]:
    """The step ids the analysis actually minted — the only valid disposition keys.

    The set of the canonical ordered walk (:func:`branch_review.analysis.step_ids`);
    dispositions only needs membership, so it drops order and duplicates.
    """
    return set(_analysis_step_ids(analysis))


def apply_updates(
    current: Mapping[str, str],
    updates: Sequence[tuple[str, str]],
    valid_ids: set[str],
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Fold ``updates`` into ``current``; returns ``(new_state, rejected)``.

    An update whose step id the analysis never minted is **rejected**, not stored —
    a hostile or stale key cannot grow the store beyond the steps the cockpit
    shows. ``unreviewed`` removes the entry (absence is the default state); every
    other state, ``skipped`` included, is stored. Later updates for the same step
    override earlier ones.
    """
    state = {k: v for k, v in current.items() if k in valid_ids and v in DISPOSITIONS}
    rejected: list[tuple[str, str]] = []
    for step, disposition in updates:
        if step not in valid_ids:
            rejected.append((step, disposition))
            continue
        if disposition == "unreviewed":
            state.pop(step, None)
        else:
            state[step] = disposition
    return state, rejected


def progress(
    analysis: Mapping[str, object], dispositions: Mapping[str, str]
) -> list[tuple[str, int, int, int]]:
    """Per-thread ``(thread_id, reviewed, total, concerns)`` — the ADR's progress view.

    ``reviewed`` counts every step the reviewer moved off ``unreviewed`` — a
    ``skipped`` step is *deliberately* addressed, so it counts as covered, not as a
    gap.
    """
    rows: list[tuple[str, int, int, int]] = []
    threads = analysis.get("threads")
    if not isinstance(threads, list):
        return rows
    for thread in threads:
        if not isinstance(thread, Mapping):
            continue
        tid = thread.get("id")
        steps = thread.get("steps")
        if not isinstance(tid, str) or not isinstance(steps, list):
            continue
        ids = [s["id"] for s in steps if isinstance(s, Mapping) and isinstance(s.get("id"), str)]
        reviewed = sum(1 for sid in ids if dispositions.get(sid) is not None)
        concerns = sum(1 for sid in ids if dispositions.get(sid) == "concern")
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
        if isinstance(k, str) and _STEP_ID.match(k) and v in DISPOSITIONS and v != "unreviewed"
    }


def save_dispositions(path: Path, state: Mapping[str, str]) -> None:
    """Write the store atomically enough for its single-writer life (the loop)."""
    payload = {"schema": _SCHEMA, "dispositions": dict(sorted(state.items()))}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def apply(out_dir: Path) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Apply the most recent poll's disposition updates to the store.

    Reads ``last-poll.toon`` (the raw poll the loop already persisted) and
    ``analysis.json`` (for the valid step ids), merges, writes
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
    valid = step_ids(analysis if isinstance(analysis, Mapping) else {})

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
        for step, disposition in applied:
            print(f"applied: {step} -> {disposition}")
        for step, disposition in rejected:
            print(f"rejected (unknown step id): {step} -> {disposition}", file=sys.stderr)
        if not applied and not rejected:
            print("no disposition updates in the last poll")
        return 0

    state = load_dispositions(args.out / DISPOSITIONS_NAME)
    try:
        analysis = json.loads((args.out / ANALYSIS_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        analysis = {}
    for step, disposition in sorted(state.items()):
        print(f"{step}: {disposition}")
    if isinstance(analysis, Mapping):
        for tid, reviewed, total, concerns in progress(analysis, state):
            note = f" ({concerns} concern{'s' if concerns != 1 else ''})" if concerns else ""
            print(f"{tid}: {reviewed}/{total} reviewed{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
