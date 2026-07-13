# The cockpit's CSP is context-aware: strict for `file://`, bounded-relaxed for Lavish

The Review Cockpit ships a `Content-Security-Policy` meta. ADR-0002 made it
**strict** (`default-src 'none'; script-src 'self'; style-src 'self'; …`, no
`'unsafe-inline'`, no remote) as defense-in-depth for the portable artifact. But a
cockpit opened **through Lavish-AXI** — the normal `/review-branch` flow — is served
by Lavish, which injects its own annotation/editor UI into the page: a CDN Tailwind
runtime and DaisyUI stylesheet (`https://cdn.jsdelivr.net`), an inline
`<style type="text/tailwindcss">`, and an inline `<script type="module">` bootstrap.
Lavish does **not** strip or reconcile with the page's own CSP.

Under the strict policy every one of those injections is blocked. Lavish's
same-origin `chrome-client.js`/`chrome.css` still load (so annotation *data* is
captured), but the entire visual layer is gone: the annotation input renders as an
unstyled, default-sized element at the end of `<body>`, focus jumps there on every
annotation, and the UI is effectively unusable. This was found by HILT testing.

**Decision.** The cockpit's CSP is **context-aware**:

- **`file://` portable artifact → strict** (`STRICT_CSP`, unchanged). This is the
  hostile-input case ADR-0002 protects.
- **Served through Lavish-AXI → `INTERACTIVE_CSP`**, a *bounded* relaxation that
  trusts `'self'` + the Lavish CDN (`https://cdn.jsdelivr.net`) and permits inline
  script/style so Lavish's editor stack can run.

**Why this is safe.** The **primary** XSS control is the deterministic
entity-escaping at the Escape Boundary (ADR-0002), which is **CSP-independent** —
untrusted diff bytes, paths, and commit messages are already HTML entities and
cannot execute under *any* policy. The strict CSP is defense-in-depth on top of
that. Relaxing it is acceptable **only** because the interactive context is local
and loopback-only: Lavish serves from `127.0.0.1`, a server the user launched. The
relaxation stays **bounded** — `default-src 'none'` still denies everything not
named, `base-uri`/`form-action` stay locked to `'none'`/`'self'`, and script/style
widen only to `'self'` + the Lavish CDN + inline/eval, never an open wildcard or an
arbitrary remote host.

## Consequences

- `escape.py` gains `INTERACTIVE_CSP` (and `LAVISH_CDN`) beside `STRICT_CSP`.
- The Cockpit Linter (`lint.py`) gains a `csp_mode` (`strict` default | `interactive`)
  and `--csp-mode` flag. `interactive` enforces the bounded baseline above; it does
  **not** relax the untrusted-markup or no-inline-JS rules — the cockpit *we* author
  still contains no inline JS (Lavish injects its own at serve time, which the lint
  never sees). A wildcard or arbitrary-remote CSP still fails in `interactive`.
- The renderer authors the cockpit with `INTERACTIVE_CSP` for the interactive review
  and lints with `--csp-mode interactive`. A future portable-export path would use
  `STRICT_CSP` + strict lint.
- The right long-term fix is upstream: a tool that injects inline/CDN assets into a
  page it serves should reconcile with that page's CSP (strip/rewrite it, or nonce
  its injections). Until Lavish does, this context-aware policy is how we stay usable
  without weakening the portable artifact. Refines [ADR-0002](./0002-deterministic-escape-boundary.md).
