// Tickable review prompts (issue #99). Working a Review Step should feel like a
// sequence of micro-completions, so each review_prompt renders as its own checklist
// item with a tick affordance — in document mode and on the Deck Stage. These tests
// drive the vendored app.js against the DOM harness to prove that:
//   * each prompt is individually tickable, and a tick round-trips the mode toggle;
//   * a tick is EPHEMERAL served-session UI state — never a Reviewer Disposition,
//     never queued through the feedback channel, never influencing progress counts
//     or auto-advance, and carried across an injection reload by the UI store only;
//   * on file:// (a baked/portable record) the prompts stay plain list items with no
//     interactive tick control.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  loadCockpit,
  buildFixtureDocument,
  memoryStorage,
  click,
  promptTick,
  promptTicked,
  tickPrompt,
} from "./harness.mjs";

const stagedStepId = (document) => {
  const crumb = document.querySelector(".deck-stage .deck-crumb-step");
  return crumb ? crumb.textContent : null;
};
const dot = (document, stepId) =>
  document.querySelectorAll(".deck-dot").find((d) => d.dataset.step === stepId);
const STORE_KEY = "brc:ui:/review.html";

// A fixture whose t1.s1 carries a second, independent review prompt — for proving
// ticks are per-prompt, not per-step (the default fixture gives each step one prompt).
function twoPromptDocument() {
  const doc = buildFixtureDocument();
  const ul = doc.getElementById("t1.s1").querySelector(".review-prompts");
  const li = doc.createElement("li");
  li.appendChild(doc.createTextNode("A second, independent comparison."));
  ul.appendChild(li);
  return doc;
}

test("each review prompt gets a tick affordance that toggles", () => {
  const { document } = loadCockpit();

  const btn = promptTick(document, "t1.s1", 0);
  assert.ok(btn, "a tick control is injected on the prompt");
  assert.equal(btn.getAttribute("aria-pressed"), "false", "it starts unticked");

  click(btn);
  assert.ok(promptTicked(document, "t1.s1", 0), "clicking ticks the prompt");
  assert.equal(btn.getAttribute("aria-pressed"), "true", "aria-pressed reflects the tick");

  click(btn);
  assert.ok(!promptTicked(document, "t1.s1", 0), "clicking again unticks it");
  assert.equal(btn.getAttribute("aria-pressed"), "false");
});

test("prompts are ticked individually, not per step", () => {
  const { document } = loadCockpit({ doc: twoPromptDocument() });

  tickPrompt(document, "t1.s1", 0);
  assert.ok(promptTicked(document, "t1.s1", 0), "the first prompt is ticked");
  assert.ok(!promptTicked(document, "t1.s1", 1), "its sibling prompt stays unticked");
  assert.ok(!promptTicked(document, "t1.s2", 0), "a prompt on another step is untouched");
});

test("a tick survives the deck ↔ document mode toggle", () => {
  const { document } = loadCockpit();
  // Deck mode opens with t1.s1 on the Stage; tick its prompt there.
  assert.ok(document.body.classList.contains("deck-active"), "starts in deck mode");
  tickPrompt(document, "t1.s1", 0);
  assert.ok(promptTicked(document, "t1.s1", 0));

  // → document mode: the step relocates home, carrying its tick with it.
  click(document.querySelector(".deck-toggle"));
  assert.ok(!document.body.classList.contains("deck-active"), "now in document mode");
  assert.ok(promptTicked(document, "t1.s1", 0), "the tick survived the trip to the document");

  // → back to the deck: still ticked.
  click(document.querySelector(".deck-toggle"));
  assert.ok(document.body.classList.contains("deck-active"), "back in deck mode");
  assert.ok(promptTicked(document, "t1.s1", 0), "and it is still ticked on the Stage");
});

test("a tick made in document mode shows when its step is staged", () => {
  const { document } = loadCockpit();
  click(document.querySelector(".deck-toggle")); // → document mode
  assert.ok(!document.body.classList.contains("deck-active"));

  tickPrompt(document, "t1.s2", 0); // a step that is not the current Stage stop
  assert.ok(promptTicked(document, "t1.s2", 0));

  // → deck, then stage t1.s2 so its <details> physically relocates onto the Stage.
  click(document.querySelector(".deck-toggle"));
  click(dot(document, "t1.s2"));
  assert.equal(stagedStepId(document), "t1.s2", "t1.s2 is now on the Stage");
  assert.ok(
    promptTicked(document, "t1.s2", 0),
    "the document-mode tick rode onto the relocated Stage node"
  );
});

test("ticking is independent of dispositions, progress, and auto-advance", () => {
  const { document, window } = loadCockpit();
  let queued = 0;
  window.lavish = {
    queuePrompt() {
      queued++;
    },
    sendQueuedPrompts() {},
  };

  const step = document.getElementById("t1.s1");
  click(dot(document, "t1.s1")); // stage t1.s1 so auto-advance would be observable
  assert.equal(stagedStepId(document), "t1.s1", "t1.s1 is staged");

  tickPrompt(document, "t1.s1", 0);

  assert.equal(queued, 0, "a tick sends nothing through the feedback channel");
  assert.equal(step.getAttribute("data-disposition"), null, "a tick sets no disposition");
  assert.equal(stagedStepId(document), "t1.s1", "a tick does not auto-advance the Stage");
  // The thread heading keeps its progress span even while a step is staged away from it.
  const progress = document.getElementById("t1").querySelector(".thread-progress");
  assert.match(progress.textContent, /^0\/2 reviewed/, "a tick is not counted as reviewed");
});

test("setting a disposition does not tick prompts (and vice versa)", () => {
  const { document, window } = loadCockpit();
  window.lavish = { queuePrompt() {}, sendQueuedPrompts() {} };

  const step = document.getElementById("t1.s1");
  const looks = step
    .querySelectorAll(".disposition-controls button")
    .find((b) => b.dataset.disposition === "looks-right");
  click(looks);
  assert.equal(step.getAttribute("data-disposition"), "looks-right");
  assert.ok(!promptTicked(document, "t1.s1", 0), "a disposition does not tick the prompt");
});

test("on file:// the prompts are plain list items with no tick control", () => {
  const { document } = loadCockpit({ protocol: "file:" });
  const items = document.getElementById("t1.s1").querySelectorAll(".review-prompts li");
  assert.ok(items.length > 0, "the prompts still render");
  for (const li of items) {
    assert.equal(li.querySelector(".prompt-tick"), null, "no interactive tick control on file://");
  }
});

test("a tick survives an injection reload via the UI store", () => {
  const storage = memoryStorage();

  const first = loadCockpit({ sessionStorage: storage });
  tickPrompt(first.document, "t1.s1", 0);
  assert.ok(promptTicked(first.document, "t1.s1", 0));

  // Injection reload: fresh document, same tab storage, same run identity.
  const second = loadCockpit({ sessionStorage: storage });
  assert.ok(promptTicked(second.document, "t1.s1", 0), "the tick was restored after the reload");
});

test("a stored tick index that no longer resolves is ignored, not thrown", () => {
  const storage = memoryStorage();

  // First run: t1.s1 carries two prompts; tick both, persisting indices [0, 1].
  const first = loadCockpit({ sessionStorage: storage, doc: twoPromptDocument() });
  tickPrompt(first.document, "t1.s1", 0);
  tickPrompt(first.document, "t1.s1", 1);
  assert.deepEqual(JSON.parse(storage.getItem(STORE_KEY)).ticks["t1.s1"], [0, 1]);

  // Injection reload against a document whose t1.s1 has only one prompt — the stored
  // index 1 is now out of range. Restore must tick 0 and quietly drop 1 (loadCockpit
  // runs restoreUiState; a missing `items[index]` guard would throw here).
  const second = loadCockpit({ sessionStorage: storage });
  assert.ok(promptTicked(second.document, "t1.s1", 0), "the resolvable index is restored");
  assert.equal(promptTick(second.document, "t1.s1", 1), null, "the out-of-range index isn't there");
});

test("the tick store blob never carries disposition state", () => {
  const storage = memoryStorage();
  const first = loadCockpit({ sessionStorage: storage });
  tickPrompt(first.document, "t1.s1", 0);

  const raw = storage.getItem(STORE_KEY);
  assert.ok(raw, "a tick is persisted");
  const stored = JSON.parse(raw);
  assert.deepEqual(
    stored.ticks["t1.s1"],
    [0],
    "the store carries the ticked prompt's own index, not just the key name"
  );
  assert.ok(!/disposition/i.test(raw), "but never any disposition state");
});

test("a regenerated run (new identity) discards stored ticks", () => {
  const storage = memoryStorage();

  const first = loadCockpit({ sessionStorage: storage, run: "run-1" });
  tickPrompt(first.document, "t1.s1", 0);
  assert.ok(promptTicked(first.document, "t1.s1", 0));

  const second = loadCockpit({ sessionStorage: storage, run: "run-2" });
  assert.ok(!promptTicked(second.document, "t1.s1", 0), "stale ticks do not cross the clean break");
});
