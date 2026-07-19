// Deck Presenter tests (issue #67, step-shaped by #86) — the repo's first JS tests.
// They exercise the vendored app.js against the minimal DOM harness (dom.mjs /
// harness.mjs): the deck builds from a fixture document, dots and threads stage
// steps, the mode toggle round-trips content/open-state/tints, no deck exists on
// file://, and the DOM-relocation-only invariant holds (a <script> in a diff
// renders as text).

import { test } from "node:test";
import assert from "node:assert/strict";
import { Element } from "./dom.mjs";
import { loadCockpit, click, press, buildFixtureDocument } from "./harness.mjs";

const dot = (document, stepId) =>
  document.querySelectorAll(".deck-dot").find((d) => d.dataset.step === stepId);
const stagedStepId = (document) =>
  document.querySelector(".deck-stage .deck-crumb-step").textContent;

function containsScriptElement(el) {
  if (el.tagName === "SCRIPT") return true;
  for (const child of el.childNodes) {
    if (child instanceof Element && containsScriptElement(child)) return true;
  }
  return false;
}

test("served Deck starts at L0 stop zero and J advances into the Review Route", () => {
  const { document } = loadCockpit();

  assert.ok(document.querySelector(".deck-stage .l0"), "the rendered orientation is staged first");
  assert.equal(document.querySelector("main .l0"), null, "L0 is relocated rather than duplicated");
  assert.match(document.querySelector(".deck-tally").textContent, /^0\/3 reviewed$/);

  press(document, "j");
  assert.equal(stagedStepId(document), "t1.s1", "forward navigation enters the first step");

  press(document, "k");
  assert.ok(document.querySelector(".deck-stage .l0"), "back from the first step returns to stop zero");
});

test("builds the Map and Stage from the document when served", () => {
  const { document } = loadCockpit();

  assert.ok(document.querySelector(".deck"), "the deck container is built");
  assert.ok(document.body.classList.contains("deck-active"), "served → deck is the presentation");

  // One dot per step, in Review Route order.
  const dots = document.querySelectorAll(".deck-dot").map((d) => d.dataset.step);
  assert.deepEqual(dots, ["t1.s1", "t1.s2", "t2.s1"]);

  // Threads in analysis order with their per-thread fractions.
  const titles = document.querySelectorAll(".deck-thread-title").map((t) => t.textContent);
  assert.deepEqual(titles, ["First thread", "Second thread"]);
  const fracs = document.querySelectorAll(".deck-thread-frac").map((f) => f.textContent);
  assert.deepEqual(fracs, ["0/2", "0/1"]);

  // Every changed file listed with its stats (nothing-hidden in the Map).
  const files = document.querySelectorAll(".deck-file-name").map((f) => f.textContent);
  assert.deepEqual(files, ["one.py", "two.py", "three.py"]);
  assert.equal(document.querySelectorAll(".deck-file .file-stats").length, 3);

  // Overall progress: nothing reviewed yet.
  assert.match(document.querySelector(".deck-tally").textContent, /^0\/3 reviewed$/);

  press(document, "j"); // stop zero → first Review Step
  // The first step is staged, whole, with its review prompts and the exact hunk that
  // substantiates it rendered inline (line-numbered — the annotated table).
  assert.equal(stagedStepId(document), "t1.s1");
  const stage = document.querySelector(".deck-stage");
  assert.ok(stage.querySelector("details.step .review-prompts"), "review prompts on Stage");
  assert.ok(stage.querySelector(".deck-hunk .diff-table"), "the evidence hunk is inline");
});

test("the Map reuses each thread's renderer-derived impact summary", () => {
  const { document } = loadCockpit();

  const summaries = document
    .querySelectorAll(".deck-thread .thread-impacts")
    .map((summary) => summary.textContent);
  assert.deepEqual(summaries, ["1 behavior-change · 1 unknown", "1 test"]);
  assert.ok(
    document
      .querySelectorAll(".deck-thread .thread-impacts")[0]
      .classList.contains("attention-unknown-impact"),
    "the renderer's attention class is preserved"
  );
});

test("the Map sizes each dot by its step's derived reading weight (issue #100)", () => {
  const { document } = loadCockpit();

  // The fixture weights (8 / 40 / 200 lines) fall in distinct size buckets, so a heavy
  // stop reads as a longer bar than a trivial one — the Map is no longer weight-blind.
  assert.ok(dot(document, "t1.s1").classList.contains("deck-dot--w1"), "8 lines → smallest");
  assert.ok(dot(document, "t1.s2").classList.contains("deck-dot--w2"), "40 lines → medium");
  assert.ok(dot(document, "t2.s1").classList.contains("deck-dot--w4"), "200 lines → largest");
  // Size is emphasis only — the dot's colour still carries impact, never its weight.
  assert.equal(dot(document, "t1.s1").getAttribute("data-impact"), "behavior-change");
});

test("the Map reuses each thread's renderer-derived reading weight (issue #100)", () => {
  const { document } = loadCockpit();

  const weights = document
    .querySelectorAll(".deck-thread .thread-weight")
    .map((weight) => weight.textContent);
  assert.deepEqual(weights, ["~2 min", "~8 min"], "the thread budget is cloned into the Map");
  assert.equal(
    document.querySelectorAll(".deck-thread .thread-weight")[1].getAttribute("data-weight"),
    "200",
    "the rendered data-weight rides along, not a JS re-sum"
  );
  // The weight label is stripped from the thread title (like impacts), never leaking in.
  const titles = document.querySelectorAll(".deck-thread-title").map((t) => t.textContent);
  assert.deepEqual(titles, ["First thread", "Second thread"]);
});

const WEIGHT_BUCKETS = ["deck-dot--w1", "deck-dot--w2", "deck-dot--w3", "deck-dot--w4"];

test("a step with no derived weight leaves its Map dot at the default size", () => {
  // An older page (or degraded render) may carry no data-weight; the deck must still
  // build and the dot simply takes no size bucket rather than throwing.
  const doc = buildFixtureDocument();
  doc.getElementById("t1.s1").removeAttribute("data-weight");
  const { document } = loadCockpit({ doc });

  const classes = dot(document, "t1.s1").classList;
  assert.ok(
    !WEIGHT_BUCKETS.some((c) => classes.contains(c)),
    "no weight bucket is applied without a data-weight"
  );
});

test("a malformed or negative data-weight falls back to the default dot size", () => {
  // A hand-edited or corrupt page could carry a non-numeric or negative weight; the
  // bucket mapping must reject it (never a NaN/negative size) rather than mislead.
  for (const bad of ["not-a-number", "-5", ""]) {
    const doc = buildFixtureDocument();
    doc.getElementById("t1.s1").setAttribute("data-weight", bad);
    const { document } = loadCockpit({ doc });

    const classes = dot(document, "t1.s1").classList;
    assert.ok(
      !WEIGHT_BUCKETS.some((c) => classes.contains(c)),
      `data-weight="${bad}" must not map to a size bucket`
    );
  }
});

test("clicking a dot or a thread stages that step", () => {
  const { document } = loadCockpit();

  click(dot(document, "t1.s2"));
  assert.equal(stagedStepId(document), "t1.s2");
  assert.ok(dot(document, "t1.s2").classList.contains("current"), "the staged dot is marked current");
  assert.ok(!dot(document, "t1.s1").classList.contains("current"));

  // A file-level evidence ref (#file-f2) stages with the file's body inline.
  const stage = document.querySelector(".deck-stage");
  assert.ok(stage.querySelector(".deck-hunk .diff-table"), "file-level evidence renders inline");

  // The thread button stages the thread's first step.
  const secondThread = document.querySelectorAll(".deck-thread")[1];
  click(secondThread);
  assert.equal(stagedStepId(document), "t2.s1");
});

test("a relates_to link stages its Review Step target across threads", () => {
  const { document } = loadCockpit();
  press(document, "j");

  const jump = document
    .querySelectorAll(".deck-stage .step-relations a")
    .find((anchor) => anchor.getAttribute("href") === "#t2.s1");
  click(jump);

  assert.equal(stagedStepId(document), "t2.s1");
  assert.ok(dot(document, "t2.s1").classList.contains("current"));
  assert.ok(document.body.classList.contains("deck-active"), "the jump stays in Deck Mode");
});

test("a non-step relation anchor keeps normal document reveal behavior", () => {
  const { document } = loadCockpit();
  press(document, "j");

  const anchor = document
    .querySelectorAll(".deck-stage .step-relations a")
    .find((candidate) => candidate.getAttribute("href") === "#hunk-a1");
  const event = click(anchor);

  assert.equal(stagedStepId(document), "t1.s1", "non-step anchors do not change the Stage");
  assert.equal(event.defaultPrevented, false, "normal anchor navigation is preserved");
  assert.equal(
    document.getElementById("hunk-a1").closest("details.file").open,
    true,
    "the target's document disclosure is revealed"
  );
});

test("the staged step is relocated (not duplicated) out of the document", () => {
  const { document } = loadCockpit();
  press(document, "j");
  // After entering the route, t1.s1 must live in exactly one place: the Stage.
  const matches = document.querySelectorAll("details.step").filter((c) => c.id === "t1.s1");
  assert.equal(matches.length, 1, "the step exists once");
  assert.ok(matches[0].closest(".deck-stage"), "and it is on the Stage");
});

test("the mode toggle round-trips content, open state, and disposition tints", () => {
  const { document, window } = loadCockpit();
  window.lavish = { queuePrompt() {}, sendQueuedPrompts() {} };

  // Give t2.s1 a disposition from the Stage, then read its Map dot tint.
  click(dot(document, "t2.s1"));
  const step = document.getElementById("t2.s1");
  const concern = step
    .querySelectorAll(".disposition-controls button")
    .find((b) => b.dataset.disposition === "concern");
  click(concern);
  assert.equal(step.getAttribute("data-disposition"), "concern");
  assert.equal(dot(document, "t2.s1").getAttribute("data-disposition"), "concern");
  assert.match(document.querySelector(".deck-progress").textContent, /1\/3 reviewed/);

  // Authored steps are closed; the staged one is forced open on the Stage.
  assert.equal(step.open, true, "staged step is open on the Stage");

  // Toggle to the document: the deck deactivates and the step returns home, closed.
  click(document.querySelector(".deck-toggle"));
  assert.ok(!document.body.classList.contains("deck-active"), "document mode is showing");
  assert.equal(document.querySelector(".deck-toggle").textContent, "Deck view");
  assert.equal(step.closest("section.thread").id, "t2", "the step is back under its thread");
  assert.equal(step.open, false, "its authored (closed) open state is restored");
  assert.equal(step.getAttribute("data-disposition"), "concern", "its tint survives the round-trip");

  // The document's own per-thread progress reflects the Stage-set disposition — it
  // could not update while the step was relocated onto the Stage, so unstaging must
  // recompute it (else document mode would read a stale "0/1 reviewed").
  assert.equal(
    document.getElementById("t2").querySelector(".thread-progress").textContent,
    "1/1 reviewed · 1 concern",
    "document thread-progress is refreshed on unstage"
  );

  // Every step is present in the document again — nothing lost.
  assert.equal(document.querySelectorAll("main details.step").length, 3);

  // Toggle back: the deck returns to the last-staged step.
  click(document.querySelector(".deck-toggle"));
  assert.ok(document.body.classList.contains("deck-active"));
  assert.equal(stagedStepId(document), "t2.s1");

  // The static file rail is cached and re-appended across every re-render, not lost.
  assert.equal(document.querySelectorAll(".deck-file-name").length, 3);
});

test("the mode toggle round-trips L0 stop zero without duplicating it", () => {
  const { document } = loadCockpit();
  const orientation = document.querySelector(".deck-stage .l0");

  click(document.querySelector(".deck-toggle"));
  assert.equal(document.querySelector("main .l0"), orientation, "document mode restores L0 home");

  click(document.querySelector(".deck-toggle"));
  assert.equal(
    document.querySelector(".deck-stage .l0"),
    orientation,
    "returning to Deck restores the same staged stop"
  );
  assert.equal(document.querySelectorAll("section.l0").length, 1, "L0 is never duplicated");
});

test("no deck is built on file:// (a baked record renders document mode only)", () => {
  const { document } = loadCockpit({ protocol: "file:" });
  assert.equal(document.querySelector(".deck"), null, "no deck container");
  assert.equal(document.querySelector(".deck-toggle"), null, "no mode toggle");
  assert.ok(!document.body.classList.contains("deck-active"), "the document renders as today");
  // The document itself is untouched — all steps still in place.
  assert.equal(document.querySelectorAll("main details.step").length, 3);
});

test("inline evidence is cloned without ids, so the live DOM keeps unique ids", () => {
  const { document } = loadCockpit();
  const ids = [];
  (function walk(el) {
    if (el.id) ids.push(el.id);
    for (const child of el.childNodes) if (child instanceof Element) walk(child);
  })(document.documentElement);
  assert.equal(new Set(ids).size, ids.length, "no id is duplicated by a cloned hunk");
});

test("DOM-relocation-only: a <script> in a diff renders as text, never executes", () => {
  globalThis.__pwned = undefined;
  const { document } = loadCockpit();
  press(document, "j");

  // t1.s1 cites a hunk whose diff line embeds a <script> string.
  const stage = document.querySelector(".deck-stage");
  const inlineHunk = stage.querySelector(".deck-hunk");
  assert.ok(
    inlineHunk.textContent.includes("<script>window.__pwned = true;</script>"),
    "the hostile diff line is present verbatim, as text"
  );
  assert.ok(
    !containsScriptElement(document.querySelector(".deck")),
    "no <script> element was ever constructed in the deck"
  );
  assert.notEqual(globalThis.__pwned, true, "no side effect fired — the payload never executed");
  delete globalThis.__pwned;
});
