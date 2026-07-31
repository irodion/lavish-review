# Spike: what the Host Seam gives us for file/line anchoring (verified against v0.1.31)

Issue #129, for the [line-anchored reviewer notes map](https://github.com/irodion/lavish-review/issues/126). Asks one question: **does Lavish already deliver a file/line-anchored annotation we can consume, so that #109 reduces to wiring?**

**Headline: no. The host derives no file or line context whatsoever.** The `target: {file, line}` shape in the [poll-format spike](./lavish-poll-format.md) documents what a *caller may pass*, not something Lavish infers — and that spike reads as though the host produces it. This document corrects that.

Method: read `dist/cli.mjs` (which embeds the in-artifact SDK served as `/sdk.js`) and `dist/chrome-client.js` from the pinned `lavish-axi@0.1.31`, resolved from the local npx cache. Line numbers below are into those files.

## 1. The context the host derives from an annotated element

```js
function context(el) {                                    // cli.mjs:887
  return {
    uid: uid(el),
    selector: selector(el),
    tag: (el.tagName || "").toLowerCase(),
    text: (el.innerText || el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 240)
  };
}
```

Four fields, and **not one of them is a file or a line**. `normalizePrompt` (`cli.mjs:1319`) then keeps exactly `{uid, prompt, selector, tag, text}` plus an optional `target`. There is no code path anywhere in the package that reads a path, a line number, or a `data-*` attribute of ours to build one.

## 2. `target` is an opaque passthrough, not a host-derived anchor

```js
function normalizeTarget(target) {                        // cli.mjs:1332
  if (!target || typeof target !== "object" || Array.isArray(target)) return null;
  return JSON.parse(JSON.stringify(target));
}
```

Any JSON-serializable object the caller hands to `queuePrompt({target})` survives verbatim. The SDK populates `target` on its own in exactly one case — a **text-range** selection (`cli.mjs:927`):

```js
const target = { type: "text-range", text, selector, commonAncestorSelector,
                 start: rangeBoundary(...), end: rangeBoundary(...) };
```

Still no file, still no line. So `target` is a slot we could put our own anchor in, which is a different (and weaker) claim than "the host already anchors to file/line".

## 3. Neither `uid` nor `selector` is usable as a durable anchor

- **`uid` is ephemeral** (`cli.mjs:862`): an incrementing counter assigned on demand and held in an in-memory `Map`. It does not survive a reload, and reload is routine here — every seam-bounded injection rewrites `review.html` and resets the iframe.
- **`selector` is positional and id-dependent** (`cli.mjs:866`): up to five parts, stopping early at the first ancestor with an `id`, otherwise `:nth-of-type(n)`. [Anchor identity](https://github.com/irodion/lavish-review/issues/127) settled that diff rows carry no ids, so a row's selector would be a positional chain through a **client-built** table — resolving it back to a line is exactly the DOM archaeology [ADR-0015](../adr/0015-claim-scoped-questions.md) moved feedback away from.

## 4. Annotation sits behind a mode toggle

The wrapper chrome owns an annotation switch and posts the state into the artifact frame (`chrome-client.js:411`):

```js
annotationSwitch.onclick = () => {
  annotation = !annotation;
  postToFrame({ type: "lavish:setAnnotationMode", enabled: annotation });
};
```

So routing line notes through annotation would put them behind a mode flip in the host chrome — a reviewer reading a hunk must first leave the artifact's own affordances, toggle a mode, then click. A `queuePrompt` control is available in one click, always.

## 5. Our own in-hunk controls are already exempt from annotation

```js
function isInteractiveControl(el) {                       // cli.mjs:950
  return !!(el && el.closest && el.closest("button,input,select,textarea,option,optgroup,label,[contenteditable]:not([contenteditable='false'])"));
}
```

Native controls are skipped by the annotation capture listeners — clicks act normally instead of annotating. This is why #108's copy button and the disposition controls work without special handling, and it means a `<button>` affordance inside a hunk needs nothing negotiated with the host.

Related, for whoever designs the capture control: `data-lavish-question` on a wrapper supplies an implicit queueKey (`cli.mjs:814`, `:830`), an alternative to passing `queueKey` explicitly.

## Recommendation: queue our own structured payload

| | ride the annotation path | queue our own payload |
|---|---|---|
| anchor delivered | a CSS selector | `{path, hunk, pos}` as structured data |
| resolving it | map selector → row → position, in a client-built table with no ids | none — it arrives resolved |
| reviewer cost | toggle annotation mode, then click | one click |
| new machinery | none, but a selector→line resolver we do not have | none — identical in shape to the step-question already shipping |

The second column is what `assets/app.js:441-451` already does for a Step-scoped Question:

```js
lavish.queuePrompt(text, {
  tag: "message",
  queueKey: "question:" + stepId,
  data: { kind: "step-question", step: stepId },
})
```

A line note is the same call with a different `data` payload. **So #109 does not reduce to host wiring — but the machinery it needs is already proven in this repo**, which is the next best answer. Element annotation stays available as a host feature; it is simply not the path line notes take, consistent with ADR-0015 having already demoted it for id-addressable feedback.

## Corrections this spike makes to earlier docs

- [`lavish-poll-format.md`](./lavish-poll-format.md) shows `target: {file: src/release_runner.cpp, line: 42}` in the verified-payload block, which reads as host-derived. It is caller-supplied; the SDK's own `target` is the text-range shape in §2 above.
- `CONTEXT.md`'s Host Seam entry describes annotation as "a CSS selector (plus optional file/line target)". True as written, but the file/line half is only ever what a caller passed.
