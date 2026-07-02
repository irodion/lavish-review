# Spike: Lavish host-seam verification (verified against v0.1.31)

Issue #38. Verifies the three capabilities the hybrid Layered Review cockpit (ADR-0009, ADR-0012) needs from the UI host, against the pinned `lavish-axi@0.1.31`. Method: read `dist/cli.mjs` + `dist/chrome-client.js`, then drive a live session end-to-end (cockpit-like artifact carrying the real `INTERACTIVE_CSP`, served via `open`, exercised in Chrome, feedback captured via `poll`).

**Headline: all three capabilities are natively supported. None of the fallback ladders in #38 is needed.** Details and caveats below — the caveats are contract details for #42/#43, not blockers.

## Architecture correction (refines the ADR-0004 picture)

In 0.1.31 Lavish does **not** inject a CDN/inline editor stack into the artifact document. The session page is a **wrapper chrome** (chat panel, annotate toggle) that hosts the artifact in a **sandboxed iframe** (`sandbox="allow-scripts allow-forms allow-popups allow-downloads"` — no `allow-same-origin`), served from `/artifact/<key>/index.html`. The only mutation of the artifact is one appended tag: `<script src="/sdk.js?key=<key>"></script>` — a **same-origin external script**, allowed by `script-src 'self'`.

- Verified live: the artifact renders fully under our real `INTERACTIVE_CSP`, the vendored relative-path `app.js` executes, zero CSP violations.
- Hardening opportunity (for a later issue, not decided here): the `script-src` relaxations (`'unsafe-inline'`, `'unsafe-eval'`, CDN) may no longer be needed for Lavish itself — but `style-src 'unsafe-inline'`-equivalent still is, because the SDK styles its annotation UI via DOM-created `<style>` elements.

## 1. Live re-render on file edit — **native support confirmed**

- Server watches the artifact with chokidar (`watchSession`): debounce 100 ms, `awaitWriteFinish` 100 ms → emits SSE `event: reload` → chrome resets the iframe `src`. No manual refresh, no reconnect.
- **Verified live, twice**: (1) rewrote `assets/app.js` + touched the HTML — the new script was executing in the browser seconds later with no browser interaction; (2) injected a new `<section>` at a `<!--brc:evidence-seam-->` comment on disk — it appeared rendered in the open session.
- Watch scope is the **file only** by default. Editing `assets/*` alone does not trigger reload — touch `review.html` (an injection always does) or opt in to directory scope with `<meta name="lavish-live-reload" content="root">` (watches the artifact's directory, ignoring `.git`/`node_modules`/`dist`/`build`/`.lavish-axi`).
- Scroll position is preserved across reloads (source-verified: the chrome tracks artifact scroll via `lavish:scroll` messages and replays it with `lavish:restoreScroll` on frame load; could not be driven live — see Harness note).

**Consequence for #43:** seam-bounded fragment injection works as designed — write the fragment to `review.html`, lint, done; the open cockpit re-renders itself. The "refresh to see it" and chat-only floors are unnecessary.

## 2. Annotation on injected content — **native support confirmed**

- The SDK binds annotation via **capture-phase listeners on `document`** (`click`/`mouseover`/`mouseup`), deriving the element's context (uid, selector, text) **at event time**. Nothing is bound per-element, so content added after load participates automatically.
- **Verified live**: a click on the dynamically injected element opened the annotation card; the queued annotation arrived in `poll` anchored to the injected node — `selector: p#injected-para`, `text:` the element's text.
- Contract correction to the poll-format spike: an element annotation's `tag` is the element's **tag name** (`p`, `section`, …; `text` for text-range annotations) — not the literal `annotation`. The loop and the bake's prompt extractor must not assume a closed `tag` vocabulary.

## 3. Structured state channel from in-page controls — **native support confirmed**

The SDK exposes `window.lavish` inside the artifact: `queuePrompt(prompt, {tag, text, uid, selector, target, data, queueKey, element})`, `sendQueuedPrompts()`, `endSession()`, `setStatus(msg)`. Our vendored `app.js` can drive it directly — no new machinery.

- **Verified live end-to-end**: a disposition control calling `queuePrompt("Disposition set: t1.c1 -> verified", {tag: "choice", text: "disposition:verified", element: btn, queueKey: "disposition:t1.c1", data: {kind: "disposition", claim: "t1.c1", disposition: "verified"}})` + `sendQueuedPrompts()` arrived in `poll` as a `tag: choice` prompt with the element's derived selector and the JSON payload appended to `prompt` as a `Context data:` block — cleanly machine-readable by the loop agent, which can then write the disposition store (ADR-0012).
- **Dedupe of pre-send updates requires an explicit `queueKey`.** Two updates for the same claim with `queueKey: "disposition:<claim-id>"` collapsed to the **last one** (verified). Without it, plain buttons derive no key and every click queues a separate prompt (also verified). Disposition controls in #42 must pass `queueKey` (or wrap controls in `data-lavish-question`).
- **Sends are presence-gated.** While the agent shows as `working` (feedback delivered, no reply yet), the chrome silently drops `sendQueuedPrompts` — the pills stay queued and flush on the next send once the agent has replied. Verified live. Consequences: dispositions are effectively **batched, not real-time**; the loop must `--agent-reply` promptly to reopen the channel; #42 should treat disposition delivery as eventually-consistent within the session.
- **`window.lavish` appears after page scripts run** (the SDK tag is appended after ours). Check for it at interaction time, never at parse time — verified `undefined` at parse, present after `load`. On `file://` (portable bake) it never appears; disposition controls must no-op or hide.

## 4. Reload / re-attach — verified

- Queued-but-unsent pills survive a full browser reload via `sessionStorage` (verified with an inert `app.js` swapped in before the reload, so nothing could re-queue them; the pill then sent and arrived in `poll`).
- Chat history is restored on reload from server state; the session (keyed by canonical file path) stays `open`. Combined with the poll-format spike's guarantee (interrupt exits 130/143, queued feedback preserved server-side), `Esc` → `/review-resume` → browser reload all compose safely.

## Harness note (testing, not production)

Chrome-extension synthetic input (clicks/scrolls dispatched by automation) does **not** penetrate the sandboxed cross-origin artifact iframe; real user input does — Lavish's own annotation UX depends on it and works. For automated E2E tests, make the artifact self-driving (test-only JS inside the artifact dispatching events / calling `window.lavish`), which is how this spike verified 2 and 3.

## Verdict table

| # | Capability (#38 ladder) | Verdict | Fallback needed |
|---|---|---|---|
| 1 | Live re-render on edit | **Native** (chokidar → SSE → iframe reset; scroll restored) | None |
| 2 | Annotation on injected content | **Native** (document-level capture delegation) | None |
| 3 | Structured state channel | **Native** (`window.lavish.queuePrompt` w/ `tag: choice`, `data`, `queueKey`) | None — but batched under presence-gating |
| 4 | Browser reload re-attach | **Verified** (sessionStorage pills + server-restored chat) | — |
