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
from collections.abc import Iterable, Mapping
from html import escape as _html_escape

# Sentinel markers delimiting one untrusted region in the raw HTML source.
# HTML comments by construction: they render nothing and are absent from
# ``element.textContent`` (only Text-node descendants count), so they never leak
# into the page or into app.js's diff colourizer. The linter pairs them up in the
# raw source and asserts the bytes between them carry no literal ``<``/``>``.
UNTRUSTED_OPEN = "<!--brc:untrusted-->"
UNTRUSTED_CLOSE = "<!--/brc:untrusted-->"

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


def build_fragments(
    *,
    branch: str,
    base: str,
    head_sha: str,
    changed_file_count: int,
    files: Iterable[Mapping[str, str]],
    commit_lines: Iterable[str],
) -> str:
    """Build ``fragments.html`` — the pre-escaped building blocks for the cockpit.

    Each section is delimited by a ``<!-- fragment: NAME -->`` guide comment so the
    agent can find and paste the blocks it needs into the frame it authors. Every
    untrusted value (branch, base, paths, commit subjects) is already escaped and
    marker-wrapped, so the agent injects these verbatim and never touches a raw
    untrusted string. ``head_sha`` and the count are git-plumbing values (hex/int),
    rendered through :func:`escape_text` anyway for uniformity.
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


def fragment_index_entry(
    record: Mapping[str, str],
    *,
    omitted: bool = False,
    reason: str | None = None,
    disposition: str | None = None,
    stats: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """One ordered ``fragments.json`` record for a changed file.

    Maps a ``changed-files.json`` record to ``{path, path_html, status, id,
    fragment, omitted, disposition?, <stats…>, old_path?, old_path_html?, reason?}``.
    ``fragment`` is the relative path of the escaped fragment file, or ``None`` when
    the body is omitted (excluded, capped, or otherwise classified out by the Change
    Classifier, issue #7) — an omitted file still appears in the index with its
    status, its ``stats``, and a **required** ``reason`` so **nothing omitted is ever
    hidden** (DESIGN). ``disposition`` (when given) is the classifier's stable verdict
    string (``include-body``/``omit:lockfile``/…) so the cockpit can group omissions
    by kind rather than parse the prose ``reason``. ``stats`` (when given) is merged
    verbatim into the entry — the classifier's per-file line counts (``added``,
    ``deleted``, ``binary``) that survive even when the body does. Keeping the whole
    entry schema in this one builder is deliberate: ``fragments.json`` has a single
    authoring site. ``path``/``old_path`` are the raw agent-facing strings;
    ``path_html``/``old_path_html`` are the same values having crossed the boundary
    (escaped, marker-wrapped) for injection into cockpit headings.
    """
    if omitted and not (reason and reason.strip()):
        raise ValueError("an omitted fragment index entry requires a non-empty reason")
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
