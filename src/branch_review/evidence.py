"""Live evidence fragment injection — bounded page mutation through the loop (issue #43).

The hybrid Layered Review (ADR-0009) pre-authors the cockpit and answers loop
questions in chat (ADR-0003: the page is never regenerated per answer). This module
is the one sanctioned exception, as amended by ADR-0009: when a mid-review answer
*is* new evidence the page should keep — "what about the callers of this?" answered
with the callers themselves — the evidence becomes an escaped fragment injected at a
**pre-planted seam** under the claim it substantiates. Same mechanism as the Q&A
seam the bake fills: seam-bounded, idempotent, nothing outside the markers is ever
touched.

Delivery follows the #38 spike's verdict: Lavish watches ``review.html`` and
re-renders the open page on write (chokidar → SSE reload, scroll restored), so a
successful injection appears in the reviewer's browser by itself. The floor —
lint failure, missing seam, malformed claim id — is **chat-only**: the injection is
refused, nothing is written, and the loop answers in chat as usual.

Three properties make the path safe:

1. **The Escape Boundary owns the body.** Evidence bodies are repo/diff content —
   untrusted — and cross :func:`branch_review.escape.diff_fragment` (escaped,
   marker-wrapped) at render time; the raw text is stored, so re-rendering never
   double-escapes.
2. **The Cockpit Linter gates the write.** The candidate page is linted *before*
   anything lands on disk; a failure blocks both the cockpit write and the record —
   an injection can never leave the page worse than it found it.
3. **The record is run-scoped and separate.** Injected fragments are recorded in
   ``live-evidence.json`` beside the session (reset on regeneration like the
   transcript), **not** appended into ``analysis.json`` — the analysis stays the
   isolated analyst's untouched testimony (ADR-0011). The Q&A log already records
   the exchange that produced the fragment; the baked page keeps the fragment
   because the bake only ever rewrites its own Q&A seam.

Pure policy (seam rendering/injection) over a thin I/O shell (:func:`add_evidence`,
:func:`main`), like the rest of the package.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from branch_review.escape import diff_fragment, escape_text
from branch_review.feedback import DEFAULT_COCKPIT
from branch_review.lint import lint_cockpit

# The run-scoped record of injected fragments (reset on regeneration by the
# collector; carried across resume). Raw text in, escaping at render time.
EVIDENCE_NAME = "live-evidence.json"

_SCHEMA = "review-live-evidence/0.1"

# A claim id as the analysis mints them (ADR-0012) — validated before any seam
# string is built, so a hostile id can never smuggle markup or marker syntax.
_CLAIM_ID = re.compile(r"^t\d+\.c\d+$")


def evidence_seam(claim_id: str) -> tuple[str, str]:
    """The seam marker pair for one claim's live evidence.

    Planted empty by the cockpit author under the claim's evidence list (SKILL
    step 5), exactly like the Q&A seam: HTML comments, invisible, and distinct
    from the Escape Boundary's ``brc:untrusted`` markers so the linter's balance
    count is unperturbed.
    """
    if not _CLAIM_ID.match(claim_id):
        raise ValueError(f"not a claim id: {claim_id!r}")
    return f"<!--brc:evidence:{claim_id}-->", f"<!--/brc:evidence:{claim_id}-->"


@dataclass(frozen=True)
class EvidenceFragment:
    """One injected fragment: which claim it substantiates, and its raw content."""

    claim: str
    seq: int
    ts: str
    title: str
    body: str


def render_claim_evidence(fragments: Sequence[EvidenceFragment]) -> str:
    """Render one claim's fragments as the seam's full content (idempotent source).

    The seam is always rewritten wholesale from the record, so re-injection can
    never duplicate earlier fragments. The ``title`` is the agent's trusted prose
    (escaped anyway — the boundary is unconditional); the ``body`` is repo/diff
    content and crosses :func:`diff_fragment` like any other evidence.
    """
    parts: list[str] = []
    for fragment in fragments:
        parts.append('<figure class="live-evidence">')
        parts.append(
            f"  <figcaption>{escape_text(fragment.title)} "
            f'<span class="live-evidence-meta">added during review</span></figcaption>'
        )
        parts.append(f"  {diff_fragment(fragment.body)}")
        parts.append("</figure>")
    return "\n".join(parts) + ("\n" if parts else "")


def inject_evidence_html(html: str, claim_id: str, seam_content: str) -> tuple[str, bool]:
    """Replace the claim's seam content with ``seam_content``; nothing else changes.

    Returns ``(new_html, seam_found)``. Unlike the Q&A injector there is **no**
    fallback insertion point: a cockpit without the claim's seam simply cannot
    take live evidence (the chat-only floor) — inventing a location would break
    "attached to the right claim". A ``lambda`` supplies the replacement so
    backslashes in the rendered content are never read as regex backreferences.
    """
    seam_open, seam_close = evidence_seam(claim_id)
    if seam_open not in html or seam_close not in html:
        return html, False
    seam = re.compile(re.escape(seam_open) + ".*?" + re.escape(seam_close), re.DOTALL)
    block = f"{seam_open}\n{seam_content}{seam_close}"
    return seam.sub(lambda _match: block, html, count=1), True


# --- I/O shell ----------------------------------------------------------------


def load_fragments(path: Path) -> list[EvidenceFragment]:
    """Read the record; absent or corrupt resolves to ``[]`` (degrade, never crash)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = raw.get("fragments") if isinstance(raw, Mapping) else None
    if not isinstance(entries, list):
        return []
    fragments: list[EvidenceFragment] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        claim = entry.get("claim")
        if not isinstance(claim, str) or not _CLAIM_ID.match(claim):
            continue
        fragments.append(
            EvidenceFragment(
                claim=claim,
                seq=int(entry.get("seq", len(fragments) + 1)),
                ts=str(entry.get("ts", "")),
                title=str(entry.get("title", "")),
                body=str(entry.get("body", "")),
            )
        )
    return fragments


def save_fragments(path: Path, fragments: Sequence[EvidenceFragment]) -> None:
    payload = {
        "schema": _SCHEMA,
        "fragments": [
            {"claim": f.claim, "seq": f.seq, "ts": f.ts, "title": f.title, "body": f.body}
            for f in fragments
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def add_evidence(
    cockpit: Path,
    claim_id: str,
    title: str,
    body: str,
    *,
    csp_mode: str = "interactive",
    styling: str = "vendored",
    now: datetime | None = None,
) -> list[str]:
    """Inject one new evidence fragment under ``claim_id``; return blocking errors.

    The full gate, in order: claim id shape, non-empty title/body, seam present,
    and the **post-injection page passes the Cockpit Linter** — only then are the
    record and the cockpit written (both or neither). A non-empty return means
    nothing was written and the loop should answer in chat instead (the floor).
    """
    if not _CLAIM_ID.match(claim_id):
        return [f"not a claim id: {claim_id!r}"]
    if not title.strip():
        return ["evidence title must not be empty"]
    if not body.strip():
        return ["evidence body must not be empty"]

    try:
        html = cockpit.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"cannot read cockpit: {exc}"]

    record_path = cockpit.parent / EVIDENCE_NAME
    fragments = load_fragments(record_path)
    fragment = EvidenceFragment(
        claim=claim_id,
        seq=max((f.seq for f in fragments), default=0) + 1,
        ts=(now or datetime.now(UTC)).isoformat(),
        title=title,
        body=body,
    )
    candidate_fragments = [*fragments, fragment]

    seam_content = render_claim_evidence([f for f in candidate_fragments if f.claim == claim_id])
    candidate_html, seam_found = inject_evidence_html(html, claim_id, seam_content)
    if not seam_found:
        return [
            f"no evidence seam for {claim_id} in {cockpit.name} — "
            "the cockpit was authored without one; answer in chat instead"
        ]

    lint_errors = lint_cockpit(candidate_html, styling=styling, csp_mode=csp_mode)
    if lint_errors:
        return [f"lint: {error}" for error in lint_errors]

    save_fragments(record_path, candidate_fragments)
    cockpit.write_text(candidate_html, encoding="utf-8")
    return []


def main(argv: list[str] | None = None) -> int:
    """CLI for the skill: inject one evidence fragment, or fail without touching disk.

    The body is read from ``--input`` (a file the agent wrote with its Write tool)
    — never from argv, so untrusted repo/diff content stays off the command line
    (the ADR-0002 posture).
    """
    parser = argparse.ArgumentParser(
        prog="inject_evidence",
        description="Inject an escaped, linted evidence fragment at a claim's seam.",
    )
    parser.add_argument("claim_id", help="The claim the evidence substantiates (e.g. t1.c2).")
    parser.add_argument("--title", required=True, help="Short caption (your trusted prose).")
    parser.add_argument(
        "--input", type=Path, required=True, help="File holding the raw evidence body."
    )
    parser.add_argument(
        "--cockpit", type=Path, default=DEFAULT_COCKPIT, help="Cockpit review.html."
    )
    parser.add_argument(
        "--csp-mode",
        choices=("interactive", "strict"),
        default="interactive",
        help="Lint baseline for the post-injection page (default: interactive).",
    )
    parser.add_argument(
        "--styling",
        choices=("vendored", "cdn"),
        default="vendored",
        help="Resolved cockpit styling for the lint (default: vendored).",
    )
    args = parser.parse_args(argv)

    try:
        body = args.input.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot read evidence body: {exc}", file=sys.stderr)
        return 2

    errors = add_evidence(
        args.cockpit,
        args.claim_id,
        args.title,
        body,
        csp_mode=args.csp_mode,
        styling=args.styling,
    )
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        print("Injection blocked — nothing was written; answer in chat.", file=sys.stderr)
        return 1

    print(f"Evidence injected at {args.claim_id} (page re-renders if served via Lavish).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
