// Deck keyboard flow + Stage dispositions (issue #68). These drive the vendored
// app.js through the DOM harness: the oversized V/C/Q control and its V/C/Q keys
// stage a disposition through the same write path the document-mode controls use,
// setting one auto-advances to the next *unreviewed* claim (never a reviewed one),
// J/K navigate freely, keys are ignored while a typing surface is focused, and the
// Map / Stage control / document controls stay in sync.

import { test } from "node:test";
import assert from "node:assert/strict";
import { loadCockpit, click, press, flush } from "./harness.mjs";

const dot = (document, claimId) =>
  document.querySelectorAll(".deck-dot").find((d) => d.dataset.claim === claimId);
const stagedClaimId = (document) =>
  document.querySelector(".deck-stage .deck-crumb-claim").textContent;
const controlBtn = (document, disposition) =>
  document.querySelectorAll(".deck-control-btn").find((b) => b.dataset.disposition === disposition);

// A window.lavish stub that records every queued prompt, so a test can assert the
// exact payload the Stage sends is the one the disposition bridge already expects.
function lavishSpy(window) {
  const calls = [];
  window.lavish = {
    calls,
    queuePrompt(message, options) {
      calls.push({ message, options });
    },
    sendQueuedPrompts() {},
  };
  return calls;
}

test("the Stage renders the oversized V/C/Q control below the claim's challenge questions", () => {
  const { document } = loadCockpit();
  const stage = document.querySelector(".deck-stage");

  const keys = stage.querySelectorAll(".deck-control-btn .deck-key").map((k) => k.textContent);
  assert.deepEqual(keys, ["V", "C", "Q"], "one key-hinted button per settable disposition");

  // The guardrail: the challenge questions (inside the claim card) sit above the
  // control in document order.
  const order = stage.childNodes.filter((n) => n.nodeType === 1).map((n) => n.className);
  const hostAt = order.indexOf("deck-claim-host");
  const controlAt = order.indexOf("deck-control");
  assert.ok(hostAt !== -1 && controlAt !== -1 && hostAt < controlAt, "claim card precedes the control");
  assert.ok(
    stage.querySelector(".deck-claim-host .challenge-questions"),
    "challenge questions are visible above the control"
  );
});

test("a V/C/Q key stages the disposition with the exact document-mode payload", () => {
  const { document, window } = loadCockpit();
  const calls = lavishSpy(window);
  assert.equal(stagedClaimId(document), "t1.s1");

  press(document, "v");

  const claim = document.getElementById("t1.s1");
  assert.equal(claim.getAttribute("data-disposition"), "verified", "the staged claim is set");
  // The payload is byte-identical to what the document-mode controls queue (both go
  // through sendDisposition) — the disposition bridge needs no change (issue #68).
  // JSON-normalise: the options object is built inside the VM sandbox, so its
  // prototype differs from the test realm's (deepStrictEqual would trip on that).
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0].options)), {
    tag: "choice",
    text: "disposition:verified",
    queueKey: "disposition:t1.s1",
    data: { kind: "disposition", claim: "t1.s1", disposition: "verified" },
  });
});

test("the Stage control button and its key are the same code path", () => {
  const { document, window } = loadCockpit();
  lavishSpy(window);

  click(controlBtn(document, "concern")); // t1.s1 staged on load
  assert.equal(document.getElementById("t1.s1").getAttribute("data-disposition"), "concern");
  // Setting it auto-advanced, exactly as the key does.
  assert.equal(stagedClaimId(document), "t1.s2");
});

test("setting a disposition auto-advances to the next unreviewed claim, skipping reviewed ones", () => {
  const { document, window } = loadCockpit();
  lavishSpy(window);

  // Review t1.s2 first (out of route order), so it must be skipped later.
  click(dot(document, "t1.s2"));
  press(document, "v");
  assert.equal(document.getElementById("t1.s2").getAttribute("data-disposition"), "verified");
  assert.equal(stagedClaimId(document), "t2.s1", "advanced forward to the next unreviewed");

  // Back to t1.s1 and dispose it: the next in route order (t1.s2) is reviewed, so
  // auto-advance skips it and wraps forward — but t2.s1 is unreviewed, so land there.
  click(dot(document, "t1.s1"));
  press(document, "c");
  assert.equal(stagedClaimId(document), "t2.s1", "skipped the reviewed t1.s2");

  // Everything but t2.s1 is reviewed; disposing it leaves nothing to advance to.
  press(document, "q");
  assert.equal(stagedClaimId(document), "t2.s1", "stays put with none left");
  assert.match(
    document.querySelector(".deck-stage-status").textContent,
    /nothing left to advance/i,
    "and it says so"
  );
});

test("re-selecting the active state clears to unreviewed and stays put (no advance)", () => {
  const { document, window } = loadCockpit();
  const calls = lavishSpy(window);

  press(document, "v"); // t1.s1 -> verified, advances to t1.s2
  assert.equal(stagedClaimId(document), "t1.s2");
  press(document, "k"); // back to t1.s1 (still verified)
  assert.equal(stagedClaimId(document), "t1.s1");

  press(document, "v"); // re-select the active state -> clears
  const claim = document.getElementById("t1.s1");
  assert.equal(claim.getAttribute("data-disposition"), null, "cleared back to unreviewed");
  assert.equal(stagedClaimId(document), "t1.s1", "no auto-advance on a clear");
  assert.equal(calls[calls.length - 1].options.data.disposition, "unreviewed", "the clear is sent");
});

test("J/K navigate the route freely, clamped at the ends and landing on reviewed claims too", () => {
  const { document, window } = loadCockpit();
  lavishSpy(window);

  // Review t1.s2 so J can be shown to land on it regardless of state.
  click(dot(document, "t1.s2"));
  press(document, "v"); // advances to t2.s1
  click(dot(document, "t1.s1")); // back to the top

  press(document, "k"); // already first — clamp
  assert.equal(stagedClaimId(document), "t1.s1", "K clamps at the route's start");

  press(document, "j"); // forward onto the reviewed t1.s2 (navigation is not state-gated)
  assert.equal(stagedClaimId(document), "t1.s2", "J lands on the reviewed claim");
  press(document, "j");
  assert.equal(stagedClaimId(document), "t2.s1");
  press(document, "j"); // last — clamp
  assert.equal(stagedClaimId(document), "t2.s1", "J clamps at the route's end");

  press(document, "k");
  assert.equal(stagedClaimId(document), "t1.s2", "K moves back one claim");
});

test("keys are ignored while a typing surface (the claim-ask box) is focused", () => {
  const { document, window } = loadCockpit();
  lavishSpy(window);

  const askBox = document.querySelector(".deck-stage .claim-ask-input");
  assert.ok(askBox, "the staged claim carries its ask affordance");
  assert.equal(askBox.tagName, "TEXTAREA");

  press(askBox, "v"); // a keystroke meant for the textarea
  assert.equal(
    document.getElementById("t1.s1").getAttribute("data-disposition"),
    null,
    "no disposition was staged"
  );
  assert.equal(stagedClaimId(document), "t1.s1", "and no navigation happened");
});

test("modifier chords pass through (⌘/Ctrl+key is a host shortcut, not a disposition)", () => {
  const { document, window } = loadCockpit();
  lavishSpy(window);
  press(document, "v", { metaKey: true });
  assert.equal(document.getElementById("t1.s1").getAttribute("data-disposition"), null);
});

test("keys are inert in document mode (the Stage owns them)", () => {
  const { document, window } = loadCockpit();
  lavishSpy(window);
  click(document.querySelector(".deck-toggle")); // to document mode
  assert.ok(!document.body.classList.contains("deck-active"));
  press(document, "v");
  assert.equal(document.getElementById("t1.s1").getAttribute("data-disposition"), null);
});

test("Map dots, thread fractions, and overall progress update on every change", () => {
  const { document, window } = loadCockpit();
  lavishSpy(window);

  press(document, "c"); // t1.s1 -> concern, advances to t1.s2
  assert.equal(dot(document, "t1.s1").getAttribute("data-disposition"), "concern", "Map dot tinted");
  const fracs = document.querySelectorAll(".deck-thread-frac").map((f) => f.textContent);
  assert.deepEqual(fracs, ["1/2", "0/1"], "the thread fraction ticks up");
  assert.match(document.querySelector(".deck-progress").textContent, /1\/3 reviewed/);
  assert.match(document.querySelector(".deck-progress").textContent, /⚠ 1/, "the concern tally shows");
});

test("the Stage control and the in-claim document-mode controls stay in sync", () => {
  const { document, window } = loadCockpit();
  lavishSpy(window);

  // Set via the in-claim (document-mode) control on the staged claim; the oversized
  // Stage control must reflect it (both read the one data-disposition).
  const claim = document.getElementById("t1.s1");
  const inClaimVerified = claim
    .querySelectorAll(".disposition-controls button")
    .find((b) => b.dataset.disposition === "verified");
  click(inClaimVerified);
  assert.equal(claim.getAttribute("data-disposition"), "verified");
  assert.equal(controlBtn(document, "verified").getAttribute("aria-pressed"), "true", "Stage control synced");

  // And the reverse: the Stage key marks the in-claim control pressed.
  const { document: doc2, window: win2 } = loadCockpit();
  lavishSpy(win2);
  press(doc2, "q"); // sets t1.s1 then auto-advances away — re-stage it to read its control
  click(dot(doc2, "t1.s1"));
  const inClaimQuestion = doc2
    .getElementById("t1.s1")
    .querySelectorAll(".disposition-controls button")
    .find((b) => b.dataset.disposition === "question-open");
  assert.equal(inClaimQuestion.getAttribute("aria-pressed"), "true", "in-claim control reflects the key");
});

test("restored dispositions (resume) tint the Map on load", async () => {
  const { document } = loadCockpit({ dispositions: { "t1.s1": "verified", "t2.s1": "concern" } });
  await flush(); // let loadDispositions' fetch → json → apply settle

  assert.equal(dot(document, "t1.s1").getAttribute("data-disposition"), "verified", "dot tinted from the store");
  assert.equal(dot(document, "t2.s1").getAttribute("data-disposition"), "concern");
  assert.match(document.querySelector(".deck-progress").textContent, /2\/3 reviewed/, "overall progress restored");
  // The staged claim's oversized control reflects the restored state too.
  assert.equal(controlBtn(document, "verified").getAttribute("aria-pressed"), "true");
});
