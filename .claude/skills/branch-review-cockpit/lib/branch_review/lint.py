"""The Cockpit Linter — a deterministic post-write tripwire on ``review.html`` (ADR-0002).

The Escape Boundary (:mod:`branch_review.escape`) makes untrusted data safe *by
construction*, and the deterministic renderer owns the frame. This linter is the
defense-in-depth check that runs before the renderer writes the cockpit and fails the
build — loudly, before the file is ever opened — if the frame violates the
hardening rules. It is a tripwire, not a sanitizer: it never edits the cockpit, it
only refuses an unsafe one.

Rules enforced:

* **Untrusted markup.** Inside any Escape-Boundary region (delimited by the
  :data:`~branch_review.escape.UNTRUSTED_OPEN` / ``UNTRUSTED_CLOSE`` markers) the
  raw source must contain no literal ``<`` or ``>`` — every untrusted character
  must have arrived as an entity. A raw ``<script>`` pasted from a hostile diff or
  commit message trips this immediately.
* **No inline JS.** Every ``<script>`` must load an external ``src`` and carry no
  inline body; inline event handlers (``on*=``) and ``javascript:`` URIs are
  rejected. This is what lets the cockpit ship a strict CSP with no
  ``'unsafe-inline'``.
* **No remote assets under vendored styling.** When ``styling: vendored`` (the
  default), any remote ``src``/``href`` (``http(s):`` or protocol-relative ``//``)
  fails — the cockpit must render with local vendored assets only. ``styling: cdn``
  is the opt-in that relaxes this.
* **Strict CSP.** A ``<meta http-equiv="Content-Security-Policy">`` must be present
  and meet the full baseline of :data:`~branch_review.escape.STRICT_CSP`, not just
  constrain scripts: ``default-src 'none'`` (deny every resource type by default),
  ``script-src`` limited to ``'self'``/``'none'``, and ``base-uri`` + ``form-action``
  locked to ``'none'``/``'self'`` — those two are checked explicitly because they do
  **not** fall back to ``default-src``, so a policy that omits them leaves ``<base>``
  hijacking and form exfiltration open. ``'unsafe-inline'`` / ``'unsafe-eval'`` are
  rejected anywhere. A policy like ``script-src 'self'`` with no ``default-src``
  fails: it would leave every other resource type governed by the browser default.

**Structural rules (ADR-0014/0016, issues #62/#86).** The rules above keep the cockpit
*safe*; these keep it *coherent*, so authoring drift breaks the lint instead of the
reviewer's session. They run only when the caller supplies the run's analysis step ids
(the ``step_ids`` argument / the CLI's ``--analysis``) — the pure-security lint above is
unchanged without it. Given that set, the lint also fails when:

* **Dangling evidence anchor.** An in-page link (``<a href="#…">``) points at a
  fragment that no element ``id`` in the document carries — a deep link that would
  land nowhere.
* **Step id set mismatch.** The step ids in the DOM (the ids of
  ``<details class="step">`` elements) are not *exactly* the analysis's step id set:
  a step present in the analysis but missing from the page, one on the page that the
  analysis never minted, or the same id on two elements.
* **Missing seam.** A required pre-planted seam is absent, unpaired, or out of order —
  the Q&A seam the bake fills (:mod:`branch_review.bake`), or any analysis step's
  live-evidence seam (:mod:`branch_review.evidence`). The seam must be *fillable*: an
  open marker followed by its close, exactly the ``open .*? close`` pattern the injectors
  match. An open-only seam or a reversed pair otherwise passes a naive presence check but
  makes the injector silently no-op — the bake appends a duplicate block, live-evidence
  records the fragment but leaves the page unchanged — a silent break the lint now catches.
* **Misplaced evidence seam.** A step's live-evidence seam must sit *inside that step's*
  ``<details class="step">`` panel. The injector matches the marker text wherever it is,
  so a ``tN.sM`` seam planted under a different step's panel would render that step's
  answer under the wrong one; the lint attributes each seam to its enclosing panel and
  fails if they disagree.

See ``DESIGN.md`` and ``docs/adr/0002-deterministic-escape-boundary.md`` (Escape
Boundary) and ``docs/adr/0014-deck-presentation-mode.md`` (structural rules).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from branch_review.escape import (
    LAVISH_CDN,
    QA_SEAM_CLOSE,
    QA_SEAM_OPEN,
    UNTRUSTED_CLOSE,
    UNTRUSTED_OPEN,
    evidence_seam_markers,
)

# A remote reference the vendored cockpit must not load: an absolute http(s) URL
# or a protocol-relative ``//host`` one. Local relative paths (``assets/app.js``),
# fragments (``#x``), and ``mailto:`` links are not remote resource loads.
_REMOTE_PREFIXES = ("http://", "https://", "//")

# The WHATWG URL parser strips ASCII tab and newlines (U+0009/000A/000D) from
# anywhere in a URL before resolving its scheme, so ``java\tscript:`` and
# ``java\nscript:`` both become ``javascript:`` in the browser.
_URL_STRIPPED = str.maketrans("", "", "\t\n\r")
# ...and it trims leading/trailing C0 controls (U+0000–U+001F) and spaces, so
# ``\x01javascript:`` resolves too. Strip the same set before the scheme checks.
_URL_TRIM = "".join(map(chr, range(0x21)))

# Tokens a strict ``script-src`` may contain.
_STRICT_SCRIPT_SOURCES = frozenset({"'self'", "'none'"})

# The strict CSP baseline every cockpit must satisfy — the enforced core of
# STRICT_CSP (escape.py), each directive mapped to the source tokens it may carry.
# ``default-src`` must be the catch-all denial ``'none'`` the skill promises;
# ``base-uri``/``form-action`` are pinned here too because they have their own
# permissive browser defaults and do NOT inherit from ``default-src``. Functional
# fetch directives (style/img/font-src) are deliberately not required — they are not
# security-load-bearing once ``default-src 'none'`` denies everything by default.
# Directives both baselines share verbatim: ``default-src`` is the catch-all denial
# in every mode, and ``base-uri``/``form-action`` stay locked because they don't
# inherit from ``default-src``. Only ``script-src``/``style-src`` differ by mode.
_COMMON_CSP_DIRECTIVES: dict[str, frozenset[str]] = {
    "default-src": frozenset({"'none'"}),
    "base-uri": frozenset({"'none'", "'self'"}),
    "form-action": frozenset({"'none'", "'self'"}),
}

_CSP_BASELINE: dict[str, frozenset[str]] = {
    **_COMMON_CSP_DIRECTIVES,
    "script-src": _STRICT_SCRIPT_SOURCES,
}

# The bounded baseline for a cockpit opened **through Lavish-AXI** (csp_mode
# "interactive"; see escape.INTERACTIVE_CSP and docs/adr/0004-interactive-csp.md).
# Lavish injects an inline/CDN editor stack the strict policy blocks, so script/style
# are widened — but only to ``'self'`` + the Lavish CDN + inline/eval, never an open
# wildcard. ``default-src 'none'`` and the ``base-uri``/``form-action`` locks are
# retained exactly as in strict mode, and the blanket ``'unsafe-inline'``/
# ``'unsafe-eval'`` rejection is intentionally NOT applied here (those tokens are the
# whole point of this mode). Functional fetch directives stay unconstrained, as in
# strict mode.
_INTERACTIVE_SCRIPT_SOURCES = frozenset(
    {"'self'", "'none'", "'unsafe-inline'", "'unsafe-eval'", "'wasm-unsafe-eval'", LAVISH_CDN}
)
_INTERACTIVE_STYLE_SOURCES = frozenset({"'self'", "'none'", "'unsafe-inline'", LAVISH_CDN})
_CSP_BASELINE_INTERACTIVE: dict[str, frozenset[str]] = {
    **_COMMON_CSP_DIRECTIVES,
    "script-src": _INTERACTIVE_SCRIPT_SOURCES,
    "style-src": _INTERACTIVE_STYLE_SOURCES,
}

# Per mode: (directive baseline, forbid unsafe-* outright, remote hosts allowed in
# ANY directive). The remote allowlist bounds *every* directive — not just the ones
# in the baseline — so a relaxed mode can't smuggle an arbitrary host into
# connect-src/img-src/worker-src/etc. Strict allows no remote host at all;
# interactive allows only the Lavish CDN (matching escape.INTERACTIVE_CSP).
_CSP_MODES: dict[str, tuple[dict[str, frozenset[str]], bool, frozenset[str]]] = {
    "strict": (_CSP_BASELINE, True, frozenset()),
    "interactive": (_CSP_BASELINE_INTERACTIVE, False, frozenset({LAVISH_CDN})),
}

_UNTRUSTED_RE = re.compile(
    re.escape(UNTRUSTED_OPEN) + "(.*?)" + re.escape(UNTRUSTED_CLOSE),
    re.DOTALL,
)

# The content of a live-evidence seam comment — ``brc:evidence:<step id>`` for an
# open marker, ``/brc:evidence:<step id>`` for a close — as HTMLParser hands it to
# handle_comment (the ``<!--``/``-->`` stripped). Captures the step id from either.
_EVIDENCE_COMMENT = re.compile(r"^\s*/?brc:evidence:(t\d+\.s\d+)\s*$")


@dataclass(frozen=True)
class LintError:
    """One reason the cockpit failed lint: a stable ``rule`` id and a message."""

    rule: str
    message: str

    def __str__(self) -> str:
        return f"[{self.rule}] {self.message}"


def _normalize_url(value: str) -> str:
    """Clean a URL attribute the way a browser does before it resolves the scheme.

    Removes embedded ASCII tab/newlines and trims leading/trailing C0 controls and
    spaces, so a control-obfuscated ``java\\tscript:`` href can't slip past the
    scheme checks that treat it as inline JS.
    """
    return value.translate(_URL_STRIPPED).strip(_URL_TRIM)


def _is_remote(url: str) -> bool:
    """True if ``url`` points at a remote resource (absolute or protocol-relative)."""
    return url.lower().startswith(_REMOTE_PREFIXES)


def _first_attr(attrs: list[tuple[str, str | None]], name: str) -> str | None:
    """The FIRST value of ``name`` (case-insensitive) — the one the browser keeps.

    HTML parsers and browsers honour the first occurrence of a duplicated attribute and
    ignore the rest, but a collapsed ``{name: value}`` dict keeps the LAST. The
    structural reads (a step panel's ``id`` and ``class``) must therefore use this, not
    the dict, or ``class="not-step" class="step"`` would read as a step to the lint
    while the browser DOM has none — a divergence a hand-authored cockpit could exploit
    to pass the step/seam checks without rendering the panel. ``None`` if absent.
    """
    for raw_name, raw_value in attrs:
        if raw_name.lower() == name:
            return raw_value or ""
    return None


def _check_untrusted_regions(html: str) -> list[LintError]:
    """Fail if any Escape-Boundary region carries a literal ``<`` or ``>``.

    Operates on the raw source, not a parsed DOM: every untrusted character must
    already be an entity by the time it lands between the markers, so a single
    literal angle bracket is proof an unescaped string slipped through the seam.
    """
    errors: list[LintError] = []

    opens = html.count(UNTRUSTED_OPEN)
    closes = html.count(UNTRUSTED_CLOSE)
    if opens != closes:
        errors.append(
            LintError(
                "untrusted-unbalanced",
                f"unbalanced Escape-Boundary markers: {opens} open vs {closes} close",
            )
        )

    for match in _UNTRUSTED_RE.finditer(html):
        inner = match.group(1)
        if "<" in inner or ">" in inner:
            snippet = inner.strip()[:80]
            errors.append(
                LintError(
                    "untrusted-markup",
                    f"unescaped '<' or '>' inside an untrusted region: {snippet!r}",
                )
            )
    return errors


class _TagAuditor(HTMLParser):
    """Walks the cockpit's tags to enforce the no-inline-JS, no-remote, CSP rules."""

    def __init__(self, styling: str) -> None:
        # convert_charrefs is irrelevant here (we inspect tags/attrs, not text),
        # but the explicit default keeps behaviour stable across versions.
        super().__init__(convert_charrefs=True)
        self.styling = styling
        self.errors: list[LintError] = []
        self.csp_content: str | None = None
        # HTMLParser treats a <script> body as raw text, so scripts can never
        # nest — a plain in/out flag is all the body tracking needs.
        self._in_script = False
        self._script_has_src = False
        self._script_inline_flagged = False
        # Structural bookkeeping (issues #62/#86), consumed only when the caller asked
        # for the structural pass. Every element id (anchor-resolution targets); the
        # ids of <details class="step"> elements in document order (duplicates kept
        # so the mismatch check can report a repeat); and the fragment of every
        # in-page <a href="#…"> (dangling-anchor sources).
        self.element_ids: set[str] = set()
        self.step_ids: list[str] = []
        self.anchor_fragments: list[str] = []
        # A stack of currently-open <details> elements — each entry is the element's
        # step id if it is a <details class="step">, else None — so a live-evidence
        # seam comment can be attributed to the step panel that encloses it (an
        # evidence seam must live inside its own step, not merely somewhere in the doc).
        self._details_stack: list[str | None] = []
        # Per step id: the enclosing step panel of each of its evidence-seam markers
        # (None = not inside any step panel). A correctly-placed seam has every marker
        # enclosed by its own step; anything else is misfiled.
        self.evidence_marker_panels: dict[str, list[str | None]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._audit(tag, attrs)
        if tag == "script":
            self._in_script = True
        elif tag == "details":
            # First-wins id/class (browser semantics), matching the DOM step-id read in
            # _audit — so a duplicate attribute can't misattribute a seam's enclosing panel.
            is_step = "step" in (_first_attr(attrs, "class") or "").split()
            self._details_stack.append(_first_attr(attrs, "id") if is_step else None)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._audit(tag, attrs)  # self-closing: no body to track (no seam can nest here)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False
        elif tag == "details" and self._details_stack:
            self._details_stack.pop()

    def handle_comment(self, data: str) -> None:
        # Attribute a live-evidence seam marker (open or close) to the step panel that
        # encloses it, so the structural pass can reject a seam planted under the wrong
        # step. Non-evidence comments (untrusted/Q&A markers, plain comments) are ignored.
        match = _EVIDENCE_COMMENT.match(data)
        if match is None:
            return
        self.evidence_marker_panels.setdefault(match.group(1), []).append(
            self._enclosing_step_panel()
        )

    def _enclosing_step_panel(self) -> str | None:
        """The nearest enclosing <details class="step"> id, or None if inside none."""
        for step_id in reversed(self._details_stack):
            if step_id is not None:
                return step_id
        return None

    def handle_data(self, data: str) -> None:
        # A non-empty body inside a <script src=...> is dead code the browser
        # ignores, but it is still inline JS in the source — flag it once. A script
        # with no src was already reported by the no-inline-JS rule below.
        if (
            self._in_script
            and self._script_has_src
            and not self._script_inline_flagged
            and data.strip()
        ):
            self.errors.append(
                LintError("inline-js", "<script> has an inline body alongside its src")
            )
            self._script_inline_flagged = True

    def _audit(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name.lower(): (value or "") for name, value in attrs}

        # Audit the RAW (name, value) pairs, not the collapsed dict: when a tag
        # carries duplicate attributes the browser keeps the FIRST, so
        # ``<a href="javascript:..." href="#ok">`` is live JS even though the dict
        # would keep the safe last value. Flagging any dangerous occurrence (and any
        # remote one) keeps the tripwire sound — duplicate security-relevant
        # attributes are a smell worth failing on regardless of order.
        for raw_name, raw_value in attrs:
            name = raw_name.lower()
            if name.startswith("on"):
                self.errors.append(
                    LintError("inline-js", f"<{tag}> carries inline event handler {raw_name!r}")
                )
            if name in ("src", "href"):
                value = _normalize_url(raw_value or "")
                if value.lower().startswith("javascript:"):
                    self.errors.append(
                        LintError("inline-js", f"<{tag}> {name} uses a javascript: URI")
                    )
                elif self.styling == "vendored" and _is_remote(value):
                    self.errors.append(
                        LintError(
                            "remote-asset",
                            f"<{tag}> {name}={value!r} is remote under styling: vendored",
                        )
                    )
                # An in-page anchor (<a href="#frag">) — the fragment must resolve to
                # an element id. Bare "#" (scroll-to-top) carries no fragment to check.
                if tag == "a" and name == "href" and value.startswith("#") and len(value) > 1:
                    self.anchor_fragments.append(value[1:])

        # Element id (an anchor target) and, when the element is a step panel, its
        # step id — the DOM side of the analysis↔page set check. Read id/class with
        # browser first-wins semantics (not the last-wins attr_map), so a duplicated
        # attribute can't make the lint disagree with the rendered DOM.
        element_id = _first_attr(attrs, "id")
        if element_id:
            self.element_ids.add(element_id)
            if "step" in (_first_attr(attrs, "class") or "").split():
                self.step_ids.append(element_id)

        if tag == "script":
            self._script_has_src = "src" in attr_map
            self._script_inline_flagged = False
            if not self._script_has_src:
                self.errors.append(
                    LintError("inline-js", "<script> without a src (inline JS forbidden)")
                )

        if tag == "meta" and attr_map.get("http-equiv", "").lower() == "content-security-policy":
            self.csp_content = attr_map.get("content", "")


# CSP source tokens that never trigger a remote fetch: the quoted keyword-sources
# (``'self'``, ``'none'``, ``'unsafe-*'``, ``'nonce-…'``, ``'sha256-…'``) and the
# non-network schemes below, which load from the document or memory rather than an
# origin. Every *other* token in a source list — the bare wildcard ``*``, a network
# scheme-source like ``https:``/``http:``/``ws:``, or any host-source
# (``example.com``, ``*.cdn.com``, ``https://host``) — can pull from a remote origin
# and must be on the mode's allowlist, so the remote check treats anything else as
# remote by default.
_CSP_NON_NETWORK_SCHEMES = frozenset({"data:", "blob:", "mediastream:", "filesystem:"})


def _csp_source_is_remote(token: str, allowed_remotes: frozenset[str]) -> bool:
    """True if a CSP source token can load from an origin not on ``allowed_remotes``.

    Inverts the test to an allowlist: a token is *safe* only if it is the mode's
    allowlisted remote, a quoted keyword-source, or a non-network scheme. Anything
    else — ``*``, a scheme-source (``https:``), or a bare/wildcard host — is treated
    as a remote that the strict/interactive bound forbids. This is stricter than
    matching known remote prefixes, which let ``*`` and ``https:`` slip through.
    """
    if token in allowed_remotes:
        return False
    if token.startswith("'") and token.endswith("'"):
        return False  # keyword-source: 'self' / 'none' / 'unsafe-*' / nonce / hash
    # Anything that isn't a non-network scheme is a host- or scheme-source (or '*').
    return token.lower() not in _CSP_NON_NETWORK_SCHEMES


def _check_csp(content: str | None, *, csp_mode: str = "strict") -> list[LintError]:
    """Fail unless the Content-Security-Policy meets the baseline for ``csp_mode``.

    ``strict`` (the default, for the portable ``file://`` artifact) enforces the
    full ``default-src 'none'`` / ``script-src 'self'`` baseline and rejects
    ``'unsafe-*'`` outright. ``interactive`` (for a cockpit served through
    Lavish-AXI) keeps ``default-src 'none'`` and the ``base-uri``/``form-action``
    locks but widens script/style to ``'self'`` + the Lavish CDN + inline/eval —
    still bounded, so an open wildcard or an arbitrary remote host is rejected. See
    ``docs/adr/0004-interactive-csp.md``.
    """
    if content is None:
        return [
            LintError(
                "csp-missing",
                "no <meta http-equiv='Content-Security-Policy'> found",
            )
        ]

    baseline, forbid_unsafe, allowed_remotes = _CSP_MODES[csp_mode]

    errors: list[LintError] = []
    directives: dict[str, list[str]] = {}
    for clause in content.split(";"):
        tokens = clause.split()
        if not tokens:
            continue
        name = tokens[0].lower()
        if name in directives:
            # Browsers honour the FIRST occurrence and ignore later duplicates, so a
            # repeated directive could let a weak first value slip past a lint that
            # only inspected the last. Flag it and keep the first (browser-enforced) one.
            errors.append(LintError("csp-weak", f"duplicate CSP directive {name!r}"))
            continue
        directives[name] = tokens[1:]

    for name, allowed in baseline.items():
        sources = directives.get(name)
        if sources is None:
            errors.append(LintError("csp-weak", f"CSP is missing the {name} directive"))
            continue
        disallowed = [tok for tok in sources if tok not in allowed]
        if disallowed:
            errors.append(
                LintError("csp-weak", f"{name} permits out-of-baseline source(s): {disallowed}")
            )

    # Bound EVERY directive's remote sources, not just the baseline ones: an arbitrary
    # host in connect-src/img-src/worker-src/etc. would otherwise pass unchecked. Only
    # the mode's allowlisted remote(s) (the Lavish CDN in interactive; none in strict)
    # may appear. The check is an allowlist (see _csp_source_is_remote), so the open
    # wildcard '*' and scheme-sources like 'https:' are rejected too — not just full
    # URLs; quoted keywords, data:/blob:, and the allowlisted host are the only passes.
    for name, sources in directives.items():
        for tok in sources:
            if _csp_source_is_remote(tok, allowed_remotes):
                errors.append(
                    LintError(
                        "csp-weak",
                        f"{name} permits a non-allowlisted remote/wildcard source {tok!r}",
                    )
                )

    # Strict mode only: a case-insensitive backstop catching 'unsafe-inline'/
    # 'unsafe-eval' *anywhere*, including in functional directives the per-directive
    # baseline above doesn't enumerate (e.g. style-src). Interactive mode permits
    # these tokens by design (see _CSP_BASELINE_INTERACTIVE), so it skips the check.
    if forbid_unsafe:
        lowered = content.lower()
        if "'unsafe-inline'" in lowered or "'unsafe-eval'" in lowered:
            errors.append(LintError("csp-weak", "CSP contains 'unsafe-inline' or 'unsafe-eval'"))
    return errors


def _seam_is_fillable(html: str, open_marker: str, close_marker: str) -> bool:
    """True iff the injectors could fill this seam — an open marker *followed by* a close.

    Both the Q&A bake (:func:`branch_review.bake.inject_qa`) and live-evidence injector
    (:func:`branch_review.evidence.inject_evidence_html`) locate the seam with the regex
    ``open .*? close`` under ``DOTALL`` and replace what it spans. Mere presence of both
    marker strings is not enough: a reversed pair (``close`` before ``open``) or an
    open-only seam matches nothing, so the injector silently no-ops — the bake appends a
    duplicate block, evidence records the fragment but leaves the page unchanged. Mirror
    the injectors' own pattern here so the lint accepts exactly the seams they can fill.
    """
    return (
        re.search(re.escape(open_marker) + ".*?" + re.escape(close_marker), html, re.DOTALL)
        is not None
    )


def _check_structure(html: str, auditor: _TagAuditor, step_ids: Iterable[str]) -> list[LintError]:
    """Fail on cockpit↔analysis structural drift (issues #62/#86).

    Given the analysis's step id set, checks that the authored DOM matches it and
    carries the seams the bake and live-evidence injection depend on: exact step id
    correspondence (no missing, extra, or duplicated ids), every in-page anchor
    resolving to a real element id, and the Q&A seam plus each step's live-evidence
    seam being present. Each message names the offending id, anchor, or seam so a
    failure points straight at what to fix. These are separate from — and never
    substitute for — the escape/CSP rules: this returns its own list, appended after.
    """
    errors: list[LintError] = []

    expected = list(step_ids)
    expected_set = set(expected)

    # DOM step ids: dedupe while flagging repeats (a duplicate id also breaks anchor
    # resolution, but here it is specifically a step invariant the reviewer relies on).
    # ``seen`` ends up equal to the unique DOM step id set — reuse it for the checks.
    dom_unique: list[str] = []
    seen: set[str] = set()
    for sid in auditor.step_ids:
        if sid in seen:
            errors.append(
                LintError(
                    "step-id-duplicate",
                    f"step id {sid!r} appears on more than one element",
                )
            )
        else:
            seen.add(sid)
            dom_unique.append(sid)

    for sid in expected:
        if sid not in seen:
            errors.append(
                LintError(
                    "step-id-missing",
                    f'analysis step {sid!r} has no <details class="step"> element',
                )
            )
    for sid in dom_unique:
        if sid not in expected_set:
            errors.append(
                LintError(
                    "step-id-unknown",
                    f"cockpit step {sid!r} is not in the analysis's step set",
                )
            )

    for fragment_id in auditor.anchor_fragments:
        if fragment_id not in auditor.element_ids:
            errors.append(
                LintError(
                    "dangling-anchor",
                    f"in-page link '#{fragment_id}' resolves to no element id",
                )
            )

    # Both the open AND close marker must be present: the bake and live-evidence
    # injectors match the open…close pair, so an author who plants only one gets a
    # silent failure downstream — inject_qa appends a duplicate block, inject_evidence
    # records the fragment but leaves the page unchanged. Require a *fillable* pair here
    # so that drift breaks the lint instead.
    if not _seam_is_fillable(html, QA_SEAM_OPEN, QA_SEAM_CLOSE):
        errors.append(
            LintError(
                "seam-missing",
                "the Q&A seam is missing or malformed (plant the open marker before its "
                "close, after the last section)",
            )
        )
    for sid in expected:
        seam_open, seam_close = evidence_seam_markers(sid)
        if not _seam_is_fillable(html, seam_open, seam_close):
            errors.append(
                LintError(
                    "seam-missing",
                    f"step {sid!r} has no fillable live-evidence seam (needs the open "
                    "marker before its close)",
                )
            )
            continue
        # A fillable seam is not enough: it must sit inside *this* step's panel, or
        # inject_evidence would render the answer under the wrong step (the injector
        # matches the marker text globally, wherever it is). A correctly-placed seam is
        # exactly its open + close markers, both enclosed by sid's own panel — i.e.
        # ``panels == [sid, sid]``. Anything else is a misfile: ``[None, …]`` (outside any
        # step) or ``[other, …]`` (under the wrong step), and — crucially — *fewer than
        # two* attributed markers, which means HTMLParser never saw one as a comment
        # because it sits inside a raw-text element (<style>, <textarea>, <title>,
        # <script>, <xmp>). _seam_is_fillable still finds it there by substring, so the
        # injector would rewrite the seam and land the answer inside that element,
        # invisible/wrong. Require the full attribution, not merely no wrong-panel marker.
        panels = auditor.evidence_marker_panels.get(sid, [])
        if panels != [sid, sid]:
            errors.append(
                LintError(
                    "seam-misplaced",
                    f"the live-evidence seam for step {sid!r} is planted outside its own "
                    '<details class="step"> panel',
                )
            )

    return errors


def lint_cockpit(
    html: str,
    *,
    styling: str = "vendored",
    csp_mode: str = "strict",
    step_ids: Iterable[str] | None = None,
) -> list[LintError]:
    """Lint a cockpit's HTML; return every violation (empty list means it passes).

    ``styling`` is the resolved cockpit styling (``vendored`` default, ``cdn``
    opt-in); it only relaxes the remote-asset rule. ``csp_mode`` selects the CSP
    baseline: ``strict`` (default, the portable ``file://`` artifact) or
    ``interactive`` (a cockpit served through Lavish-AXI — see
    :func:`_check_csp`). The untrusted-markup and no-inline-JS rules are unchanged
    by either: the cockpit we author never contains inline JS, even in
    ``interactive`` mode (Lavish injects its own at serve time).

    ``step_ids`` is the run's analysis step id set (from
    :func:`branch_review.analysis.step_ids`). When given, the structural pass
    (:func:`_check_structure`) also runs — step id correspondence, anchor
    resolution, and seam presence. When ``None`` (a caller with no analysis to hand,
    e.g. a bare security re-lint) only the escape/CSP rules run. Either way the two
    families are independent: a structural failure never suppresses an escape/CSP one.
    """
    errors: list[LintError] = []
    errors.extend(_check_untrusted_regions(html))

    auditor = _TagAuditor(styling)
    auditor.feed(html)
    auditor.close()
    errors.extend(auditor.errors)
    errors.extend(_check_csp(auditor.csp_content, csp_mode=csp_mode))
    if step_ids is not None:
        errors.extend(_check_structure(html, auditor, step_ids))
    return errors


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: lint a cockpit file, exit non-zero on any violation."""
    parser = argparse.ArgumentParser(
        prog="lint_cockpit",
        description="Fail if a Review Cockpit violates the Escape Boundary hardening rules.",
    )
    parser.add_argument("path", type=Path, help="Path to the cockpit review.html.")
    parser.add_argument(
        "--styling",
        choices=("vendored", "cdn"),
        default="vendored",
        help="Resolved cockpit styling (default: vendored).",
    )
    parser.add_argument(
        "--csp-mode",
        choices=("strict", "interactive"),
        default="strict",
        help="CSP baseline: strict (portable file:// artifact) or interactive "
        "(served through Lavish-AXI). Default: strict.",
    )
    parser.add_argument(
        "--analysis",
        type=Path,
        default=None,
        help="Path to the run's analysis.json. When given, the structural pass also "
        "runs: step ids must match the analysis, in-page anchors must resolve, and "
        "the Q&A / per-step evidence seams must be present.",
    )
    args = parser.parse_args(argv)

    try:
        html = args.path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot read {args.path}: {exc}", file=sys.stderr)
        return 2

    step_ids: list[str] | None = None
    if args.analysis is not None:
        from branch_review.analysis import step_ids as analysis_step_ids

        try:
            analysis = json.loads(args.analysis.read_text(encoding="utf-8"))
        except OSError as exc:
            print(f"error: cannot read {args.analysis}: {exc}", file=sys.stderr)
            return 2
        except json.JSONDecodeError as exc:
            print(f"error: {args.analysis} is not valid JSON: {exc}", file=sys.stderr)
            return 2
        step_ids = analysis_step_ids(analysis)

    errors = lint_cockpit(html, styling=args.styling, csp_mode=args.csp_mode, step_ids=step_ids)
    if errors:
        for error in errors:
            print(f"lint: {error}", file=sys.stderr)
        print(
            f"Cockpit lint FAILED: {len(errors)} problem(s) in {args.path}",
            file=sys.stderr,
        )
        return 1

    print(f"Cockpit lint OK: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
