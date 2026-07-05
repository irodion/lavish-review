"""The deterministic Escape Boundary — untrusted data crosses here, escaped (ADR-0002).

The agent authors the cockpit's frame and prose but **never hand-interpolates
untrusted strings**. Diff bodies, file paths, commit messages, and branch names
are attacker-influenceable: a hostile branch can hide ``<script>`` in a filename,
a hunk, or a commit subject. This module is the single chokepoint that turns any
such string into a safe HTML fragment the agent injects verbatim at a fixed seam.

Two things make the boundary mechanical rather than a matter of agent discretion:

1. Every untrusted value is run through :func:`escape_text` (stdlib ``html.escape``
   with ``quote=True``) — ``<``, ``>``, ``&``, and both quote styles become
   entities, so the value can only ever render as visible characters.
2. Every escaped value is wrapped in sentinel markers (:data:`UNTRUSTED_OPEN` /
   :data:`UNTRUSTED_CLOSE`). These are HTML comments — invisible in the browser
   and excluded from ``element.textContent``, so they never reach ``app.js`` or
   the rendered page — and they let the Cockpit Linter (:mod:`branch_review.lint`)
   locate every untrusted region in the raw source and fail the build if a literal
   ``<`` or ``>`` ever survives inside one.

See ``DESIGN.md``, ``CONTEXT.md`` (Review Cockpit), and
``docs/adr/0002-deterministic-escape-boundary.md``.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from html import escape as _html_escape

# Sentinel markers delimiting one untrusted region in the raw HTML source.
# HTML comments by construction: they render nothing and are absent from
# ``element.textContent`` (only Text-node descendants count), so they never leak
# into the page or into app.js's diff colourizer. The linter pairs them up in the
# raw source and asserts the bytes between them carry no literal ``<``/``>``.
UNTRUSTED_OPEN = "<!--brc:untrusted-->"
UNTRUSTED_CLOSE = "<!--/brc:untrusted-->"

# Structural seams the cockpit author pre-plants empty for a later mechanical fill:
# the Q&A log the bake folds in (issue #9) and each claim's live evidence (issue #43).
# Same ``brc:`` HTML-comment family as the untrusted markers above — invisible in the
# browser and, being comments, they never perturb the linter's untrusted-marker
# balance count. They live here in the escape leaf so their producers (:mod:`bake`,
# :mod:`evidence`) and the linter that checks they were planted (:mod:`lint`) share
# one definition instead of duplicating it — escape imports nothing, so no cycle.
QA_SEAM_OPEN = "<!--brc:qa-log-->"
QA_SEAM_CLOSE = "<!--/brc:qa-log-->"


def evidence_seam_markers(claim_id: str) -> tuple[str, str]:
    """The raw open/close markers of one claim's live-evidence seam.

    Just the string format — callers that write a seam validate the claim id first
    (:func:`branch_review.evidence.evidence_seam`); the linter only checks presence,
    so it builds the markers straight from the analysis's claim ids.
    """
    return f"<!--brc:evidence:{claim_id}-->", f"<!--/brc:evidence:{claim_id}-->"


# The strict Content-Security-Policy the vendored cockpit must ship — the
# defense-in-depth twin of the escaping above (ADR-0002). `script-src 'self'`
# with no `'unsafe-inline'` forbids inline JS and forces all behaviour into the
# vendored app.js; `default-src 'none'` denies every fetch the cockpit doesn't
# explicitly re-allow. This is the single source the SKILL guidance, the cockpit,
# and the Cockpit Linter's tests share, so the policy lives in one place.
STRICT_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "font-src 'self'; base-uri 'none'; form-action 'none'"
)

# The CDN origin Lavish-AXI loads its editor stack (Tailwind + DaisyUI) from when it
# injects its interactive client into a served page.
LAVISH_CDN = "https://cdn.jsdelivr.net"

# The relaxed policy for a cockpit opened **through Lavish-AXI**, not via ``file://``.
#
# Lavish serves the cockpit and injects its annotation/editor UI into the page —
# a CDN Tailwind runtime + DaisyUI stylesheet, an inline ``<style
# type="text/tailwindcss">``, and an inline ``<script type="module">`` bootstrap —
# without reconciling with the page's own CSP. Under :data:`STRICT_CSP` every one of
# those injections is blocked, so the annotation UI renders unstyled and unusable
# (issue: strict CSP vs. the Lavish interactive layer; see
# ``docs/adr/0004-interactive-csp.md``). This policy trusts ``'self'`` plus the
# Lavish CDN and permits inline script/style so that UI can run.
#
# This is a **defense-in-depth reduction, not a hole**: the primary XSS control is
# the deterministic entity-escaping at the Escape Boundary (ADR-0002), which is
# CSP-independent — untrusted diff bytes are already entities and cannot execute
# under any policy. Relaxing the CSP is acceptable *only* because this context is
# local and loopback-only (the cockpit is served from 127.0.0.1 by a tool the user
# launched). The portable ``file://`` artifact keeps :data:`STRICT_CSP`. The
# relaxation stays **bounded**: ``default-src 'none'`` still denies everything not
# named, ``base-uri``/``form-action`` stay locked, and script/style are widened only
# to ``'self'`` + the Lavish CDN + inline/eval — not to an open wildcard.
INTERACTIVE_CSP = (
    "default-src 'none'; "
    f"script-src 'self' 'unsafe-inline' 'unsafe-eval' {LAVISH_CDN}; "
    f"style-src 'self' 'unsafe-inline' {LAVISH_CDN}; "
    f"img-src 'self' data: {LAVISH_CDN}; "
    f"font-src 'self' data: {LAVISH_CDN}; "
    f"connect-src 'self' {LAVISH_CDN}; "
    "worker-src 'self' blob:; "
    "base-uri 'none'; form-action 'none'"
)

# Shown in place of an untrusted region when there is genuinely nothing to escape.
# A trusted literal, so it carries no markers.
_EMPTY_DIFF = "(no changes in this range)"


def escape_text(value: str) -> str:
    """The one escaping primitive: HTML-escape untrusted text, quotes included.

    ``quote=True`` matters because a fragment may be injected into an attribute
    context as well as element text; escaping ``"`` and ``'`` keeps it safe in both.
    """
    return _html_escape(value, quote=True)


def fragment(value: str) -> str:
    """Wrap one untrusted value as an escaped, marker-delimited inline fragment.

    The result is safe to drop anywhere the agent needs the value as text. The
    markers are invisible and exist only so the linter can prove the region holds
    no unescaped markup.
    """
    return f"{UNTRUSTED_OPEN}{escape_text(value)}{UNTRUSTED_CLOSE}"


def diff_fragment(diff_text: str) -> str:
    """Render the unified diff as a safe ``<pre class="diff">`` fragment.

    The ``<pre>`` shell is trusted frame the boundary owns; only the diff body —
    the attacker-controlled part — is escaped and marker-wrapped inside it.
    """
    body = fragment(diff_text) if diff_text else _EMPTY_DIFF
    return f'<pre class="diff">{body}</pre>\n'


def notice_fragment(message: str) -> str:
    """A trusted notice rendered in the whole diff's place (e.g. the total-diff fallback).

    Reuses the ``<pre class="diff">`` shell so the cockpit's Diff section styles it
    like a diff block, but the body is a tool-authored message — not attacker data —
    so it carries no untrusted markers. Escaped through :func:`escape_text` anyway
    for uniformity; the message is plain text by construction.
    """
    return f'<pre class="diff">{escape_text(message)}</pre>\n'


def _file_line(record: Mapping[str, str]) -> str:
    """One ``<li>`` for a changed file; status and path(s) both cross the boundary.

    The git status flag (``A``/``M``/``R100``…) is not attacker-controlled, but it
    is escaped through :func:`escape_text` anyway: the boundary is unconditional by
    design (ADR-0002), never a per-value judgement call.
    """
    status = escape_text(record.get("status", ""))
    path = fragment(record.get("path", ""))
    old_path = record.get("old_path")
    if old_path is not None:
        # Rename/copy: show ``old → new``, both escaped; the arrow is trusted frame.
        path = f"{fragment(old_path)} &rarr; {path}"
    return f'  <li><span class="status">{status}</span> {path}</li>'


def goal_fragment(goal: Mapping[str, str] | None) -> str:
    """The L0 goal block: the stated goal, escaped and attributed — or the degraded line.

    Goal Evidence (ADR-0010) is untrusted: issue bodies, commit messages, and any
    provenance string embedding a branch name are attacker-writable, so both cross
    the boundary. With no goal there is nothing untrusted to show — the degraded
    notice is a fixed trusted literal, and an *inferred* intent is never dressed up
    as a stated goal.
    """
    if goal is None:
        return '<p class="goal-missing">No stated goal found; intent inferred from the diff.</p>'
    text = f'<blockquote class="goal-text">{fragment(goal.get("text", ""))}</blockquote>'
    provenance = (
        f'<p class="goal-provenance">Stated goal — {fragment(goal.get("provenance", ""))}</p>'
    )
    return f"{text}\n{provenance}"


def build_fragments(
    *,
    branch: str,
    base: str,
    head_sha: str,
    changed_file_count: int,
    files: Iterable[Mapping[str, str]],
    commit_lines: Iterable[str],
    goal: Mapping[str, str] | None = None,
) -> str:
    """Build ``fragments.html`` — the pre-escaped building blocks for the cockpit.

    Each section is delimited by a ``<!-- fragment: NAME -->`` guide comment so the
    agent can find and paste the blocks it needs into the frame it authors. Every
    untrusted value (branch, base, paths, commit subjects, the goal text) is already
    escaped and marker-wrapped, so the agent injects these verbatim and never touches
    a raw untrusted string. ``head_sha`` and the count are git-plumbing values
    (hex/int), rendered through :func:`escape_text` anyway for uniformity. ``goal``
    is the resolved Goal Evidence block (or ``None`` — the fragment then carries the
    degraded no-goal notice, ADR-0010).
    """
    files = list(files)
    commit_lines = list(commit_lines)
    # Branch appears in both the title and the meta block; escape it once.
    branch_frag = fragment(branch)
    parts: list[str] = []

    parts.append("<!-- fragment: title -->")
    parts.append(f'<h1 class="cockpit-title">{branch_frag}</h1>')

    parts.append("<!-- fragment: meta -->")
    parts.append('<dl class="cockpit-meta">')
    parts.append(f"  <dt>Base</dt><dd>{fragment(base)}</dd>")
    parts.append(f"  <dt>Branch</dt><dd>{branch_frag}</dd>")
    parts.append(f"  <dt>Head</dt><dd><code>{escape_text(head_sha[:12])}</code></dd>")
    parts.append(f"  <dt>Files changed</dt><dd>{int(changed_file_count)}</dd>")
    parts.append("</dl>")

    parts.append("<!-- fragment: goal -->")
    parts.append(goal_fragment(goal))

    parts.append("<!-- fragment: files -->")
    if files:
        parts.append('<ul class="changed-files">')
        parts.extend(_file_line(record) for record in files)
        parts.append("</ul>")
    else:
        parts.append('<p class="changed-files-empty">No files changed in this range.</p>')

    parts.append("<!-- fragment: commits -->")
    if commit_lines:
        parts.append('<ul class="commits">')
        parts.extend(f"  <li>{fragment(line)}</li>" for line in commit_lines)
        parts.append("</ul>")
    else:
        parts.append('<p class="commits-empty">No commits in this range.</p>')

    return "\n".join(parts) + "\n"


# --- Per-file diff fragments (File Walkthrough substrate, issue #21) ----------
#
# The whole-diff ``diff_fragment`` above is one big ``<pre>``; the File Walkthrough
# and Review Route (issue #6) instead interleave the agent's per-file prose with
# *that file's* diff, in route order. To keep ADR-0002 intact, the split happens on
# **our** side of the boundary: the collector escapes each file's hunk separately
# through :func:`diff_fragment`, so a ``<script>`` in one file renders as text and
# the linter's per-region guarantee holds for every fragment independently. The
# agent still never hand-pastes raw diff — it injects these per-file fragments.

# Sub-directory under ``.review-agent/`` that holds the per-file escaped fragments.
FRAGMENTS_DIRNAME = "fragments"


def file_fragment_id(path: str) -> str:
    """A stable, traversal-safe, collision-free id for a changed file's fragment.

    The id is a hex SHA-1 prefix of the UTF-8 path, so by construction it contains
    only ``[0-9a-f]`` — never ``/``, ``..``, a leading dot, a space, or any byte
    that could escape the ``fragments/`` directory or collide on a
    case-insensitive filesystem, no matter how hostile or unusual the path. The
    hash is an identifier, not a security primitive (``usedforsecurity=False``);
    64 bits of prefix make a collision between two distinct paths effectively
    impossible for any real changeset, and the collector asserts uniqueness anyway.
    """
    digest = hashlib.sha1(path.encode("utf-8"), usedforsecurity=False).hexdigest()
    return digest[:16]


# --- Hunk anchors (Deck Mode, ADR-0014, issue #63) ----------------------------
#
# A per-file fragment used to be one big ``<pre class="diff">``; the Deck Mode Stage
# wants to land a claim's evidence on the *exact hunk* that substantiates it, not the
# whole file. So the boundary now emits a deterministic per-hunk id inside each
# fragment and records a hunk index in the fragments manifest — the **Hunk Anchorer**.
# The split happens on our side of the boundary (each hunk's body still crosses
# :func:`fragment`), so ADR-0002 holds per hunk exactly as it held per file, and a
# ``<script>`` hidden in a hunk still renders as text. Evidence refs may address a hunk
# (``{path, hunk}``, analysis schema 0.3); the anchor is read from the manifest and
# never hand-typed, and the browser's native ``#anchor`` scroll completes the deep link.

# A unified-diff hunk header — a line beginning ``@@`` (multiline ``^`` matches at
# string start and after every ``\n``, git's own line model). Body lines are prefixed
# with a space/``+``/``-`` (or ``\`` for the no-newline marker), so a bare ``@@`` at a
# line start is always a header, never content. Anchored on ``\n`` alone — never
# ``str.splitlines`` — so an embedded ``\r`` inside a hunk body can't forge a split.
_HUNK_HEADER_RE = re.compile(r"(?m)^@@")


def hunk_anchor_id(fragment_id: str, index: int) -> str:
    """The deterministic element id of the ``index``-th hunk (1-based) in a file's diff.

    ``fragment_id`` is the file's :func:`file_fragment_id` (16 hex chars) and ``index``
    is the hunk's 1-based position in that file's hunk sequence, so ``hunk-<fid>-<n>`` is
    unique per (file, hunk) across the whole document and — being ``[0-9a-f-]`` only — is
    safe both as an HTML ``id`` and as a URL ``#fragment``. The cockpit links a
    ``{path, hunk}`` evidence ref to this id straight from the manifest (never
    hand-typed), and the browser's native anchor scroll lands the reviewer on the hunk.
    """
    return f"hunk-{fragment_id}-{index}"


def file_diff_fragment(
    diff_text: str, fragment_id: str
) -> tuple[str, list[dict[str, object]]]:
    """Render one changed file's diff as anchored per-hunk blocks + its hunk index.

    Splits the file's unified diff at each hunk header so every hunk becomes an
    individually anchored ``<section class="hunk" id=…>`` wrapping its own escaped
    ``<pre class="diff">``; the header preamble (the ``diff --git`` / ``---`` / ``+++``
    lines, or a pure rename's ``rename from/to``) leads as a ``diff-preamble`` block.
    The bytes are **sliced from** ``diff_text`` verbatim — preamble and every hunk
    concatenate back to the original — so the reviewed change is shown byte-for-byte and
    each hunk body still crosses the boundary through :func:`fragment` (ADR-0002 holds
    per hunk).

    Returns ``(html, hunks)`` where each ``hunks`` entry is
    ``{index, anchor, header_html}`` — the 1-based index, the :func:`hunk_anchor_id`
    element id, and the ``@@`` header line **crossed through the boundary** (escaped,
    marker-wrapped) — the per-file hunk index the manifest carries and the cockpit
    links evidence to. A diff with no hunk (a pure rename or a mode-only change) yields
    just its preamble and an empty index; an empty ``diff_text`` (never written for an
    omitted body) degrades to the trusted empty notice with no hunks.
    """
    if not diff_text:
        return diff_fragment(diff_text), []

    starts = [match.start() for match in _HUNK_HEADER_RE.finditer(diff_text)]
    parts: list[str] = ['<div class="file-diff">']
    hunks: list[dict[str, object]] = []

    # Everything before the first hunk header is the preamble — the whole diff when
    # there is no hunk (a pure rename / mode change), and possibly empty when the diff
    # opens straight on a hunk. Each hunk then runs to the next header or to EOF.
    preamble = diff_text[: starts[0]] if starts else diff_text
    if preamble:
        parts.append(f'<pre class="diff diff-preamble">{fragment(preamble)}</pre>')

    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(diff_text)
        hunk_text = diff_text[start:end]
        index = i + 1
        anchor = hunk_anchor_id(fragment_id, index)
        # The ``@@ -a,b +c,d @@ <section>`` header, crossed through the boundary. Its
        # trailing context is git's function heading, lifted from (attacker-influenceable)
        # source, so it is untrusted — escaped + marker-wrapped exactly like ``path_html``
        # so an author who labels a hunk card has a safe-by-construction value. The raw
        # line is never handed out (the escaped ``<pre>`` body already shows it verbatim).
        header_html = fragment(hunk_text.split("\n", 1)[0])
        hunks.append({"index": index, "anchor": anchor, "header_html": header_html})
        parts.append(
            f'<section class="hunk" id="{anchor}">'
            f'<pre class="diff">{fragment(hunk_text)}</pre>'
            "</section>"
        )

    parts.append("</div>")
    return "\n".join(parts) + "\n", hunks


def fragment_index_entry(
    record: Mapping[str, str],
    *,
    omitted: bool = False,
    reason: str | None = None,
    disposition: str | None = None,
    stats: Mapping[str, object] | None = None,
    hunks: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """One ordered ``fragments.json`` record for a changed file.

    Maps a ``changed-files.json`` record to ``{path, path_html, status, id,
    fragment, omitted, hunks?, disposition?, <stats…>, old_path?, old_path_html?,
    reason?}``. ``fragment`` is the relative path of the escaped fragment file, or
    ``None`` when the body is omitted (excluded, capped, or otherwise classified out by
    the Change Classifier, issue #7) — an omitted file still appears in the index with
    its status, its ``stats``, and a **required** ``reason`` so **nothing omitted is
    ever hidden** (DESIGN). ``disposition`` (when given) is the classifier's stable
    verdict string (``include-body``/``omit:lockfile``/…) so the cockpit can group
    omissions by kind rather than parse the prose ``reason``. ``stats`` (when given) is
    merged verbatim into the entry — the classifier's per-file line counts (``added``,
    ``deleted``, ``binary``) that survive even when the body does. ``hunks`` (when
    given) is the file's per-hunk index from :func:`file_diff_fragment` —
    ``[{index, anchor, header_html}, …]``, the manifest side of the Hunk Anchorer
    (ADR-0014) that lets a ``{path, hunk}`` evidence ref link to the exact hunk. It rides only on
    an included body — an **omitted** file has no fragment, hence no hunk ids (the key
    is simply absent, never ``[]`` masquerading as "no hunks in a shown diff"). Keeping
    the whole entry schema in this one builder is deliberate: ``fragments.json`` has a
    single authoring site. ``path``/``old_path`` are the raw agent-facing strings;
    ``path_html``/``old_path_html`` are the same values having crossed the boundary
    (escaped, marker-wrapped) for injection into cockpit headings.
    """
    if omitted and not (reason and reason.strip()):
        raise ValueError("an omitted fragment index entry requires a non-empty reason")
    if omitted and hunks is not None:
        raise ValueError("an omitted fragment index entry has no body, so it carries no hunks")
    fid = file_fragment_id(record["path"])
    entry: dict[str, object] = {
        "path": record["path"],
        # ``path_html`` is the same path having *crossed the boundary* — escaped and
        # marker-wrapped. The File Walkthrough / Review Route (issue #6) place paths
        # inside cockpit headings, and a path is attacker-influenceable, so the agent
        # injects ``path_html`` there verbatim and never hand-types ``path`` into HTML.
        "path_html": fragment(record["path"]),
        "status": record.get("status", ""),
        "id": fid,
        "fragment": None if omitted else f"{FRAGMENTS_DIRNAME}/{fid}.html",
        "omitted": omitted,
    }
    if hunks is not None:
        entry["hunks"] = hunks
    if disposition is not None:
        entry["disposition"] = disposition
    if stats is not None:
        entry.update(stats)
    old_path = record.get("old_path")
    if old_path is not None:
        entry["old_path"] = old_path
        entry["old_path_html"] = fragment(old_path)
    if reason is not None:
        entry["reason"] = reason
    return entry
