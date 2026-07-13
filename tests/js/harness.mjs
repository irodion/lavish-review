// Load the vendored cockpit script against the minimal DOM (dom.mjs) and drive it
// the way a browser would: build a fixture cockpit document that mirrors the
// authored L0–L3 structure, run app.js in a fresh VM context wired to the fake
// window/document/location, and fire DOMContentLoaded. The Deck Presenter tests
// (deck.test.mjs) build on the handles returned here.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import { Document, DomEvent } from "./dom.mjs";

const APP_JS = fileURLToPath(
  new URL("../../.claude/skills/branch-review-cockpit/assets/app.js", import.meta.url)
);

// A tiny builder: h(doc, "tag.class#id", { attr: value }, [children | "text"]).
// The `#id` may appear anywhere after the tag and is split off first, so ids that
// contain no `.` work regardless of class order (step ids, which carry a dot, are
// set on the element directly instead).
function h(doc, spec, attrs, children) {
  let id = null;
  const hash = spec.indexOf("#");
  if (hash !== -1) {
    id = spec.slice(hash + 1);
    spec = spec.slice(0, hash);
  }
  const [tag, ...classes] = spec.split(".");
  const el = doc.createElement(tag);
  if (id) el.id = id;
  if (classes.length) el.className = classes.join(" ");
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  }
  for (const child of children || []) {
    el.appendChild(typeof child === "string" ? doc.createTextNode(child) : child);
  }
  return el;
}

// One L2 Review Step panel: <details class="step" id data-impact>… summary chips +
// summary text, body with detail, why_now, review prompts, evidence links, and any
// attention notes (muted asides, never counted).
function step(doc, { id, impact, summary, confidence, whyNow, prompts, evidence, notes }) {
  const summaryEl = h(doc, "summary", null, [
    h(doc, "span.chip.impact-" + impact, null, [impact]),
    " " + summary + " ",
    h(doc, "span.chip.confidence-" + confidence, null, ["confidence: " + confidence]),
  ]);
  const evidenceList = h(
    doc,
    "ul.evidence-list",
    null,
    evidence.map((e) =>
      h(doc, "li", null, [h(doc, "a", { href: e.href }, [e.label])].concat(e.note ? [" — ", h(doc, "span.note", null, [e.note])] : []))
    )
  );
  const bodyChildren = [
    h(doc, "p.detail", null, ["Detail for " + id + "."]),
    h(doc, "p.why-now", null, [whyNow]),
    h(doc, "h4", null, ["Review prompts"]),
    h(doc, "ul.review-prompts", null, prompts.map((q) => h(doc, "li", null, [q]))),
    h(doc, "h4", null, ["Evidence"]),
    evidenceList,
  ];
  for (const note of notes || []) {
    bodyChildren.push(h(doc, "aside.attention-note", null, [note]));
  }
  const body = h(doc, "div.step-body", null, bodyChildren);
  // The step id carries a dot (t1.s1) — set it directly, not through the dotted
  // `.class` spec syntax of h(). data-impact drives the derived thread/Map character.
  const panel = h(doc, "details.step", { "data-impact": impact }, [summaryEl, body]);
  panel.id = id;
  return panel;
}

// One L3 file panel with a single hunk section whose <pre class="diff"> holds the
// raw (pre-escaped) diff text — the caller supplies the diff body verbatim.
function filePanel(doc, { id, path, added, deleted, hunkId, diffText }) {
  const stats = h(doc, "span.file-stats", null, [
    h(doc, "span.added", null, ["+" + added]),
    " ",
    h(doc, "span.deleted", null, ["−" + deleted]),
  ]);
  const summary = h(doc, "summary", null, [path + " ", stats]);
  const pre = h(doc, "pre.diff", null, [diffText]);
  const hunk = h(doc, "section.hunk#" + hunkId, null, [pre]);
  const fileBody = h(doc, "div.file-body", null, [hunk]);
  return h(doc, "details.file#" + id, null, [summary, fileBody]);
}

// The default fixture: two threads (t1: two steps, t2: one), three changed files.
// t1.s1 (a behavior-change) cites a hunk whose diff text carries a <script> string
// (the DOM-relocation invariant fixture); t1.s2 (unknown-impact) cites a file-level
// anchor; t2.s1 (test-change) cites another hunk.
export function buildFixtureDocument() {
  const doc = new Document();
  const main = doc.createElement("main");
  doc.body.appendChild(main);

  const t1 = h(doc, "section.thread#t1", null, [
    h(doc, "h2", null, [h(doc, "span.thread-id", null, ["t1"]), "First thread"]),
    h(doc, "p.thread-summary", null, ["Summary of the first thread."]),
    step(doc, {
      id: "t1.s1",
      impact: "behavior-change",
      summary: "The first step, substantiated by a hunk.",
      confidence: "high",
      whyNow: "Start here — the observable behavior change.",
      prompts: ["Compare the old and new delay computation."],
      evidence: [{ href: "#hunk-a1", label: "src/one.py", note: "the changed function" }],
      notes: ["No test in the diff exercises the new timing."],
    }),
    step(doc, {
      id: "t1.s2",
      impact: "unknown-impact",
      summary: "The second step, substantiated at file level.",
      confidence: "medium",
      whyNow: "Read right after the change it depends on.",
      prompts: ["Check the caller's timeout — does the cap ever bite?"],
      evidence: [{ href: "#file-f2", label: "src/two.py" }],
    }),
  ]);

  const t2 = h(doc, "section.thread#t2", null, [
    h(doc, "h2", null, [h(doc, "span.thread-id", null, ["t2"]), "Second thread"]),
    h(doc, "p.thread-summary", null, ["Summary of the second thread."]),
    step(doc, {
      id: "t2.s1",
      impact: "test-change",
      summary: "A test-change step in the second thread.",
      confidence: "high",
      whyNow: "Read this once you understand t1.s1 — it pins the new behavior.",
      prompts: ["Does the test fail if the cap regresses?"],
      evidence: [{ href: "#hunk-b1", label: "src/three.py" }],
    }),
  ]);

  main.appendChild(t1);
  main.appendChild(t2);

  const l3 = doc.createElement("section");
  main.appendChild(l3);
  l3.appendChild(
    filePanel(doc, {
      id: "file-f1",
      path: "src/one.py",
      added: 12,
      deleted: 3,
      hunkId: "hunk-a1",
      // A hostile diff line: if the presenter ever built markup from this string it
      // would execute; because it only ever relocates/clones text nodes, it stays text.
      diffText:
        "@@ -1,3 +1,4 @@\n context\n+    payload = '<script>window.__pwned = true;</script>'\n-    old = 1\n",
    })
  );
  l3.appendChild(
    filePanel(doc, {
      id: "file-f2",
      path: "src/two.py",
      added: 4,
      deleted: 0,
      hunkId: "hunk-b0",
      diffText: "@@ -10,0 +11,4 @@\n+    added = True\n",
    })
  );
  l3.appendChild(
    filePanel(doc, {
      id: "file-f3",
      path: "src/three.py",
      added: 7,
      deleted: 7,
      hunkId: "hunk-b1",
      diffText: "@@ -5,7 +5,7 @@\n context\n-    before()\n+    after()\n",
    })
  );

  return doc;
}

// Run app.js against a document in the given protocol (default served/"http:").
// `dispositions`, when given, is the `{stepId: state}` map a resumed session's
// dispositions.json would carry — the harness serves it back through fetch so the
// restore-tint path can be exercised without a network.
export function loadCockpit({
  protocol = "http:",
  doc = buildFixtureDocument(),
  dispositions = null,
} = {}) {
  const location = { protocol, hash: "" };
  const window = {
    lavish: undefined,
    addEventListener() {},
    removeEventListener() {},
  };
  // The presenter never fetches on file://; on http:// loadDispositions() calls
  // fetch. Serve the given dispositions store back (a resumed review), or an
  // not-ok response so it no-ops without a network (a fresh review).
  const fetch = () =>
    Promise.resolve(
      dispositions
        ? { ok: true, json: () => Promise.resolve({ dispositions }) }
        : { ok: false, json: () => Promise.resolve(null) }
    );

  const sandbox = { window, document: doc, location, fetch, console, Promise };
  window.document = doc;
  vm.runInNewContext(readFileSync(APP_JS, "utf8"), sandbox, { filename: "app.js" });

  // Fire DOMContentLoaded — annotateDiff, dispositions, questions, then the deck.
  doc.dispatchEvent(new DomEvent("DOMContentLoaded", { bubbles: false }));

  return { document: doc, window, location };
}

// Dispatch a bubbling click on an element and return the event.
export function click(el) {
  const event = new DomEvent("click", { bubbles: true });
  el.dispatchEvent(event);
  return event;
}

// Dispatch a bubbling keydown from `target` (default: the document, i.e. no field
// focused), carrying `key` and any modifier flags — the shape onDeckKeydown reads.
// Dispatching from an input/textarea models a focused typing surface (the event's
// target is that element, exactly as a real keystroke would set it).
export function press(target, key, opts = {}) {
  const event = new DomEvent("keydown", { bubbles: true });
  event.key = key;
  event.metaKey = !!opts.metaKey;
  event.ctrlKey = !!opts.ctrlKey;
  event.altKey = !!opts.altKey;
  target.dispatchEvent(event);
  return event;
}

// Let the microtask chain in loadDispositions (fetch → json → apply) settle.
export function flush() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}
