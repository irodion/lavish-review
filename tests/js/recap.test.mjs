// Resume recap card tests (issue #102). When a reviewer RESUMES a served session — a
// new sitting on a review they already worked, not a first open and not an injection
// reload mid-sitting — the presenter stages a deterministic "previously on…" recap
// before the route: coverage so far (per thread), their concerns and follow-ups (each
// linked to its step), the next unreviewed stop in Review Route order, and a cumulative
// count of their answered questions. These tests drive app.js against the DOM harness to
// prove that derivation, its gating, and its escape discipline.
//
// The gate is the explicit resume signal — session.json `resume_seq`, bumped by the
// resume lifecycle. The recap stages only when the server's seq has advanced beyond what
// this tab has acknowledged (remembered in the run-scoped UI store): a same-tab reload
// that follows a /review-resume (seq bumped) shows the card, a mid-review injection reload
// (same seq) does not. A fresh tab has acknowledged nothing, so it shows the card for any
// prior work. A working store is still required to remember the acknowledged seq, so
// file://, no run identity, and no sessionStorage all fail safe to no recap.

import { test } from "node:test";
import assert from "node:assert/strict";
import { loadCockpit, buildFixtureDocument, memoryStorage, click, flush } from "./harness.mjs";

// The fixture is two threads — t1 (t1.s1, t1.s2) and t2 (t2.s1) — so the Review Route in
// order is [t1.s1, t1.s2, t2.s1]; three steps total.
const recap = (document) => document.querySelector(".deck-recap");
const text = (document, selector) => {
  const el = document.querySelector(selector);
  return el ? el.textContent : null;
};
const stagedStepId = (document) => {
  const crumb = document.querySelector(".deck-stage .deck-crumb-step");
  return crumb ? crumb.textContent : null;
};
const answered = (n) => Array.from({ length: n }, (_v, i) => ({ seq: i + 1, ts: "t", feedback_raw: "q", answer: "a" }));

test("a resume with prior dispositions stages the recap before the route", async () => {
  const { document } = loadCockpit({
    dispositions: { "t1.s1": "looks-right", "t1.s2": "concern" },
    sessionStorage: memoryStorage(),
  });
  await flush();

  assert.ok(recap(document), "the recap is staged on a resume");
  assert.match(text(document, ".deck-recap-coverage"), /2 of 3 steps/, "overall coverage");
  // The concern is listed and links to its step.
  const concernLink = document.querySelector('.deck-recap-list a[href="#t1.s2"]');
  assert.ok(concernLink, "the concern links to its step");
  // The next unreviewed stop in Review Route order is t2.s1 (t1.s1/t1.s2 are disposed).
  assert.equal(text(document, ".deck-recap-continue-step"), "t2.s1", "next unreviewed stop");
});

test("per-thread coverage reflects each thread's disposed steps", async () => {
  const { document } = loadCockpit({
    dispositions: { "t1.s1": "looks-right" }, // 1 of t1's 2; 0 of t2's 1
    sessionStorage: memoryStorage(),
  });
  await flush();

  const rows = document.querySelectorAll(".deck-recap-threads li").map((li) => li.textContent);
  assert.ok(
    rows.some((r) => r.includes("t1") && r.includes("1/2")),
    "t1 shows 1/2 reviewed"
  );
  assert.ok(
    rows.some((r) => r.includes("t2") && r.includes("0/1")),
    "t2 shows 0/1 reviewed"
  );
});

test("the answered-question count comes from the Q&A Log (qa.jsonl)", async () => {
  const { document } = loadCockpit({
    dispositions: { "t1.s1": "looks-right" },
    qaLog: [
      { seq: 1, ts: "t", feedback_raw: "…", answer: "yes" },
      { seq: 2, ts: "t", feedback_raw: "…", answer: "because X" },
      { seq: 3, ts: "t", feedback_raw: "…", answer: "" }, // no answer delivered → not counted
    ],
    sessionStorage: memoryStorage(),
  });
  await flush();

  assert.match(
    text(document, ".deck-recap-answered"),
    /2 questions answered so far/,
    "counts only records that carry an answer"
  );
});

test("answered questions alone (no dispositions) still warrant a recap", async () => {
  const { document } = loadCockpit({
    qaLog: answered(1),
    sessionStorage: memoryStorage(),
  });
  await flush();

  assert.ok(recap(document), "a review with answers but no dispositions still recaps");
  assert.match(text(document, ".deck-recap-coverage"), /0 of 3 steps/, "nothing reviewed yet");
  assert.match(text(document, ".deck-recap-answered"), /1 question answered so far/, "singular phrasing");
  assert.equal(text(document, ".deck-recap-continue-step"), "t1.s1", "next stop is the route head");
});

test("a first open — no dispositions, no answered questions — shows no recap", async () => {
  const { document } = loadCockpit({ sessionStorage: memoryStorage() });
  await flush();

  assert.equal(recap(document), null, "no prior work → no recap");
  assert.ok(document.querySelector(".deck-stage .l0"), "the deck opens at L0 stop zero instead");
});

test("an injection reload mid-sitting shows no recap", async () => {
  const storage = memoryStorage();
  const priors = { dispositions: { "t1.s1": "concern" }, sessionStorage: storage };

  const first = loadCockpit(priors);
  await flush();
  assert.ok(recap(first.document), "the recap shows on the first load of this tab");

  // A second load against the same tab storage, with resume_seq unchanged, models the
  // host's SSE reload after a live-evidence injection — no resume happened, so no recap.
  const second = loadCockpit(priors);
  await flush();
  assert.equal(recap(second.document), null, "no recap when the resume signal has not advanced");
});

test("a same-tab reload after a resume re-stages the recap (explicit resume signal)", async () => {
  const storage = memoryStorage();
  const priors = { dispositions: { "t1.s1": "concern" }, sessionStorage: storage };

  // First load of this tab (resume_seq 0): the recap shows, and the tab records that it
  // has acknowledged signal 0.
  const first = loadCockpit({ ...priors, resumeSeq: 0 });
  await flush();
  assert.ok(recap(first.document), "the recap shows on the first load");

  // A mid-review injection reload (still resume_seq 0) must NOT re-show it.
  const second = loadCockpit({ ...priors, resumeSeq: 0 });
  await flush();
  assert.equal(recap(second.document), null, "an injection reload with no resume shows nothing");

  // The reviewer runs /review-resume (session.py resume → resume_seq 1) and reloads the
  // SAME tab: the advanced signal re-stages the recap — the same-tab reload the marker fixes.
  const third = loadCockpit({ ...priors, resumeSeq: 1 });
  await flush();
  assert.ok(recap(third.document), "an advanced resume signal re-stages the recap after a same-tab reload");
});

test("the baked/portable record (file://) never shows a recap", async () => {
  const { document } = loadCockpit({
    protocol: "file:",
    dispositions: { "t1.s1": "concern" },
    qaLog: answered(2),
    sessionStorage: memoryStorage(),
  });
  await flush();

  assert.equal(recap(document), null, "a record is not a review surface");
});

test("without a working UI store there is no recap", async () => {
  // No sessionStorage → no run-scoped store → a new sitting cannot be told from a
  // reload, so the recap fails safe to nothing (this also guards the restored-tint path,
  // which passes dispositions without a store).
  const { document } = loadCockpit({ dispositions: { "t1.s1": "concern" } });
  await flush();

  assert.equal(recap(document), null, "no store → no recap");
});

test("Continue stages the next unreviewed step and dismisses the recap", async () => {
  const { document } = loadCockpit({
    dispositions: { "t1.s1": "looks-right" },
    sessionStorage: memoryStorage(),
  });
  await flush();

  assert.equal(text(document, ".deck-recap-continue-step"), "t1.s2", "next unreviewed is t1.s2");
  click(document.querySelector(".deck-recap-continue"));
  assert.equal(stagedStepId(document), "t1.s2", "staged the next unreviewed step");
  assert.equal(recap(document), null, "the recap is cleared once the reviewer continues");
});

test("clicking a concern link stages that step", async () => {
  const { document } = loadCockpit({
    dispositions: { "t1.s2": "concern" },
    sessionStorage: memoryStorage(),
  });
  await flush();

  click(document.querySelector('.deck-recap-list a[href="#t1.s2"]'));
  assert.equal(stagedStepId(document), "t1.s2", "the concern link jumps into the route at its step");
});

test("all steps reviewed and nothing un-narrated → no next stop, and it says so", async () => {
  const doc = buildFixtureDocument();
  doc.getElementById("unnarrated-changes").remove(); // full coverage — no tail to walk either
  const { document } = loadCockpit({
    doc,
    dispositions: { "t1.s1": "looks-right", "t1.s2": "follow-up", "t2.s1": "skipped" },
    sessionStorage: memoryStorage(),
  });
  await flush();

  assert.ok(recap(document), "a fully-reviewed resume still recaps");
  assert.match(text(document, ".deck-recap-coverage"), /3 of 3 steps/, "full coverage");
  assert.equal(document.querySelector(".deck-recap-continue-step"), null, "no next-step chip");
  assert.ok(document.querySelector(".deck-recap-done"), "states every step is reviewed");
  assert.equal(document.querySelector(".deck-recap-tail"), null, "no tail line when the diff is fully narrated");
  // The follow-up is still surfaced with its link even when nothing is left to review.
  assert.ok(document.querySelector('.deck-recap-list a[href="#t1.s2"]'), "the follow-up is linked");
});

test("a hostile step title renders as inert text (the escape boundary holds)", async () => {
  const doc = buildFixtureDocument();
  // Append a script-looking string to a step's summary as a text node — if the recap
  // ever built markup from it, it would become an element; it must stay characters.
  doc.getElementById("t1.s2").querySelector("summary").appendChild(
    doc.createTextNode(" <script>alert(1)</script>")
  );
  const { document } = loadCockpit({
    doc,
    dispositions: { "t1.s2": "concern" },
    sessionStorage: memoryStorage(),
  });
  await flush();

  const link = document.querySelector('.deck-recap-list a[href="#t1.s2"]');
  assert.ok(link, "the concern is listed");
  assert.match(link.textContent, /<script>alert\(1\)<\/script>/, "the hostile title survives as text");
  assert.equal(recap(document).querySelectorAll("script").length, 0, "never a live element");
});

test("the recap counts questions but never renders their text", async () => {
  const { document } = loadCockpit({
    dispositions: { "t1.s1": "looks-right" },
    qaLog: [{ seq: 1, ts: "t", feedback_raw: "prompts[1]{prompt}:\n  is this a SECRET question?", answer: "a" }],
    sessionStorage: memoryStorage(),
  });
  await flush();

  assert.match(text(document, ".deck-recap-answered"), /1 question answered so far/);
  assert.ok(!recap(document).textContent.includes("SECRET"), "the reviewer's question text is never shown");
});

test("disposition acks in the Q&A Log are not counted as answered questions", async () => {
  // A disposition click lands a qa.jsonl record with a "Recorded." ack (SKILL.md step 8b);
  // Python stamps it disposition_only so the recap excludes it — only real questions count.
  const { document } = loadCockpit({
    dispositions: { "t1.s1": "concern" },
    qaLog: [
      { seq: 1, ts: "t", feedback_raw: "d", answer: "Recorded.", disposition_only: true },
      { seq: 2, ts: "t", feedback_raw: "d", answer: "Recorded.", disposition_only: true },
      { seq: 3, ts: "t", feedback_raw: "q", answer: "the cap bounds at 30s" }, // a real question
    ],
    sessionStorage: memoryStorage(),
  });
  await flush();

  assert.match(
    text(document, ".deck-recap-answered"),
    /1 question answered so far/,
    "the two disposition acks are excluded; only the real question counts"
  );
});

test("a resume whose Q&A is only disposition acks shows no answered line", async () => {
  const { document } = loadCockpit({
    dispositions: { "t1.s1": "looks-right" },
    qaLog: [{ seq: 1, ts: "t", feedback_raw: "d", answer: "Recorded.", disposition_only: true }],
    sessionStorage: memoryStorage(),
  });
  await flush();

  assert.ok(recap(document), "the recap still shows — a disposition was set");
  assert.equal(document.querySelector(".deck-recap-answered"), null, "acks alone contribute no answered line");
});

test("a reviewer action during the fetch window is not clobbered by the recap", async () => {
  const { document } = loadCockpit({
    dispositions: { "t1.s1": "concern" },
    sessionStorage: memoryStorage(),
  });
  // Before the recap's state fetches resolve, the reviewer switches to the document view.
  click(document.querySelector(".deck-toggle"));
  assert.ok(!document.body.classList.contains("deck-active"), "switched to document view");

  await flush(); // the recap's fetches resolve now
  assert.ok(!document.body.classList.contains("deck-active"), "the reviewer's document choice stands");
  assert.equal(recap(document), null, "no recap is staged over an action taken mid-fetch");
});

test("a Core/Full route switch during the fetch window is not clobbered by the recap", async () => {
  const { document } = loadCockpit({
    dispositions: { "t1.s1": "concern" },
    sessionStorage: memoryStorage(),
  });
  // The fixture abridges, so the route selector is offered. Switching to Core changes
  // deck.route without touching mode or stop — the guard must catch that axis too.
  const coreBtn = document.querySelectorAll(".deck-route-btn").find((b) => b.dataset.route === "core");
  assert.ok(coreBtn, "the route selector is present at stop zero");
  click(coreBtn);

  await flush(); // the recap's fetches resolve now
  assert.equal(recap(document), null, "no recap is staged over a route switch taken mid-fetch");
});
