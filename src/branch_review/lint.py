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
  and strict: scripts limited to ``'self'``/``'none'`` and no ``'unsafe-inline'``
  / ``'unsafe-eval'`` anywhere.

See ``DESIGN.md`` and ``docs/adr/0002-deterministic-escape-boundary.md``.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from branch_review.escape import UNTRUSTED_CLOSE, UNTRUSTED_OPEN

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

# Tokens a strict ``script-src`` (or ``default-src`` fallback) may contain.
_STRICT_SCRIPT_SOURCES = frozenset({"'self'", "'none'"})

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


def _check_csp(content: str | None) -> list[LintError]:
    """Fail unless a strict Content-Security-Policy governs scripts."""
    if content is None:
        return [
            LintError(
                "csp-missing",
                "no <meta http-equiv='Content-Security-Policy'> found",
            )
        ]

    errors: list[LintError] = []
    directives: dict[str, list[str]] = {}
    for clause in content.split(";"):
        tokens = clause.split()
        if tokens:
            directives[tokens[0].lower()] = tokens[1:]

    script_sources = directives.get("script-src", directives.get("default-src"))
    if script_sources is None:
        errors.append(LintError("csp-weak", "CSP defines neither script-src nor default-src"))
    else:
        non_strict = [tok for tok in script_sources if tok not in _STRICT_SCRIPT_SOURCES]
        if non_strict:
            errors.append(
                LintError(
                    "csp-weak",
                    f"script-src permits non-strict source(s): {non_strict}",
                )
            )

    lowered = content.lower()
    if "'unsafe-inline'" in lowered or "'unsafe-eval'" in lowered:
        errors.append(LintError("csp-weak", "CSP contains 'unsafe-inline' or 'unsafe-eval'"))
    return errors


def lint_cockpit(html: str, *, styling: str = "vendored") -> list[LintError]:
    """Lint a cockpit's HTML; return every violation (empty list means it passes).

    ``styling`` is the resolved cockpit styling (``vendored`` default, ``cdn``
    opt-in); it only relaxes the remote-asset rule.
    """
    errors: list[LintError] = []
    errors.extend(_check_untrusted_regions(html))

    auditor = _TagAuditor(styling)
    auditor.feed(html)
    auditor.close()
    errors.extend(auditor.errors)
    errors.extend(_check_csp(auditor.csp_content))
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
    args = parser.parse_args(argv)

    try:
        html = args.path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot read {args.path}: {exc}", file=sys.stderr)
        return 2

    errors = lint_cockpit(html, styling=args.styling)
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
