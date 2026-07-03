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
import csv
import io
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from branch_review.escape import STRICT_CSP, escape_text, fragment
from branch_review.feedback import DEFAULT_COCKPIT, QA_NAME

# Files the bake reads and writes, all under the cockpit's own (gitignored) dir.
# ``DEFAULT_COCKPIT`` (the cockpit path) and ``QA_NAME`` (the transcript) are the loop's
# contract, owned by :mod:`branch_review.feedback`; we consume them rather than redefine.
ANALYSIS_NAME = "analysis.json"  # the structured Analysis (for the Markdown export)
DEFAULT_MD_NAME = "review.md"  # the optional Markdown export

# The seam the bake fills. The agent authors an empty placeholder
# ``<!--brc:qa-log--><!--/brc:qa-log-->`` after the Test Checklist (SKILL.md §5); the
# bake replaces everything between the markers, so re-baking is idempotent. These are
# HTML comments — invisible in the browser — and are distinct from the Escape
# Boundary's ``brc:untrusted`` markers, so they never perturb the linter's balance count.
QA_SEAM_OPEN = "<!--brc:qa-log-->"
QA_SEAM_CLOSE = "<!--/brc:qa-log-->"


# --- The bounded prompt extractor (ADR-0007) --------------------------------


# Lavish emits queued feedback as one TOON tabular array: a header line declaring the
# field order, then one indented data row per prompt. Anchored at column 0 so it can
# never match the ``prompts[N]`` literals that appear *inside* the quoted
# ``dom_snapshot`` scalar (which are indented/quoted), and bounded by the declared
# count so the row scan stops before the next top-level key (e.g. ``next_step:``).
_PROMPTS_HEADER = re.compile(r"^prompts\[(\d+)\]\{([^}]*)\}:[ \t]*$", re.MULTILINE)


@dataclass(frozen=True)
class Prompt:
    """One queued reviewer prompt, lifted from the poll TOON's ``prompts[]`` block.

    ``prompt`` is the reviewer's question. For a free-form chat message ``tag`` is
    ``message`` and there is no anchored element; for an annotation ``tag`` is the
    element's tag (``span``/``li``/…) and ``selector``/``text`` locate and quote the
    annotated element or diff line. Every field is reviewer-originated untrusted data —
    it crosses the Escape Boundary before rendering.
    """

    uid: str
    prompt: str
    selector: str
    tag: str
    text: str

    @property
    def is_annotation(self) -> bool:
        """True when the prompt is anchored to a page element rather than free-form.

        A free-form message (``tag == "message"``) has no meaningful ``selector``/``text``
        to show; an annotation does, so the renderer shows its anchored line for context.
        """
        return self.tag not in ("", "message")


def _split_toon_row(row: str) -> list[str]:
    """Split one TOON data row into its fields, honouring TOON's quoting.

    TOON quotes a field with double quotes and escapes an inner quote with a backslash
    (``\\"``), not by doubling it — so the CSV reader runs with ``doublequote=False`` and
    ``escapechar='\\'``. Unquoted fields (a question with no comma) pass through verbatim.
    """
    reader = csv.reader(
        io.StringIO(row),
        quotechar='"',
        doublequote=False,
        escapechar="\\",
    )
    try:
        return next(reader)
    except StopIteration:
        return []


def extract_prompts(toon: str) -> list[Prompt]:
    """Lift the reviewer prompts from one poll's raw TOON — the bounded extractor (ADR-0007).

    Finds the single column-0 ``prompts[N]{fields}:`` header, reads the field order from
    its ``{…}``, and parses exactly ``N`` following indented rows into :class:`Prompt`
    records. Returns ``[]`` when there is no prompts block (a ``waiting``/``ended`` poll)
    or the header is malformed — a missing question degrades to an empty log, never a
    crash. This is the *only* place anything is read out of the stored TOON, and it reads
    nothing but this one block.
    """
    header = _PROMPTS_HEADER.search(toon)
    if header is None:
        return []
    count = int(header.group(1))
    fields = [name.strip() for name in header.group(2).split(",")]

    prompts: list[Prompt] = []
    for line in toon[header.end() :].splitlines():
        if len(prompts) >= count:
            break
        if line.startswith((" ", "\t")):
            if not line.strip():
                continue
            values = _split_toon_row(line.strip())
            record = dict(zip(fields, values, strict=False))
            prompts.append(
                Prompt(
                    uid=record.get("uid", ""),
                    prompt=record.get("prompt", ""),
                    selector=record.get("selector", ""),
                    tag=record.get("tag", ""),
                    text=record.get("text", ""),
                )
            )
        elif line.strip():
            break  # a non-indented, non-blank line is the next top-level key — block ended
    return prompts


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
    html: str, exchanges: Sequence[Exchange], *, swap_to_strict: bool = True
) -> tuple[str, bool]:
    """Fold the exchanges into ``html`` and (by default) swap to the strict CSP.

    Pure string→string: the I/O lives in :func:`bake_review`. Returns the baked HTML and
    whether the CSP was swapped.
    """
    html = inject_qa(html, render_qa_html(exchanges))
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


def _format_claim_block(claim: Mapping[str, object]) -> str:
    """One claim as a ``### [kind] summary`` heading with its badges and questions.

    ``verify`` claims are the export's checklist (they replaced the old
    ``test_checklist.items``), so they render as **real task-list items** — the
    ``- [ ]`` marker must sit at list level; inside a ``###`` heading GitHub
    renders it as literal heading text, not a checkbox.
    """
    kind = str(claim.get("kind", ""))
    badges = [f"confidence: {claim.get('confidence', '')}"]
    if claim.get("category"):
        badges.append(str(claim["category"]))
    if claim.get("level"):
        badges.append(f"level: {claim['level']}")
    label = f"[{kind}] {claim.get('summary', '')} ({'; '.join(badges)})"
    detail = str(claim.get("detail", ""))
    questions = claim.get("challenge_questions")

    if kind == "verify":
        lines = [f"- [ ] {label}"]
        if detail:
            lines.append(f"  {detail}")  # indented: stays inside the task item
        if isinstance(questions, list):
            lines.extend(f"  - {q}" for q in questions)
        return "\n".join(lines)

    bullets = "\n".join(f"- {q}" for q in questions) if isinstance(questions, list) else ""
    return "\n".join((f"### {label}", "", detail, "", bullets)).rstrip()


def _format_thread(thread: Mapping[str, object]) -> str:
    """One thread: its summary then every claim block (ADR-0009's L1/L2 in Markdown)."""
    parts = [str(thread.get("summary", ""))]
    claims = thread.get("claims")
    if isinstance(claims, list):
        parts += [_format_claim_block(c) for c in claims if isinstance(c, Mapping)]
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


def build_markdown(analysis: Mapping[str, object] | None, exchanges: Sequence[Exchange]) -> str:
    """Build ``review.md`` from the Analysis and the Q&A — for pasting into a PR.

    Renders the reviewer-facing Analysis (the L0 orientation, then each thread with
    its claims — verify claims as checkboxes) followed by the Q&A Log. ``analysis``
    may be ``None`` (no ``analysis.json``) — the export then carries the Q&A alone rather
    than failing. The Analysis is the agent's own trusted prose; reviewer-originated text
    in the Q&A goes through :func:`_fenced` so it cannot break the Markdown.
    """
    analysis = analysis or {}
    title = str(analysis.get("title") or "Branch Review")
    out: list[str] = [f"# {title}", ""]

    out += _md_section("Orientation", str(analysis.get("intent_summary") or ""))

    threads = analysis.get("threads")
    if isinstance(threads, list):
        for thread in threads:
            if not isinstance(thread, Mapping):
                continue
            heading = f"{thread.get('id', '')} — {thread.get('title', '')}".strip(" —")
            out += _md_section(heading, _format_thread(thread))

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
    idempotent. When ``markdown_path`` is given, the Markdown export is built from
    ``analysis.json`` (default beside the cockpit) plus the same exchanges and written
    there. All paths default under the cockpit's own directory.
    """
    out_dir = cockpit.parent
    qa_path = qa_path or out_dir / QA_NAME
    analysis_path = analysis_path or out_dir / ANALYSIS_NAME

    exchanges = load_exchanges(qa_path)
    html = cockpit.read_text(encoding="utf-8")
    baked, csp_swapped = bake_html(html, exchanges, swap_to_strict=swap_to_strict)
    cockpit.write_text(baked, encoding="utf-8")

    if markdown_path is not None:
        markdown = build_markdown(_load_analysis(analysis_path), exchanges)
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
