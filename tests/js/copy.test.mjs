// Hunk locators — copy `path:line` (issue #108). The renderer puts each hunk's
// coordinates on the page; app.js adds the one part that needs script. The invariants:
// the affordance exists in BOTH modes (served and the `file://` record — copying a
// location needs no session), it copies exactly the rendered text, it survives the deck's
// CLONING of hunk sections (delegation, not per-button listeners), and a browser that
// refuses the Clipboard API falls back rather than silently doing nothing.

import test from "node:test";
import assert from "node:assert/strict";
import {
  buildFixtureDocument,
  click,
  flush,
  loadCockpit,
  memoryClipboard,
  press,
} from "./harness.mjs";

const copyButton = (document, hunkId) =>
  document.getElementById(hunkId).querySelector(".hunk-copy");

test("every rendered hunk gains a copy button, served (issue #108)", () => {
  const { document } = loadCockpit();
  const locators = document.querySelectorAll(".hunk-locator");
  assert.equal(locators.length, 3, "the fixture's three hunks each carry a locator");
  for (const locator of locators) {
    const button = locator.querySelector(".hunk-copy");
    assert.ok(button, "each locator has a copy button");
    assert.equal(button.getAttribute("type"), "button");
    assert.equal(button.textContent, "copy");
    // Labelled with what it copies, so a screen reader hears the target, not "copy".
    assert.equal(
      button.getAttribute("aria-label"),
      "Copy " + locator.querySelector(".hunk-path").textContent
    );
  }
});

test("the copy affordance is present on file:// too — no session needed (issue #108)", () => {
  // Unlike dispositions/ask/ticks (presence-gated), the portable and baked records keep
  // this: it is the whole point of the criterion "served AND file://".
  const { document } = loadCockpit({ protocol: "file:" });
  assert.equal(document.querySelectorAll(".hunk-copy").length, 3);
  assert.equal(document.querySelectorAll(".step-ask").length, 0, "still no ask box on file://");
});

test("clicking copy writes exactly the rendered path:line (issue #108)", async () => {
  const { document, clipboard } = loadCockpit();
  const button = copyButton(document, "hunk-a1");

  click(button);
  await flush();

  assert.deepEqual(clipboard.writes, ["src/one.py:1"]);
  assert.equal(button.textContent, "copied");
  assert.ok(button.className.includes("copied"));
});

test("a second copy resets the first button's label (issue #108)", async () => {
  // Exactly one affordance may claim the clipboard's contents: a stale "copied" on a
  // different hunk would misstate where the clipboard points.
  const { document, clipboard } = loadCockpit();
  const first = copyButton(document, "hunk-a1");
  const second = copyButton(document, "hunk-b1");

  click(first);
  await flush();
  click(second);
  await flush();

  assert.deepEqual(clipboard.writes, ["src/one.py:1", "src/three.py:5"]);
  assert.equal(first.textContent, "copy");
  assert.ok(!first.className.includes("copied"));
  assert.equal(second.textContent, "copied");
});

test("the copy button works on the Stage's cloned hunk (issue #108)", async () => {
  // The deck clones hunk sections for inline evidence; a clone carries no listeners, so
  // the click must be delegated — otherwise the Stage's button would be dead.
  const { document, clipboard } = loadCockpit();
  const staged = document.querySelector(".deck-stage");
  assert.ok(staged, "the deck is built");

  press(document, "j"); // stop zero → stage t1.s1, which clones hunk-a1 inline

  const inline = document.querySelector(".deck-stage .deck-hunk .hunk-copy");
  assert.ok(inline, "the cloned hunk carries the copy affordance");
  click(inline);
  await flush();
  assert.deepEqual(clipboard.writes, ["src/one.py:1"], "the clone copies its own path:line");
});

test("a refused clipboard falls back and reports failure rather than lying (issue #108)", async () => {
  // No execCommand in this DOM either, so the fallback cannot succeed — the button must
  // say so instead of claiming a copy that never happened.
  const { document } = loadCockpit({ clipboard: memoryClipboard({ reject: true }) });
  const button = copyButton(document, "hunk-a1");

  click(button);
  await flush();

  assert.equal(button.textContent, "copy failed");
  assert.ok(!button.className.includes("copied"));
});

test("no Clipboard API at all is handled without throwing (issue #108)", async () => {
  const { document } = loadCockpit({ clipboard: null }); // no `navigator` in scope
  const button = copyButton(document, "hunk-a1");
  click(button);
  await flush();
  assert.equal(button.textContent, "copy failed");
});

test("a hunk with no locator gains no button — a pre-#108 page is unchanged", () => {
  const doc = buildFixtureDocument();
  const locator = doc.getElementById("hunk-a1").querySelector(".hunk-locator");
  locator.parentNode.removeChild(locator);
  const { document } = loadCockpit({ doc });
  assert.equal(document.getElementById("hunk-a1").querySelector(".hunk-copy"), null);
  assert.equal(document.querySelectorAll(".hunk-copy").length, 2, "the other hunks still have one");
});

test("the editor deep link is left exactly as rendered (issue #108)", () => {
  // The link is server-rendered from the machine's `editor` config; app.js neither builds
  // nor rewrites it — the client's only job is the copy button beside it.
  const doc = buildFixtureDocument();
  const locator = doc.getElementById("hunk-a1").querySelector(".hunk-locator");
  const link = doc.createElement("a");
  link.className = "hunk-editor";
  link.setAttribute("href", "vscode://file/repo/src/one.py:1");
  link.appendChild(doc.createTextNode("open"));
  locator.appendChild(link);

  const { document } = loadCockpit({ doc });

  const rendered = document.getElementById("hunk-a1").querySelector(".hunk-editor");
  assert.equal(rendered.getAttribute("href"), "vscode://file/repo/src/one.py:1");
  // The copy button is injected between the path and the link, never over it.
  assert.ok(document.getElementById("hunk-a1").querySelector(".hunk-copy"));
});
