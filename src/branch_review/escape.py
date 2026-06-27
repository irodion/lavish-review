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

from collections.abc import Iterable, Mapping
from html import escape as _html_escape

# Sentinel markers delimiting one untrusted region in the raw HTML source.
# HTML comments by construction: they render nothing and are absent from
# ``element.textContent`` (only Text-node descendants count), so they never leak
# into the page or into app.js's diff colourizer. The linter pairs them up in the
# raw source and asserts the bytes between them carry no literal ``<``/``>``.
UNTRUSTED_OPEN = "<!--brc:untrusted-->"
UNTRUSTED_CLOSE = "<!--/brc:untrusted-->"

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
    """One ``<li>`` for a changed file: trusted status badge + escaped path(s)."""
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
    parts: list[str] = []

    parts.append("<!-- fragment: title -->")
    parts.append(f'<h1 class="cockpit-title">{fragment(branch)}</h1>')

    parts.append("<!-- fragment: meta -->")
    parts.append('<dl class="cockpit-meta">')
    parts.append(f"  <dt>Base</dt><dd>{fragment(base)}</dd>")
    parts.append(f"  <dt>Branch</dt><dd>{fragment(branch)}</dd>")
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
