// Run-scoped UI-state store tests (issue #112). The one sanctioned page mutation —
// live-evidence injection — makes the host reload the iframe, which resets the deck's
// ephemeral state. These tests drive the vendored app.js against the DOM harness to
// prove the store carries the staged step, deck/document mode, per-step ask drafts,
// and document-mode disclosure across a reload, keyed by the renderer's run identity
// so a regenerated run self-invalidates — and stays inert on file:// / without a
// backend / without a run identity.

import { test } from "node:test";
import assert from "node:assert/strict";
import { DomEvent } from "./dom.mjs";
import { loadCockpit, buildFixtureDocument, memoryStorage, typeDraft, click, press } from "./harness.mjs";

const dot = (document, stepId) =>
  document.querySelectorAll(".deck-dot").find((d) => d.dataset.step === stepId);
const stagedStepId = (document) => {
  const crumb = document.querySelector(".deck-stage .deck-crumb-step");
  return crumb ? crumb.textContent : null;
};
const STORE_KEY = "brc:ui:/review.html";

test("staged step and ask draft survive an injection reload", () => {
  const storage = memoryStorage();

  // First session: enter the route, land on a mid-route step, type a draft question.
  const first = loadCockpit({ sessionStorage: storage });
  click(dot(first.document, "t1.s2"));
  assert.equal(stagedStepId(first.document), "t1.s2");
  typeDraft(first.document, "t1.s2", "does the cap ever bite?");

  // Injection reload: fresh document, same tab storage, same run identity.
  const second = loadCockpit({ sessionStorage: storage });
  assert.ok(second.document.body.classList.contains("deck-active"), "deck mode restored");
  assert.equal(stagedStepId(second.document), "t1.s2", "still staged on the same step");
  const input = second.document.getElementById("t1.s2").querySelector(".step-ask-input");
  assert.equal(input.value, "does the cap ever bite?", "the half-typed question survived");
});

test("deck/document mode is restored after a reload", () => {
  const storage = memoryStorage();

  const first = loadCockpit({ sessionStorage: storage });
  click(first.document.querySelector(".deck-toggle")); // → document mode
  assert.ok(!first.document.body.classList.contains("deck-active"));

  const second = loadCockpit({ sessionStorage: storage });
  assert.ok(!second.document.body.classList.contains("deck-active"), "document mode restored");
});

test("a document-mode reviewer's return position survives a reload", () => {
  const storage = memoryStorage();

  // Stage a mid-route step, then toggle to the full document. The deck is not showing
  // any step now, but toggling back must return to that step — the return memory.
  const first = loadCockpit({ sessionStorage: storage });
  click(dot(first.document, "t2.s1"));
  click(first.document.querySelector(".deck-toggle")); // → document mode
  assert.ok(!first.document.body.classList.contains("deck-active"));

  // Injection reload: restore document mode AND the return position.
  const second = loadCockpit({ sessionStorage: storage });
  assert.ok(!second.document.body.classList.contains("deck-active"), "document mode restored");
  click(second.document.querySelector(".deck-toggle")); // → back to the deck
  assert.equal(stagedStepId(second.document), "t2.s1", "returns to the step left off on");
});

test("document-mode disclosure survives a reload", () => {
  const storage = memoryStorage();

  const first = loadCockpit({ sessionStorage: storage });
  const file = first.document.getElementById("file-f2");
  file.open = true;
  file.dispatchEvent(new DomEvent("toggle", { bubbles: false })); // the event the store hears

  const second = loadCockpit({ sessionStorage: storage });
  assert.equal(second.document.getElementById("file-f2").open, true, "the file panel reopened");
});

test("a regenerated run (new identity) discards stored deck state", () => {
  const storage = memoryStorage();

  const first = loadCockpit({ sessionStorage: storage, run: "run-1" });
  click(dot(first.document, "t2.s1"));
  assert.equal(stagedStepId(first.document), "t2.s1");

  // The renderer stamps a new identity on regeneration; the stale state must not ride
  // across the clean break — the deck opens fresh at L0 stop zero.
  const second = loadCockpit({ sessionStorage: storage, run: "run-2" });
  assert.ok(second.document.querySelector(".deck-stage .l0"), "starts fresh at L0 stop zero");
  assert.equal(stagedStepId(second.document), null, "no step is staged");
});

test("a stored staged step that no longer resolves is discarded, not thrown", () => {
  const storage = memoryStorage();

  const first = loadCockpit({ sessionStorage: storage });
  click(dot(first.document, "t2.s1"));
  assert.equal(stagedStepId(first.document), "t2.s1");

  // The regenerated run drops that step; restore must ignore the dangling id and fall
  // back to stop zero rather than crash or stage nothing coherent.
  const doc2 = buildFixtureDocument();
  doc2.getElementById("t2.s1").remove();
  const second = loadCockpit({ sessionStorage: storage, doc: doc2 });
  assert.ok(second.document.querySelector(".deck-stage .l0"), "falls back to stop zero");
});

test("the store carries no dispositions (those stay server-state)", () => {
  const storage = memoryStorage();

  const first = loadCockpit({ sessionStorage: storage });
  first.window.lavish = { queuePrompt() {}, sendQueuedPrompts() {} };
  // Set a disposition from the Map, then stage a step so state is persisted.
  const step = first.document.getElementById("t2.s1");
  click(dot(first.document, "t2.s1"));
  const concern = step
    .querySelectorAll(".disposition-controls button")
    .find((b) => b.dataset.disposition === "concern");
  click(concern);
  assert.equal(step.getAttribute("data-disposition"), "concern");

  const raw = storage.getItem(STORE_KEY);
  assert.ok(raw, "state was persisted");
  assert.ok(!/disposition/i.test(raw), "the store blob never carries disposition state");

  // On reload the tint is NOT restored from the store — it would come only from a
  // fetched dispositions.json, which this reload is not given.
  const second = loadCockpit({ sessionStorage: storage });
  assert.equal(
    second.document.getElementById("t2.s1").getAttribute("data-disposition"),
    null,
    "restore does not resurrect a disposition"
  );
});

test("on file:// the store is inert — nothing is written", () => {
  const storage = memoryStorage();
  loadCockpit({ protocol: "file:", sessionStorage: storage });
  assert.equal(storage._map.size, 0, "a baked/portable record persists no deck state");
});

test("without a run identity the store is inert", () => {
  const storage = memoryStorage();
  const first = loadCockpit({ sessionStorage: storage, run: null }); // no <meta brc-run>
  click(dot(first.document, "t1.s2"));
  assert.equal(storage._map.size, 0, "no identity to key on → nothing persisted");
});

test("without sessionStorage the deck still works and nothing throws", () => {
  const { document } = loadCockpit({ sessionStorage: null });
  assert.ok(document.querySelector(".deck"), "the deck is built");
  press(document, "j");
  assert.equal(stagedStepId(document), "t1.s1", "navigation works with no store");
});
