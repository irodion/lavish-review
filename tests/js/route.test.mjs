// Core-first route tests (issue #101). These drive the vendored app.js through the DOM
// harness to prove the served deck offers a core/full route selector, that navigation and
// auto-advance sequence within the active route (core = behavior-change + unknown-impact
// steps, in Review Route order), that progress and the L0 budgets are reported per route,
// that switching routes keeps the staged step, and that the choice survives an injection
// reload (self-invalidating on a regenerated run) — all while nothing is hidden.
//
// The default fixture abridges: core = [t1.s1 (behavior-change), t1.s2 (unknown-impact)],
// full adds t2.s1 (test-change). L0 carries the renderer-stamped budgets (~2 min / ~10 min).

import { test } from "node:test";
import assert from "node:assert/strict";
import { loadCockpit, buildFixtureDocument, memoryStorage, click, press } from "./harness.mjs";

const dot = (document, stepId) =>
  document.querySelectorAll(".deck-dot").find((d) => d.dataset.step === stepId);
const stagedStepId = (document) => {
  const crumb = document.querySelector(".deck-stage .deck-crumb-step");
  return crumb ? crumb.textContent : null;
};
const routeBtn = (document, route) =>
  document.querySelectorAll(".deck-route-btn").find((b) => b.dataset.route === route);
const threadBtn = (document, threadId) =>
  document.querySelectorAll(".deck-thread").find((b) => {
    const id = b.querySelector(".deck-thread-id");
    return id && id.textContent === threadId;
  });
const tallyText = (document) => document.querySelector(".deck-tally").textContent;

// A window.lavish stub so Stage dispositions have a channel to queue to (the local tint —
// which auto-advance reads — is applied regardless, but this mirrors a live session).
function withLavish(window) {
  window.lavish = { queuePrompt() {}, sendQueuedPrompts() {} };
}

test("the Map offers a core/full route selector with the renderer's per-route budgets", () => {
  const { document } = loadCockpit();

  const selector = document.querySelector(".deck-route");
  assert.ok(selector, "the route selector is rendered on the Map");
  const names = document.querySelectorAll(".deck-route-name").map((n) => n.textContent);
  assert.deepEqual(names, ["Core", "Full"], "core is offered first");
  const budgets = document.querySelectorAll(".deck-route-budget").map((b) => b.textContent);
  assert.deepEqual(budgets, ["~2 min", "~10 min"], "each route shows its derived budget");

  // Full is the default — the deck opens on the whole route; core is opt-in.
  assert.equal(routeBtn(document, "full").getAttribute("aria-pressed"), "true");
  assert.equal(routeBtn(document, "core").getAttribute("aria-pressed"), "false");
});

test("progress reports both routes honestly, emphasising the active one", () => {
  const { document } = loadCockpit();

  assert.equal(tallyText(document), "core 0/2 · full 0/3 reviewed");
  assert.ok(
    document.querySelector(".deck-frac-full").classList.contains("active"),
    "the active (full) route is emphasised"
  );
  assert.ok(!document.querySelector(".deck-frac-core").classList.contains("active"));

  // Switching the route moves the emphasis; both counts stay visible (never masquerades).
  click(routeBtn(document, "core"));
  assert.equal(tallyText(document), "core 0/2 · full 0/3 reviewed");
  assert.ok(document.querySelector(".deck-frac-core").classList.contains("active"));
  assert.ok(!document.querySelector(".deck-frac-full").classList.contains("active"));
  assert.equal(routeBtn(document, "core").getAttribute("aria-pressed"), "true");
});

test("core route sequences J and auto-advance through only the core steps", () => {
  const { document, window } = loadCockpit();
  withLavish(window);
  click(routeBtn(document, "core"));

  // J from stop zero enters the head of the core route.
  press(document, "j");
  assert.equal(stagedStepId(document), "t1.s1", "core route starts at the first core step");

  // Disposing advances to the next unreviewed CORE step, never the non-core t2.s1.
  press(document, "l");
  assert.equal(stagedStepId(document), "t1.s2", "advanced to the second core step");

  press(document, "l");
  assert.equal(stagedStepId(document), "t1.s2", "stays put — no core step left to advance to");
  assert.match(
    document.querySelector(".deck-stage-status").textContent,
    /All core steps reviewed — nothing left to advance/,
    "the boundary names the core route and does not spill into the full route"
  );
  // t2.s1 (full-only) is genuinely untouched — the core pass never dispositioned it.
  assert.equal(document.getElementById("t2.s1").getAttribute("data-disposition"), null);
});

test("in core mode J/K step over off-route steps, and K from the head returns to stop zero", () => {
  const { document } = loadCockpit();
  click(routeBtn(document, "core"));
  press(document, "j"); // t1.s1
  press(document, "j"); // t1.s2 (last core step)
  assert.equal(stagedStepId(document), "t1.s2");

  press(document, "j"); // no further core step — clamp
  assert.equal(stagedStepId(document), "t1.s2", "J clamps at the core route's end (skips t2.s1)");

  press(document, "k"); // back to the first core step
  assert.equal(stagedStepId(document), "t1.s1");
  press(document, "k"); // no earlier core step — back to stop zero
  assert.ok(document.querySelector(".deck-stage .l0"), "K from the core head returns to stop zero");
});

test("clicking an off-route dot still stages it; K then lands back on the core route", () => {
  const { document } = loadCockpit();
  click(routeBtn(document, "core"));

  // Nothing is hidden: the non-core dot is still present and clickable, just dimmed.
  const offDot = dot(document, "t2.s1");
  assert.ok(offDot.classList.contains("off-route"), "the non-core dot is marked off-route");
  click(offDot);
  assert.equal(stagedStepId(document), "t2.s1", "an off-route step can still be staged");

  press(document, "k"); // step back into the core route
  assert.equal(stagedStepId(document), "t1.s2", "K lands on the previous core step");
});

test("off-route dots are dimmed only while the core route is active", () => {
  const { document } = loadCockpit();

  // Full route (default): no dot is off-route.
  assert.ok(!dot(document, "t2.s1").classList.contains("off-route"));

  click(routeBtn(document, "core"));
  assert.ok(dot(document, "t2.s1").classList.contains("off-route"), "the test step is off the core route");
  assert.ok(!dot(document, "t1.s1").classList.contains("off-route"), "core steps stay lit");
  assert.ok(!dot(document, "t1.s2").classList.contains("off-route"));

  click(routeBtn(document, "full"));
  assert.ok(!dot(document, "t2.s1").classList.contains("off-route"), "back to full — nothing dimmed");
});

test("in Core mode a thread heading stages the thread's first core step, not an off-route lead", () => {
  // Make t1 open with a non-core step (t1.s1) followed by a core one (t1.s2), the case the
  // heading must not mishandle: staging steps[0] blindly would drop onto the off-route lead.
  const doc = buildFixtureDocument();
  doc.getElementById("t1.s1").removeAttribute("data-core");
  const { document } = loadCockpit({ doc });

  // Full route: the heading lands on the thread's first step, exactly as before.
  click(threadBtn(document, "t1"));
  assert.equal(stagedStepId(document), "t1.s1");

  // Core route: the heading enters the active route at t1.s2 — the thread's first core
  // step — never the off-route t1.s1 ahead of it.
  click(routeBtn(document, "core"));
  click(threadBtn(document, "t1"));
  assert.equal(stagedStepId(document), "t1.s2", "thread heading enters the active route");
});

test("in Core mode a thread with no core step still navigates from its heading (fallback)", () => {
  const { document } = loadCockpit();
  click(routeBtn(document, "core"));
  // t2 is entirely non-core (its only step is a test-change). The heading falls back to the
  // thread's first step, so the thread stays reachable (off-route — nothing is hidden).
  click(threadBtn(document, "t2"));
  assert.equal(stagedStepId(document), "t2.s1");
});

test("switching routes keeps the staged step when it belongs to both", () => {
  const { document } = loadCockpit();
  press(document, "j"); // stage t1.s1 (a core step — belongs to both routes)
  assert.equal(stagedStepId(document), "t1.s1");

  click(routeBtn(document, "core"));
  assert.equal(stagedStepId(document), "t1.s1", "kept when switching to core");
  click(routeBtn(document, "full"));
  assert.equal(stagedStepId(document), "t1.s1", "kept when switching back to full");
});

test("switching to core keeps a staged non-core step (nothing is force-navigated)", () => {
  const { document } = loadCockpit();
  click(dot(document, "t2.s1")); // stage the full-only step
  assert.equal(stagedStepId(document), "t2.s1");

  click(routeBtn(document, "core"));
  assert.equal(stagedStepId(document), "t2.s1", "the staged step is not yanked away");
  assert.ok(dot(document, "t2.s1").classList.contains("off-route"), "but its dot reads as off-route");
});

test("a review that does not abridge shows no selector and a single fraction", () => {
  // Make every step core (the renderer stamps data-core on it): core === full, so there is
  // nothing to select and the deck behaves exactly as the single full route.
  const doc = buildFixtureDocument();
  doc.getElementById("t2.s1").setAttribute("data-core", "true");
  const { document } = loadCockpit({ doc });

  assert.equal(document.querySelector(".deck-route"), null, "no route selector");
  assert.equal(tallyText(document), "0/3 reviewed", "a single, unlabelled fraction");
});

test("the selector degrades to no budget sub-label on a page missing the L0 attributes", () => {
  const doc = buildFixtureDocument();
  const l0 = doc.querySelector("section.l0");
  l0.removeAttribute("data-core-budget");
  l0.removeAttribute("data-full-budget");
  const { document } = loadCockpit({ doc });

  assert.ok(document.querySelector(".deck-route"), "the selector still renders");
  assert.equal(document.querySelectorAll(".deck-route-budget").length, 0, "no budget sub-labels");
});

test("the chosen route survives an injection reload and self-invalidates on regeneration", () => {
  const storage = memoryStorage();

  const first = loadCockpit({ sessionStorage: storage });
  click(routeBtn(first.document, "core"));
  assert.equal(routeBtn(first.document, "core").getAttribute("aria-pressed"), "true");

  // Injection reload: same tab storage, same run identity → the core choice is restored.
  const second = loadCockpit({ sessionStorage: storage });
  assert.equal(routeBtn(second.document, "core").getAttribute("aria-pressed"), "true", "route restored");
  assert.ok(second.document.querySelector(".deck-frac-core").classList.contains("active"));

  // Regenerated run (new identity): the stale route must not ride across the clean break.
  const third = loadCockpit({ sessionStorage: storage, run: "run-2" });
  assert.equal(routeBtn(third.document, "full").getAttribute("aria-pressed"), "true", "back to the full default");
});

test("document mode and the route feature never touch file:// (a baked record)", () => {
  const { document } = loadCockpit({ protocol: "file:" });
  assert.equal(document.querySelector(".deck-route"), null, "no selector on a baked record");
  assert.equal(document.querySelectorAll("main details.step").length, 3, "the document is intact");
});
