"""The Cockpit Linter — a deterministic post-write tripwire on ``review.html`` (ADR-0002).

The Escape Boundary (:mod:`branch_review.escape`) makes untrusted data safe *by
construction*, but the agent still authors the frame by hand. This linter is the
defense-in-depth check that runs after the agent writes the cockpit and fails the
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

See ``DESIGN.md`` and ``docs/adr/0002-deterministic-escape-boundary.md``.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from branch_review.escape import LAVISH_CDN, UNTRUSTED_CLOSE, UNTRUSTED_OPEN

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

# Resolved CSP baseline per mode, and whether the mode also forbids unsafe-* outright.
_CSP_MODES: dict[str, tuple[dict[str, frozenset[str]], bool]] = {
    "strict": (_CSP_BASELINE, True),
    "interactive": (_CSP_BASELINE_INTERACTIVE, False),
}

_UNTRUSTED_RE = re.compile(
    re.escape(UNTRUSTED_OPEN) + "(.*?)" + re.escape(UNTRUSTED_CLOSE),
    re.DOTALL,
)


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

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._audit(tag, attrs)
        if tag == "script":
            self._in_script = True

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._audit(tag, attrs)  # self-closing: no body to track

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False

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

        for name in attr_map:
            if name.startswith("on"):
                self.errors.append(
                    LintError("inline-js", f"<{tag}> carries inline event handler {name!r}")
                )

        for name in ("src", "href"):
            if name not in attr_map:
                continue
            value = _normalize_url(attr_map[name])
            if value.lower().startswith("javascript:"):
                self.errors.append(LintError("inline-js", f"<{tag}> {name} uses a javascript: URI"))
            elif self.styling == "vendored" and _is_remote(value):
                self.errors.append(
                    LintError(
                        "remote-asset",
                        f"<{tag}> {name}={value!r} is remote under styling: vendored",
                    )
                )

        if tag == "script":
            self._script_has_src = "src" in attr_map
            self._script_inline_flagged = False
            if not self._script_has_src:
                self.errors.append(
                    LintError("inline-js", "<script> without a src (inline JS forbidden)")
                )

        if tag == "meta" and attr_map.get("http-equiv", "").lower() == "content-security-policy":
            self.csp_content = attr_map.get("content", "")


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

    baseline, forbid_unsafe = _CSP_MODES[csp_mode]

    directives: dict[str, list[str]] = {}
    for clause in content.split(";"):
        tokens = clause.split()
        if tokens:
            directives[tokens[0].lower()] = tokens[1:]

    errors: list[LintError] = []
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

    # Strict mode only: a case-insensitive backstop catching 'unsafe-inline'/
    # 'unsafe-eval' *anywhere*, including in functional directives the per-directive
    # baseline above doesn't enumerate (e.g. style-src). Interactive mode permits
    # these tokens by design (see _CSP_BASELINE_INTERACTIVE), so it skips the check.
    if forbid_unsafe:
        lowered = content.lower()
        if "'unsafe-inline'" in lowered or "'unsafe-eval'" in lowered:
            errors.append(LintError("csp-weak", "CSP contains 'unsafe-inline' or 'unsafe-eval'"))
    return errors


def lint_cockpit(
    html: str, *, styling: str = "vendored", csp_mode: str = "strict"
) -> list[LintError]:
    """Lint a cockpit's HTML; return every violation (empty list means it passes).

    ``styling`` is the resolved cockpit styling (``vendored`` default, ``cdn``
    opt-in); it only relaxes the remote-asset rule. ``csp_mode`` selects the CSP
    baseline: ``strict`` (default, the portable ``file://`` artifact) or
    ``interactive`` (a cockpit served through Lavish-AXI — see
    :func:`_check_csp`). The untrusted-markup and no-inline-JS rules are unchanged
    by either: the cockpit we author never contains inline JS, even in
    ``interactive`` mode (Lavish injects its own at serve time).
    """
    errors: list[LintError] = []
    errors.extend(_check_untrusted_regions(html))

    auditor = _TagAuditor(styling)
    auditor.feed(html)
    auditor.close()
    errors.extend(auditor.errors)
    errors.extend(_check_csp(auditor.csp_content, csp_mode=csp_mode))
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
    args = parser.parse_args(argv)

    try:
        html = args.path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot read {args.path}: {exc}", file=sys.stderr)
        return 2

    errors = lint_cockpit(html, styling=args.styling, csp_mode=args.csp_mode)
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
