"""Q&A bake at close — fold the transcript into a self-contained artifact (issue #9).

During a review the Feedback Loop (:mod:`branch_review.feedback`) appends every
exchange to ``qa.jsonl`` and shows each answer *live* in the Lavish chat — the cockpit
on disk is never regenerated per answer (ADR-0003). At close that live discussion would
be lost the moment Lavish stops serving the page. This module is the **bake**: it folds
``qa.jsonl`` back into ``review.html`` once, so the saved cockpit shows the whole
conversation when reopened later in a plain browser with no Lavish running, and it emits
an optional ``review.md`` suitable for pasting into a pull request.

Three things make the bake safe and faithful:

1. **The Escape Boundary still owns untrusted output.** Browser feedback is untrusted
   data (DESIGN, ADR-0002): every reviewer-originated string — the question, the
   annotated line, the CSS selector — crosses :func:`branch_review.escape.fragment`
   (entity-escaped, marker-wrapped) before it lands in the HTML, so a ``<script>`` typed
   into a prompt renders as text and the Cockpit Linter verifies the region. The agent's
   own answer is trusted prose, escaped through :func:`~branch_review.escape.escape_text`
   for uniformity.
2. **A bounded prompt extractor, not a TOON parser (ADR-0007).** ``qa.jsonl`` stores each
   poll's raw TOON verbatim (the live loop reads TOON directly and writes no parser). To
   render a clean Q&A log the bake reads only one thing from that blob: the single
   ``prompts[N]{…}:`` tabular block Lavish emits. :func:`extract_prompts` is scoped to
   that block alone — it runs offline at close, never in the live loop, and its output is
   re-hardened through the Escape Boundary above. This is the deliberate, narrow
   exception to "no TOON parser is written" (see ``docs/adr/0007-bake-prompt-extractor.md``).
3. **The portable artifact gets the strict CSP.** The interactive cockpit ships the
   relaxed :data:`~branch_review.escape.INTERACTIVE_CSP` so Lavish's injected editor UI
   runs (ADR-0004); once baked it is a ``file://`` artifact that needs none of that, so
   the bake swaps the policy back to :data:`~branch_review.escape.STRICT_CSP` — the baked
   file passes ``lint_cockpit.py --csp-mode strict``, which is the mechanical proof of
   "escaped and self-contained".

The bake is **idempotent**: the Q&A section is delimited by a seam
(:data:`QA_SEAM_OPEN` / :data:`QA_SEAM_CLOSE`) the agent leaves empty in the authored
cockpit, so re-baking replaces it in place rather than appending a second copy.

See ``DESIGN.md`` ("Feedback loop" / "Persistence"), ``CONTEXT.md`` (Q&A Log), and
``docs/adr/0007-bake-prompt-extractor.md``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from branch_review.dispositions import (
    DISPOSITIONS_NAME,
    load_dispositions,
    parse_disposition_prompt,
    progress,
)

# ``QA_SEAM_OPEN`` / ``QA_SEAM_CLOSE`` are re-exported here (the ``as`` form marks the
# re-export explicit) as the bake's public contract: escape owns the one seam-marker
# definition, so the bake and the Cockpit Linter that checks the seam can never drift.
from branch_review.escape import (
    QA_SEAM_CLOSE as QA_SEAM_CLOSE,
)
from branch_review.escape import (
    QA_SEAM_OPEN as QA_SEAM_OPEN,
)
from branch_review.escape import STRICT_CSP, escape_text, fragment

# ``Prompt`` / ``extract_prompts`` are re-exported here (the ``as`` form marks the
# re-export explicit) for compatibility: the extractor moved to
# :mod:`branch_review.feedback` (which owns the poll format) so the dispositions
# bridge can share it cycle-free.
from branch_review.feedback import (
    DEFAULT_COCKPIT,
    QA_NAME,
)
from branch_review.feedback import (
    Prompt as Prompt,
)
from branch_review.feedback import (
    extract_prompts as extract_prompts,
)

# Files the bake reads and writes, all under the cockpit's own (gitignored) dir.
# ``DEFAULT_COCKPIT`` (the cockpit path) and ``QA_NAME`` (the transcript) are the loop's
# contract, owned by :mod:`branch_review.feedback`; we consume them rather than redefine.
ANALYSIS_NAME = "analysis.json"  # the structured Analysis (for the Markdown export)
DEFAULT_MD_NAME = "review.md"  # the optional Markdown export

# The bake fills the seam the agent authors as an empty placeholder
# ``<!--brc:qa-log--><!--/brc:qa-log-->`` after the Test Checklist (SKILL.md §5),
# replacing everything between the markers so re-baking is idempotent.


# --- Loading the transcript -------------------------------------------------


@dataclass(frozen=True)
class Exchange:
    """One folded Q&A exchange: the prompts the reviewer sent and the agent's one answer.

    A single poll can carry several prompts but is answered once, so ``prompts`` is a list
    while ``answer`` is a single trusted string. ``seq``/``ts`` are the record's own
    metadata from ``qa.jsonl``.
    """

    seq: int
    ts: str
    prompts: list[Prompt]
    answer: str


def load_exchanges(qa_path: Path) -> list[Exchange]:
    """Read ``qa.jsonl`` into :class:`Exchange` records, extracting prompts per line.

    A missing file is the ordinary "no questions were asked" case → ``[]``. Each line is a
    record ``{seq, ts, feedback_raw, answer}`` written by
    :func:`branch_review.feedback.append_exchange`; its ``feedback_raw`` is run through
    :func:`extract_prompts`. A blank line is skipped; a
    line that is not a JSON object is skipped rather than aborting the whole bake, so one
    corrupt record never costs the rest of the transcript.
    """
    if not qa_path.is_file():
        return []
    exchanges: list[Exchange] = []
    for line in qa_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        # ``seq`` is our own field (feedback.append_exchange writes it as an int), but a
        # hand-edited or truncated record could carry ``null``/``"bad"``; fall back to the
        # positional index rather than let one malformed line abort the whole bake.
        try:
            seq = int(record.get("seq", len(exchanges) + 1))
        except (TypeError, ValueError):
            seq = len(exchanges) + 1
        exchanges.append(
            Exchange(
                seq=seq,
                ts=str(record.get("ts", "")),
                prompts=extract_prompts(str(record.get("feedback_raw", ""))),
                answer=str(record.get("answer", "")),
            )
        )
    return exchanges


# --- Reviewer dispositions at close (ADR-0012) --------------------------------

# List order for the outcome: what needs attention first, what was cleared, what
# was never examined. The unreviewed tail is deliberate — nothing hidden.
_OUTCOME_ORDER = ("concern", "question-open", "verified", "unreviewed")


def _without_disposition_prompts(exchanges: Sequence[Exchange]) -> list[Exchange]:
    """Drop disposition updates from the Q&A before rendering (ADR-0012).

    A disposition click travels the same feedback channel as a question, so it lands
    in ``qa.jsonl`` whenever the loop replies to a poll that carried one (the ack
    that reopens the presence-gated channel, or a mixed poll). It is review *state*,
    not conversation — the Review outcome section accounts for it; rendering it as
    Q&A would duplicate it as noise. An exchange whose prompts were **all**
    dispositions disappears entirely (its answer was just the ack).
    """
    filtered: list[Exchange] = []
    for exchange in exchanges:
        kept = [p for p in exchange.prompts if parse_disposition_prompt(p) is None]
        if exchange.prompts and not kept:
            continue
        if len(kept) != len(exchange.prompts):
            exchange = replace(exchange, prompts=kept)
        filtered.append(exchange)
    return filtered


def _claims_by_disposition(
    analysis: Mapping[str, object], dispositions: Mapping[str, str]
) -> list[tuple[str, str, str]]:
    """Every claim as ``(disposition, claim_id, summary)``, grouped concern-first.

    Groups follow :data:`_OUTCOME_ORDER`; within a group, analysis order (the Review
    Route). A claim absent from the store is ``unreviewed`` — listed, never dropped.
    """
    groups: dict[str, list[tuple[str, str]]] = {d: [] for d in _OUTCOME_ORDER}
    threads = analysis.get("threads")
    if not isinstance(threads, list):
        return []
    for thread in threads:
        if not isinstance(thread, Mapping):
            continue
        claims = thread.get("claims")
        if not isinstance(claims, list):
            continue
        for claim in claims:
            if not isinstance(claim, Mapping) or not isinstance(claim.get("id"), str):
                continue
            disposition = dispositions.get(claim["id"], "unreviewed")
            if disposition not in _OUTCOME_ORDER:
                disposition = "unreviewed"
            groups[disposition].append((claim["id"], str(claim.get("summary", ""))))
    return [(d, cid, summary) for d in _OUTCOME_ORDER for cid, summary in groups[d]]


def _outcome_counts_line(rows: Sequence[tuple[str, str, str]]) -> str:
    """The one-line aggregate, in the ADR's reading order (verified first)."""
    counts = Counter(disposition for disposition, _cid, _summary in rows)
    ordered = ("verified", "concern", "question-open", "unreviewed")
    return " · ".join(f"{d} {counts.get(d, 0)}" for d in ordered)


def _progress_text(reviewed: int, total: int, concerns: int) -> str:
    """One thread's totals, in the exact wording the live page's progress span uses."""
    text = f"{reviewed}/{total} reviewed"
    if concerns:
        text += f" · {concerns} concern{'s' if concerns != 1 else ''}"
    return text


def _thread_titles(analysis: Mapping[str, object]) -> dict[str, str]:
    threads = analysis.get("threads")
    if not isinstance(threads, list):
        return {}
    return {
        thread["id"]: str(thread.get("title", ""))
        for thread in threads
        if isinstance(thread, Mapping) and isinstance(thread.get("id"), str)
    }


# The open tag of any ``<details>`` element, and within it a claim-shaped id and any
# previously baked disposition attribute (stripped first, so re-baking replaces).
_DETAILS_TAG = re.compile(r"<details\b[^>]*>", re.IGNORECASE)
_CLAIM_DETAILS_ID = re.compile(r'\bid\s*=\s*"(t\d+\.c\d+)"')
_DISPOSITION_ATTR = re.compile(r'\s+data-disposition\s*=\s*"[^"]*"')


def bake_dispositions_html(html: str, dispositions: Mapping[str, str]) -> str:
    """Stamp each claim's reviewer disposition onto its ``<details>`` open tag.

    On the live page the tint comes from ``app.js`` setting ``data-disposition``;
    the baked ``file://`` artifact runs no disposition code (a record, not a review
    surface), so the bake writes the same attribute statically and the stylesheet's
    existing ``details.claim[data-disposition=…]`` rules light up under the strict
    CSP with no script at all. Only values in the closed vocabulary are ever
    stamped — anything else (including ``unreviewed``, which is absence) strips the
    attribute, which also makes re-baking idempotent.
    """

    def stamp(match: re.Match[str]) -> str:
        tag = match.group(0)
        id_match = _CLAIM_DETAILS_ID.search(tag)
        if not id_match:
            return tag
        tag = _DISPOSITION_ATTR.sub("", tag)
        disposition = dispositions.get(id_match.group(1))
        if disposition in _OUTCOME_ORDER and disposition != "unreviewed":
            tag = f'{tag[:-1]} data-disposition="{disposition}">'
        return tag

    return _DETAILS_TAG.sub(stamp, html)


# The attribution the ADR draws the verdict line with: the aggregate is the
# reviewer's, and the tool never prints a bottom line it authored.
_OUTCOME_NOTE = (
    "Dispositions were set by the reviewer in the cockpit; unreviewed claims are "
    "listed, never hidden. The review tool states per-claim confidence only and "
    "issues no overall verdict."
)


def render_outcome_html(
    analysis: Mapping[str, object] | None, dispositions: Mapping[str, str]
) -> str:
    """The close-time Review outcome section — the reviewer's dispositions (ADR-0012).

    States what the reviewer verified, what raised concerns, what has a question
    still open, and what was never examined — with per-thread totals so attention
    coverage is visible at a glance. The claim ids and summaries are the
    analysis's trusted prose and the disposition values a validated closed
    vocabulary, but everything renders through ``escape_text`` anyway — the boundary
    is unconditional. Returns ``""`` when the analysis has no claims (missing or
    old-schema analysis): the bake degrades, never crashes (ADR-0007).
    """
    analysis = analysis or {}
    rows = _claims_by_disposition(analysis, dispositions)
    if not rows:
        return ""
    parts = [
        '<section id="review-outcome">',
        "  <h2>Review outcome</h2>",
        f'  <p class="outcome-counts">{escape_text(_outcome_counts_line(rows))}</p>',
    ]
    totals = progress(analysis, dispositions)
    if totals:
        titles = _thread_titles(analysis)
        parts.append('  <ul class="outcome-threads">')
        for tid, reviewed, total, concerns in totals:
            head = f"<code>{escape_text(tid)}</code>"
            if titles.get(tid):
                head += f" {escape_text(titles[tid])}"
            parts.append(
                f"    <li>{head} — {escape_text(_progress_text(reviewed, total, concerns))}</li>"
            )
        parts.append("  </ul>")
    parts.append('  <ul class="outcome-list">')
    for disposition, cid, summary in rows:
        parts.append(
            f'    <li><span class="disposition {escape_text(disposition)}">'
            f"{escape_text(disposition)}</span> <code>{escape_text(cid)}</code>"
            f" — {escape_text(summary)}</li>"
        )
    parts.append("  </ul>")
    parts.append(f'  <p class="outcome-note">{escape_text(_OUTCOME_NOTE)}</p>')
    parts.append("</section>")
    return "\n".join(parts) + "\n"


# --- HTML rendering + injection ---------------------------------------------


def render_qa_html(exchanges: Sequence[Exchange]) -> str:
    """Render the folded transcript as one escaped, self-contained ``<section>``.

    Every reviewer-originated value (the question, the annotated line, the selector) goes
    through :func:`~branch_review.escape.fragment` so it is entity-escaped, marker-wrapped,
    and linter-verified; the agent's answer goes through
    :func:`~branch_review.escape.escape_text` as trusted prose. The result drops straight
    between the Q&A seam markers — no further escaping at the call site.
    """
    parts: list[str] = ['<section id="qa-log">', "  <h2>Q&amp;A Log</h2>"]
    if not exchanges:
        parts.append('  <p class="qa-empty">No questions were asked during this review.</p>')
    for exchange in exchanges:
        parts.append('  <div class="qa-exchange">')
        parts.append(
            f'    <p class="qa-num">#{escape_text(str(exchange.seq))} '
            f"&middot; <time>{escape_text(exchange.ts)}</time></p>"
        )
        for prompt in exchange.prompts:
            parts.append('    <div class="qa-question">')
            parts.append(f'      <p class="qa-prompt">{fragment(prompt.prompt)}</p>')
            if prompt.is_annotation:
                parts.append(f'      <pre class="qa-anchor">{fragment(prompt.text)}</pre>')
                parts.append(f'      <p class="qa-selector">{fragment(prompt.selector)}</p>')
            parts.append("    </div>")
        parts.append(f'    <pre class="qa-answer">{escape_text(exchange.answer)}</pre>')
        parts.append("  </div>")
    parts.append("</section>")
    return "\n".join(parts) + "\n"


def inject_qa(html: str, qa_section: str) -> str:
    """Place ``qa_section`` between the Q&A seam markers, idempotently.

    When the authored cockpit carries the seam (the common path), everything between the
    markers is replaced — so re-baking after more questions never appends a second log.
    When it does not (an older or hand-built cockpit), the section is inserted before
    ``</body>`` so the bake still works on any base ``review.html`` — the guarantee the
    issue's "given a base review.html" test relies on. A ``lambda`` supplies the
    replacement so backslashes in the rendered section are never read as regex
    backreferences.
    """
    block = f"{QA_SEAM_OPEN}\n{qa_section}{QA_SEAM_CLOSE}"
    if QA_SEAM_OPEN in html and QA_SEAM_CLOSE in html:
        seam = re.compile(re.escape(QA_SEAM_OPEN) + ".*?" + re.escape(QA_SEAM_CLOSE), re.DOTALL)
        return seam.sub(lambda _match: block, html, count=1)
    insert = block + "\n"
    idx = html.rfind("</body>")
    if idx == -1:
        return html + "\n" + insert
    return html[:idx] + insert + html[idx:]


# A ``<meta …>`` tag (DOTALL: the authored CSP meta spans two lines) and the ``content``
# attribute within it. The CSP is identified by ``http-equiv`` rather than attribute
# order, so a meta with ``content`` before ``http-equiv`` is still matched.
_META_TAG = re.compile(r"<meta\b[^>]*>", re.IGNORECASE | re.DOTALL)
_CONTENT_ATTR = re.compile(r"(content\s*=\s*)([\"'])(.*?)(\2)", re.IGNORECASE | re.DOTALL)


def swap_csp(html: str, *, target: str = STRICT_CSP) -> tuple[str, bool]:
    """Rewrite the cockpit's Content-Security-Policy meta to ``target`` (strict by default).

    The interactive cockpit trusts the Lavish CDN and inline script/style so Lavish's
    editor UI runs (ADR-0004); the baked ``file://`` artifact needs none of that, so the
    bake tightens the policy back to :data:`~branch_review.escape.STRICT_CSP`. Returns the
    rewritten HTML and whether a CSP meta was found and changed, so the caller can report
    a cockpit that carried no policy (which the post-bake strict lint would then flag).
    """
    swapped = False

    def rewrite_meta(match: re.Match[str]) -> str:
        nonlocal swapped
        tag = match.group(0)
        if "content-security-policy" not in tag.lower():
            return tag
        new_tag, replaced = _CONTENT_ATTR.subn(
            lambda content: f"{content.group(1)}{content.group(2)}{target}{content.group(4)}",
            tag,
            count=1,
        )
        if replaced:
            swapped = True
            return new_tag
        return tag

    return _META_TAG.sub(rewrite_meta, html), swapped


@dataclass(frozen=True)
class BakeResult:
    """What the bake did, for a human-readable summary line."""

    exchanges: int
    prompts: int
    csp_swapped: bool
    markdown_path: Path | None


def bake_html(
    html: str,
    exchanges: Sequence[Exchange],
    *,
    outcome_html: str = "",
    dispositions: Mapping[str, str] | None = None,
    swap_to_strict: bool = True,
) -> tuple[str, bool]:
    """Fold the close-time record into ``html`` and (by default) swap to the strict CSP.

    The record is the Review outcome (``outcome_html``, may be empty) followed by the
    Q&A log, injected together at the seam so re-baking stays idempotent. When
    ``dispositions`` is given, each claim's state is also stamped onto its own
    ``<details>`` tag (:func:`bake_dispositions_html`) so the saved page shows the
    tints without script. Pure string→string: the I/O lives in :func:`bake_review`.
    Returns the baked HTML and whether the CSP was swapped.
    """
    if dispositions is not None:
        html = bake_dispositions_html(html, dispositions)
    html = inject_qa(html, outcome_html + render_qa_html(exchanges))
    csp_swapped = False
    if swap_to_strict:
        html, csp_swapped = swap_csp(html)
    return html, csp_swapped


# --- Markdown export --------------------------------------------------------

_BACKTICK_RUN = re.compile(r"`+")


def _fenced(text: str, *, lang: str = "") -> str:
    """Wrap ``text`` in a fenced code block whose fence outlives any backtick run inside.

    A reviewer-annotated line or a snippet of code may itself contain a run of backticks;
    GitHub closes a fence at the first run of *at least* as many backticks, so the fence is
    sized one longer than the longest run in the body. This keeps untrusted content from
    breaking out of its block in a Markdown paste.
    """
    longest = max((len(run) for run in _BACKTICK_RUN.findall(text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{lang}\n{text}\n{fence}"


def _inline(text: str) -> str:
    """Collapse all whitespace to single spaces so untrusted prose is safe inline Markdown.

    A reviewer's question is prose, so it belongs inline in the ``### Q`` heading rather
    than in a code fence — but a raw newline in it would end the heading and let the
    remainder reshape the pasted PR body as new blocks (headings, lists, rules). Collapsing
    every whitespace run to a single space removes that structural-injection vector; any
    remaining Markdown punctuation is inline-only (it cannot open a new block mid-line) and
    GitHub sanitises raw HTML in a rendered paste.
    """
    return " ".join(text.split())


def _format_claim_block(claim: Mapping[str, object], disposition: str | None = None) -> str:
    """One claim as a ``### [kind] summary`` heading with its badges and questions.

    A reviewer disposition, when set, joins the badges as ``reviewer: …`` — the
    per-claim state stays attributed to the human, beside the agent's confidence.
    ``verify`` claims are the export's checklist (they replaced the old
    ``test_checklist.items``), so they render as **real task-list items** — the
    ``- [ ]`` marker must sit at list level; inside a ``###`` heading GitHub
    renders it as literal heading text, not a checkbox — and the box is checked
    exactly when the reviewer set ``verified``, so the pasted checklist reflects
    real verification state, never the claim's mere existence.
    """
    kind = str(claim.get("kind", ""))
    badges = [f"confidence: {claim.get('confidence', '')}"]
    if claim.get("category"):
        badges.append(str(claim["category"]))
    if claim.get("level"):
        badges.append(f"level: {claim['level']}")
    if disposition:
        badges.append(f"reviewer: {disposition}")
    label = f"[{kind}] {claim.get('summary', '')} ({'; '.join(badges)})"
    detail = str(claim.get("detail", ""))
    questions = claim.get("challenge_questions")

    if kind == "verify":
        box = "x" if disposition == "verified" else " "
        lines = [f"- [{box}] {label}"]
        if detail:
            lines.append(f"  {detail}")  # indented: stays inside the task item
        if isinstance(questions, list):
            lines.extend(f"  - {q}" for q in questions)
        return "\n".join(lines)

    bullets = "\n".join(f"- {q}" for q in questions) if isinstance(questions, list) else ""
    return "\n".join((f"### {label}", "", detail, "", bullets)).rstrip()


def _format_thread(thread: Mapping[str, object], dispositions: Mapping[str, str]) -> str:
    """One thread: its summary then every claim block (ADR-0009's L1/L2 in Markdown)."""
    parts = [str(thread.get("summary", ""))]
    claims = thread.get("claims")
    if isinstance(claims, list):
        parts += [
            _format_claim_block(c, dispositions.get(str(c.get("id", ""))))
            for c in claims
            if isinstance(c, Mapping)
        ]
    return "\n\n".join(part for part in parts if part.strip())


def render_qa_markdown(exchanges: Sequence[Exchange]) -> str:
    """Render the transcript as a Markdown ``## Q&A Log`` section."""
    lines: list[str] = ["## Q&A Log", ""]
    if not exchanges:
        lines.append("_No questions were asked during this review._")
        lines.append("")
        return "\n".join(lines)
    for exchange in exchanges:
        for prompt in exchange.prompts:
            lines.append(f"### Q{exchange.seq}. {_inline(prompt.prompt)}")
            lines.append("")
            if prompt.is_annotation and prompt.text:
                lines.append(_fenced(prompt.text))
                lines.append("")
        lines.append("**A:**")
        lines.append("")
        lines.append(exchange.answer.rstrip("\n"))
        lines.append("")
    return "\n".join(lines)


def _md_section(title: str, body: str) -> list[str]:
    """A ``## title`` block followed by ``body`` (skipped entirely when body is empty)."""
    if not body.strip():
        return []
    return [f"## {title}", "", body, ""]


def _drive_by_threads(analysis: Mapping[str, object]) -> set[str]:
    """The thread ids ``alignment`` flags as drive-bys (empty when no stated goal)."""
    alignment = analysis.get("alignment")
    if not isinstance(alignment, Mapping):
        return set()
    raw = alignment.get("drive_by")
    if not isinstance(raw, list):
        return set()
    return {t for t in raw if isinstance(t, str)}


def _alignment_line(analysis: Mapping[str, object]) -> str:
    """One goal-alignment sentence for the Orientation (ADR-0010), or ``""``.

    Rendered only when ``alignment`` is an object — a stated goal existed and the
    threads were measured against it. Goal-unserved work needs no line here: it is
    ``omission`` claims (kind ``goal``) that render with their threads.
    """
    alignment = analysis.get("alignment")
    if not isinstance(alignment, Mapping):
        return ""
    serves = alignment.get("serves_goal")
    drive_by = alignment.get("drive_by")
    bits: list[str] = []
    if isinstance(serves, list) and serves:
        bits.append(f"serving the stated goal: {', '.join(str(t) for t in serves)}")
    if isinstance(drive_by, list) and drive_by:
        bits.append(f"drive-by (unrelated to the goal): {', '.join(str(t) for t in drive_by)}")
    if not bits:
        return ""
    return f"Goal alignment — {'; '.join(bits)}."


def _outcome_markdown(
    analysis: Mapping[str, object] | None, dispositions: Mapping[str, str]
) -> str:
    """The Review outcome as Markdown — the reviewer's account, for a PR paste."""
    analysis = analysis or {}
    rows = _claims_by_disposition(analysis, dispositions)
    if not rows:
        return ""
    lines = [f"Reviewer dispositions — {_outcome_counts_line(rows)}.", ""]
    totals = progress(analysis, dispositions)
    if totals:
        titles = _thread_titles(analysis)
        bits = []
        for tid, reviewed, total, concerns in totals:
            name = f"{tid} ({_inline(titles[tid])})" if titles.get(tid) else tid
            bits.append(f"{name} {_progress_text(reviewed, total, concerns)}")
        lines += [f"Per thread: {'; '.join(bits)}.", ""]
    lines += [
        f"- **{disposition}** — {cid}: {_inline(summary)}" for disposition, cid, summary in rows
    ]
    lines += ["", f"_{_OUTCOME_NOTE}_"]
    return "\n".join(lines)


def build_markdown(
    analysis: Mapping[str, object] | None,
    exchanges: Sequence[Exchange],
    dispositions: Mapping[str, str] | None = None,
) -> str:
    """Build ``review.md`` from the Analysis, the dispositions, and the Q&A.

    Renders the reviewer-facing Analysis (the L0 orientation with its goal-alignment
    line, then each thread with its claims — verify claims as checkboxes checked by
    the reviewer's ``verified``, per-claim dispositions as ``reviewer:`` badges,
    drive-by threads flagged in their headings), the Review outcome (the
    **reviewer's** dispositions with per-thread totals, ADR-0012 — omitted when
    ``dispositions`` is ``None``), and the Q&A Log. ``analysis`` may be ``None``
    (no ``analysis.json``) — the export then carries the Q&A alone rather than
    failing. The Analysis is the agent's own trusted prose; reviewer-originated
    text in the Q&A goes through :func:`_fenced` so it cannot break the Markdown.
    """
    analysis = analysis or {}
    claim_dispositions = dispositions or {}
    title = str(analysis.get("title") or "Branch Review")
    out: list[str] = [f"# {title}", ""]

    orientation_parts = [str(analysis.get("intent_summary") or ""), _alignment_line(analysis)]
    out += _md_section("Orientation", "\n\n".join(p for p in orientation_parts if p.strip()))

    if dispositions is not None:
        out += _md_section("Review outcome", _outcome_markdown(analysis, dispositions))

    drive_by = _drive_by_threads(analysis)
    threads = analysis.get("threads")
    if isinstance(threads, list):
        for thread in threads:
            if not isinstance(thread, Mapping):
                continue
            heading = f"{thread.get('id', '')} — {thread.get('title', '')}".strip(" —")
            if thread.get("id") in drive_by:
                heading += " (drive-by)"
            out += _md_section(heading, _format_thread(thread, claim_dispositions))

    runner_block = analysis.get("test_runner")
    if isinstance(runner_block, Mapping):
        runner = str(runner_block.get("command") or runner_block.get("runner") or "")
        if runner:
            out += _md_section(
                "Test runner",
                f"Suggested runner (not run by the review): `{runner}`",
            )

    out.append(render_qa_markdown(exchanges))
    return "\n".join(out).rstrip("\n") + "\n"


# --- Orchestration + CLI ----------------------------------------------------


def _load_analysis(path: Path) -> Mapping[str, object] | None:
    """Load ``analysis.json`` for the Markdown export; ``None`` if absent or unreadable.

    A missing or malformed Analysis must not abort the bake — the cockpit HTML is the
    primary artifact and is independent of it — so this degrades to ``None`` (Q&A-only
    Markdown) rather than raising.
    """
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def bake_review(
    cockpit: Path,
    *,
    qa_path: Path | None = None,
    analysis_path: Path | None = None,
    markdown_path: Path | None = None,
    swap_to_strict: bool = True,
) -> BakeResult:
    """Fold ``qa.jsonl`` into the cockpit on disk and optionally write ``review.md``.

    The cockpit is read, baked, and written back in place; the Q&A seam makes that
    idempotent. The baked record is the Review outcome (the reviewer's dispositions,
    read from ``dispositions.json`` beside the cockpit — ADR-0012) followed by the
    Q&A log with disposition updates filtered out (they are state, not
    conversation). When ``markdown_path`` is given, the Markdown export is built
    from ``analysis.json`` (default beside the cockpit) plus the same record and
    written there. All paths default under the cockpit's own directory.
    """
    out_dir = cockpit.parent
    qa_path = qa_path or out_dir / QA_NAME
    analysis_path = analysis_path or out_dir / ANALYSIS_NAME

    analysis = _load_analysis(analysis_path)
    dispositions = load_dispositions(out_dir / DISPOSITIONS_NAME)
    exchanges = _without_disposition_prompts(load_exchanges(qa_path))
    html = cockpit.read_text(encoding="utf-8")
    baked, csp_swapped = bake_html(
        html,
        exchanges,
        outcome_html=render_outcome_html(analysis, dispositions),
        dispositions=dispositions,
        swap_to_strict=swap_to_strict,
    )
    cockpit.write_text(baked, encoding="utf-8")

    if markdown_path is not None:
        markdown = build_markdown(analysis, exchanges, dispositions)
        markdown_path.write_text(markdown, encoding="utf-8")

    return BakeResult(
        exchanges=len(exchanges),
        prompts=sum(len(e.prompts) for e in exchanges),
        csp_swapped=csp_swapped,
        markdown_path=markdown_path,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the skill's ``bake_review.py`` (run at ``/review-close``)."""
    parser = argparse.ArgumentParser(
        prog="bake_review",
        description="Fold qa.jsonl into review.html (self-contained); optionally emit review.md.",
    )
    parser.add_argument(
        "cockpit", nargs="?", type=Path, default=DEFAULT_COCKPIT, help="Cockpit review.html."
    )
    parser.add_argument("--qa", type=Path, default=None, help="qa.jsonl (default: beside cockpit).")
    parser.add_argument(
        "--analysis", type=Path, default=None, help="analysis.json (default: beside cockpit)."
    )
    parser.add_argument(
        "--md",
        nargs="?",
        const="",  # bare --md → review.md beside the cockpit; --md PATH → that path
        default=None,  # --md absent → no Markdown export
        help="Also write a Markdown export (default path: <cockpit dir>/review.md).",
    )
    parser.add_argument(
        "--no-csp-swap",
        action="store_true",
        help="Keep the cockpit's existing CSP instead of swapping to the strict policy.",
    )
    args = parser.parse_args(argv)

    if not args.cockpit.is_file():
        print(f"error: cockpit not found: {args.cockpit}", file=sys.stderr)
        return 2

    markdown_path: Path | None = None
    if args.md is not None:
        markdown_path = Path(args.md) if args.md else args.cockpit.parent / DEFAULT_MD_NAME

    result = bake_review(
        args.cockpit,
        qa_path=args.qa,
        analysis_path=args.analysis,
        markdown_path=markdown_path,
        swap_to_strict=not args.no_csp_swap,
    )

    print(
        f"Baked {result.exchanges} exchange(s) / {result.prompts} prompt(s) into {args.cockpit}"
        + ("; CSP set to strict" if result.csp_swapped else "")
    )
    if result.markdown_path is not None:
        print(f"Wrote Markdown export to {result.markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
