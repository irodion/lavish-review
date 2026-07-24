// Un-narrated act — the route's final act (issue #105). After the last Review Step,
// forward navigation walks the bare hunks no step anchors (the generated Un-narrated
// changes queue, issue #104), one at a time with their file for context. These drive the
// vendored app.js through the DOM harness: entry from the last step, J/K within the tail
// and the clean return to the last step, the Map's session-scoped "tail walked" progress
// (kept out of the step counts), the absence of any disposition control on a bare hunk, a
// lossless mode-toggle round-trip, and persistence across an injection reload keyed by the
// run identity (issue #112). A bare hunk is NOT a Review Step — it never carries a
// disposition or a step-scoped ask; a question about it goes through branch-scoped chat.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  loadCockpit,
  buildFixtureDocument,
  memoryStorage,
  click,
  press,
  flush,
  h,
  filePanel,
} from "./harness.mjs";

const STORE_KEY = "brc:ui:/review.html";

const tailNote = (document) => document.querySelector(".deck-stage .deck-tail-note");
const crumbText = (document, cls) => {
  const el = document.querySelector(".deck-stage ." + cls);
  return el ? el.textContent : null;
};
const actDot = (document, anchor) =>
  document.querySelectorAll(".deck-act-dot").find((d) => d.dataset.hunk === anchor);

// Add a second bare hunk (in a second file) to the default fixture, reusing the harness's
// own builders (filePanel for the L3 panel, h for the queue entry) so the shapes stay
// identical to buildFixtureDocument. Built before DOMContentLoaded so buildTailHunks reads
// the queue and annotateDiff annotates the diff. Returns the doc for loadCockpit.
function withSecondBareHunk(doc = buildFixtureDocument()) {
  const l3 = doc.createElement("section");
  l3.appendChild(
    filePanel(doc, {
      id: "file-f4",
      path: "src/four.py",
      added: 2,
      deleted: 0,
      hunkId: "hunk-c0",
      diffText: "@@ -1,0 +1,2 @@\n+    added_four = True\n",
    })
  );
  doc.querySelector("main").appendChild(l3);

  doc.getElementById("unnarrated-changes").appendChild(
    h(doc, "div.unnarrated-file", null, [
      h(doc, "p.unnarrated-file-head", null, [
        h(doc, "a", { href: "#file-f4" }, ["src/four.py"]),
        " — 1 un-narrated hunk",
      ]),
      h(doc, "ul.unnarrated-hunks", null, [
        h(doc, "li", null, [h(doc, "a", { href: "#hunk-c0" }, ["hunk 1"])]),
      ]),
    ])
  );
  return doc;
}

// Walk the default route (t1.s1 → t1.s2 → t2.s1) to the last step, then one more J.
function enterTail(document) {
  press(document, "j"); // L0 → t1.s1
  press(document, "j"); // t1.s2
  press(document, "j"); // t2.s1 (last step)
  press(document, "j"); // → the un-narrated act
}

test("forward past the last step enters the un-narrated act, staged with file context", () => {
  const { document } = loadCockpit();
  enterTail(document);

  assert.ok(tailNote(document), "the bare hunk is staged with its not-a-step note");
  assert.equal(crumbText(document, "deck-crumb-act"), "Un-narrated changes", "the act is named");
  assert.equal(crumbText(document, "deck-crumb-title"), "src/two.py", "its file gives context");
  assert.equal(crumbText(document, "deck-crumb-hunk"), "hunk 1", "the hunk label rides the crumb");

  // The hunk itself is cloned inline (the annotated diff table), exactly like step evidence.
  assert.ok(
    document.querySelector(".deck-stage .deck-hunk .diff-table"),
    "the bare hunk's diff renders inline"
  );
});

test("a staged bare hunk carries NO disposition control and NO step-scoped ask", () => {
  const { document, window } = loadCockpit();
  const calls = [];
  window.lavish = { calls, queuePrompt: (m, o) => calls.push({ m, o }), sendQueuedPrompts() {} };
  enterTail(document);

  const stage = document.querySelector(".deck-stage");
  assert.equal(stage.querySelector(".deck-control"), null, "no oversized L/C/F/S control");
  assert.equal(stage.querySelector(".deck-step-host"), null, "no relocated step card");
  assert.equal(stage.querySelector(".step-ask-input"), null, "no step-scoped ask box");

  // The disposition keys are inert on a bare hunk — nothing to judge, nothing sent.
  press(document, "l");
  press(document, "c");
  press(document, "f");
  press(document, "s");
  assert.ok(tailNote(document), "still on the bare hunk — a disposition key did nothing");
  assert.equal(calls.length, 0, "no disposition was queued for a bare hunk");
});

test("the Map shows the act with session-scoped tail-walked progress, out of the step counts", () => {
  const { document } = loadCockpit();

  // Built into the Map from the start (before the reviewer walks it): frac 0/1, no visited.
  assert.ok(document.querySelector(".deck-act"), "the un-narrated act rides the Map");
  assert.equal(document.querySelector(".deck-act-title").textContent, "Un-narrated changes");
  assert.equal(document.querySelector(".deck-act-note").textContent, "tail walked", "steps are 'reviewed'; the tail is 'walked'");
  assert.equal(document.querySelector(".deck-act-frac").textContent, "0/1", "nothing walked yet");
  assert.ok(!actDot(document, "hunk-b0").classList.contains("visited"), "its dot is unwalked");

  const tallyBefore = document.querySelector(".deck-tally").textContent;
  enterTail(document);

  // Walking the hunk ticks the tail progress — and leaves the step tally untouched.
  assert.equal(document.querySelector(".deck-act-frac").textContent, "1/1", "the walked hunk counts");
  assert.ok(actDot(document, "hunk-b0").classList.contains("visited"), "its dot fills as visited");
  assert.ok(actDot(document, "hunk-b0").classList.contains("current"), "and reads as the current stop");
  assert.equal(
    document.querySelector(".deck-tally").textContent,
    tallyBefore,
    "the tail is excluded from the step-review counts"
  );
  assert.equal(tallyBefore, "core 0/2 · full 0/3 reviewed", "and the step tally still speaks only of steps");
});

test("J/K walk the bare hunks; K from the first returns to the last step; J clamps at the last", () => {
  const { document } = loadCockpit({ doc: withSecondBareHunk() });

  enterTail(document); // → the first bare hunk (src/two.py hunk 1)
  assert.equal(crumbText(document, "deck-crumb-title"), "src/two.py");

  press(document, "j"); // → the second bare hunk (src/four.py hunk 1)
  assert.equal(crumbText(document, "deck-crumb-title"), "src/four.py", "J walks to the next bare hunk");
  assert.equal(document.querySelector(".deck-act-frac").textContent, "2/2", "both hunks now walked");

  press(document, "j"); // clamp — nothing beyond the tail
  assert.equal(crumbText(document, "deck-crumb-title"), "src/four.py", "J clamps at the last bare hunk");

  press(document, "k"); // → back to the first bare hunk
  assert.equal(crumbText(document, "deck-crumb-title"), "src/two.py", "K walks back one bare hunk");

  press(document, "k"); // → back to the last step of the route
  assert.equal(
    document.querySelector(".deck-stage .deck-crumb-step").textContent,
    "t2.s1",
    "K from the first bare hunk returns to the last step"
  );
});

test("clicking the act (and its dots) stages bare hunks directly", () => {
  const { document } = loadCockpit({ doc: withSecondBareHunk() });

  click(document.querySelector(".deck-act")); // the act button stages the first bare hunk
  assert.ok(tailNote(document), "the act button enters the tail");
  assert.equal(crumbText(document, "deck-crumb-title"), "src/two.py");

  click(actDot(document, "hunk-c0")); // a dot stages that specific bare hunk
  assert.equal(crumbText(document, "deck-crumb-title"), "src/four.py", "the dot stages its hunk");
});

test("the mode-toggle round-trip is lossless: document is whole, and it returns to the bare hunk", () => {
  const { document } = loadCockpit();
  enterTail(document);
  assert.ok(tailNote(document), "on a bare hunk");

  click(document.querySelector(".deck-toggle")); // → document mode
  assert.ok(!document.body.classList.contains("deck-active"), "document mode is showing");
  // The document is whole: the L3 hunk (cloned, never relocated) and the queue are intact.
  assert.ok(document.getElementById("hunk-b0"), "the L3 hunk stayed in the document");
  assert.ok(document.getElementById("unnarrated-changes"), "the un-narrated queue is intact");

  click(document.querySelector(".deck-toggle")); // → back to the deck
  assert.ok(document.body.classList.contains("deck-active"), "deck mode restored");
  assert.ok(tailNote(document), "the toggle returns to the bare hunk, not a step");
  assert.equal(crumbText(document, "deck-crumb-hunk"), "hunk 1", "the same bare hunk");
});

test("the tail stop and walked progress survive an injection reload", () => {
  const storage = memoryStorage();

  const first = loadCockpit({ sessionStorage: storage });
  enterTail(first.document);
  assert.ok(tailNote(first.document), "walked to a bare hunk before the reload");

  // Injection reload: fresh document, same tab storage, same run identity.
  const second = loadCockpit({ sessionStorage: storage });
  assert.ok(second.document.body.classList.contains("deck-active"), "deck mode restored");
  assert.ok(tailNote(second.document), "still staged on the bare hunk after the reload");
  assert.equal(
    second.document.querySelector(".deck-act-frac").textContent,
    "1/1",
    "the tail-walked progress survived"
  );
  assert.ok(
    actDot(second.document, "hunk-b0").classList.contains("visited"),
    "its walked dot is restored"
  );
});

test("a regenerated run discards the tail state (self-invalidation on run identity)", () => {
  const storage = memoryStorage();

  const first = loadCockpit({ sessionStorage: storage, run: "run-1" });
  enterTail(first.document);
  assert.ok(tailNote(first.document), "walked the tail under run-1");

  // A new run identity is the clean break — the stale tail position/progress must not ride.
  const second = loadCockpit({ sessionStorage: storage, run: "run-2" });
  assert.ok(second.document.querySelector(".deck-stage .l0"), "opens fresh at L0 stop zero");
  assert.equal(second.document.querySelector(".deck-act-frac").textContent, "0/1", "nothing walked");
  assert.ok(
    !actDot(second.document, "hunk-b0").classList.contains("visited"),
    "no walked mark carried across the break"
  );
});

test("a restored walked-anchor that no longer resolves is dropped, never resurrected", () => {
  // Same run, but the persisted tail list names a hunk the current queue no longer has —
  // only the still-present anchor is restored (the derived, never-guessed posture).
  const seed = {
    [STORE_KEY]: JSON.stringify({
      run: "run-1",
      mode: "deck",
      stop: "l0",
      tail: ["hunk-b0", "hunk-gone"],
    }),
  };
  const { document } = loadCockpit({ sessionStorage: memoryStorage(seed), run: "run-1" });

  assert.equal(document.querySelector(".deck-act-frac").textContent, "1/1", "only the live anchor restored");
  assert.ok(actDot(document, "hunk-b0").classList.contains("visited"), "the present hunk reads walked");
});

test("a stale tail: stop that no-ops still renders the restored walked progress", () => {
  // Same run, but the persisted stop points at a bare hunk this run no longer has while a
  // live walked anchor remains. stageTailHunkByAnchor no-ops on the stale stop, so the Map
  // must still be rendered from the restored tailVisited (not left at the build-time frac).
  const seed = {
    [STORE_KEY]: JSON.stringify({
      run: "run-1",
      mode: "deck",
      stop: "tail:hunk-gone",
      tail: ["hunk-b0"],
    }),
  };
  const { document } = loadCockpit({ sessionStorage: memoryStorage(seed), run: "run-1" });

  assert.ok(document.querySelector(".deck-stage .l0"), "the stale stop no-ops → orientation stays staged");
  assert.equal(document.querySelector(".deck-act-frac").textContent, "1/1", "the restored walked count is rendered");
  assert.ok(actDot(document, "hunk-b0").classList.contains("visited"), "and its dot reads walked");
});

test("a fully-narrated diff (no queue) has no act, and forward clamps at the last step", () => {
  const doc = buildFixtureDocument();
  doc.getElementById("unnarrated-changes").remove(); // full coverage — nothing bare
  doc.querySelector("section.l0").setAttribute("data-coverage-label", "3 of 3 hunks narrated");
  const { document } = loadCockpit({ doc });

  assert.equal(document.querySelector(".deck-act"), null, "no un-narrated act");
  assert.ok(
    !document.querySelectorAll(".deck-map-label").some((l) => /final act/i.test(l.textContent)),
    "and no Final act label"
  );

  enterTail(document); // the fourth J is a no-op clamp with nothing bare to walk
  assert.equal(
    document.querySelector(".deck-stage .deck-crumb-step").textContent,
    "t2.s1",
    "forward clamps at the last step, exactly as before #105"
  );
});

test("multiple bare hunks in one file are each walked, sharing the file context", () => {
  const doc = buildFixtureDocument();
  // A second bare hunk in the SAME file (src/two.py): a second L3 hunk + a second queue li,
  // so buildTailHunks must group both under one file path.
  doc
    .getElementById("file-f2")
    .querySelector(".file-body")
    .appendChild(
      h(doc, "section.hunk#hunk-b0b", null, [
        h(doc, "pre.diff", null, ["@@ -20,0 +21,2 @@\n+    also_added = True\n"]),
      ])
    );
  doc
    .querySelector("#unnarrated-changes .unnarrated-hunks")
    .appendChild(h(doc, "li", null, [h(doc, "a", { href: "#hunk-b0b" }, ["hunk 2"])]));

  const { document } = loadCockpit({ doc });

  assert.equal(document.querySelectorAll(".deck-act-dot").length, 2, "one dot per bare hunk");
  enterTail(document); // → src/two.py hunk 1
  assert.equal(crumbText(document, "deck-crumb-title"), "src/two.py");
  assert.equal(crumbText(document, "deck-crumb-hunk"), "hunk 1");

  press(document, "j"); // → the same file's second bare hunk
  assert.equal(crumbText(document, "deck-crumb-title"), "src/two.py", "same file, next bare hunk");
  assert.equal(crumbText(document, "deck-crumb-hunk"), "hunk 2");
  assert.equal(document.querySelector(".deck-act-frac").textContent, "2/2", "both hunks walked");
});

test("at the route's end, the boundary line points a keys-only reviewer at the tail", () => {
  const { document, window } = loadCockpit();
  window.lavish = { queuePrompt() {}, sendQueuedPrompts() {} };
  press(document, "j"); // enter the route at t1.s1
  press(document, "l"); // dispose → advance to t1.s2
  press(document, "l"); // → t2.s1
  press(document, "l"); // last step disposed → the route boundary announcement

  const status = document.querySelector(".deck-stage-status").textContent;
  assert.match(status, /nothing left to advance/i, "the route boundary is announced");
  assert.match(status, /Press J to walk the un-narrated changes/i, "and it points at the tail");
});

test("the boundary line omits the tail hint on a fully-narrated diff", () => {
  const doc = buildFixtureDocument();
  doc.getElementById("unnarrated-changes").remove(); // no tail to walk
  const { document, window } = loadCockpit({ doc });
  window.lavish = { queuePrompt() {}, sendQueuedPrompts() {} };
  press(document, "j");
  press(document, "l");
  press(document, "l");
  press(document, "l");

  const status = document.querySelector(".deck-stage-status").textContent;
  assert.match(status, /nothing left to advance/i, "still announces the boundary");
  assert.doesNotMatch(status, /un-narrated changes/i, "but omits the tail hint when there is no tail");
});

test("resume recap steers to the tail when every step is reviewed but the tail isn't walked", async () => {
  const { document } = loadCockpit({
    dispositions: { "t1.s1": "looks-right", "t1.s2": "looks-right", "t2.s1": "looks-right" },
    sessionStorage: memoryStorage(),
  });
  await flush(); // let the recap's disposition/answered/resume fetches settle

  assert.ok(document.querySelector(".deck-recap"), "a resume with prior work recaps");
  assert.match(
    document.querySelector(".deck-recap-coverage").textContent,
    /3 of 3 steps/,
    "every step reviewed"
  );
  // Tail coverage is stated distinctly — steps are 'reviewed', the tail is 'walked' (issue #105).
  assert.match(
    document.querySelector(".deck-recap-tail").textContent,
    /0 of 1 hunks walked/,
    "the recap reports tail coverage apart from step coverage"
  );
  // The only CTA steers into the un-narrated tail, not 'Back to orientation' — so the recap
  // never tells a reviewer with an unwalked tail that the change is done, and staging the
  // bare hunk sets the tail return memory (tailStop) rather than discarding it.
  assert.ok(document.querySelector(".deck-recap-done"), "states every step is reviewed");
  assert.equal(
    document.querySelector(".deck-recap-continue-step").textContent,
    "hunk 1",
    "the CTA names the first un-walked bare hunk"
  );
  click(document.querySelector(".deck-recap-continue"));
  assert.ok(tailNote(document), "clicking it walks the un-narrated tail");
});

test("resume recap keeps the CTA on the active route: Core done → the tail, not a Full-only step", async () => {
  // Restore the Core route from the store, then resume with both Core steps (t1.s1, t1.s2)
  // disposed and the Full-only t2.s1 (test-change) left undisposed. Core is complete, so the
  // recap CTA must enter the final act — matching J — not jump to the off-route t2.s1.
  const storage = memoryStorage({
    [STORE_KEY]: JSON.stringify({ run: "run-1", route: "core", mode: "deck", stop: "l0" }),
  });
  const { document } = loadCockpit({
    dispositions: { "t1.s1": "looks-right", "t1.s2": "looks-right" },
    sessionStorage: storage,
    run: "run-1",
  });
  await flush();

  assert.ok(document.querySelector(".deck-recap"), "the resume recaps");
  assert.match(
    document.querySelector(".deck-recap-continue-label").textContent,
    /Walk the un-narrated changes/,
    "Core complete → the CTA enters the tail, not the off-route Full-only step"
  );
  assert.equal(document.querySelector(".deck-recap-continue-step").textContent, "hunk 1");
  click(document.querySelector(".deck-recap-continue"));
  assert.ok(tailNote(document), "and it walks the tail");
});
