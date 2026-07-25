// Move-dimming in the client diff rebuild (issue #106). The collector classifies
// relocated-but-identical lines and relays their positions on each hunk section as
// `data-moved`; annotateDiff must dim exactly those rows (a `dl-moved` class) so a
// genuine edit inside a moved block stands out — and the dimming must travel onto the
// Stage's inline evidence clone. A hunk without the attribute must render exactly as
// before the detector existed.

import test from "node:test";
import assert from "node:assert/strict";
import { buildFixtureDocument, loadCockpit, press } from "./harness.mjs";

// hunk-a1's body after the `@@` header (0-based): pos 0 = " context",
// pos 1 = "+ payload…", pos 2 = "- old = 1".

test("annotateDiff dims exactly the lines named by data-moved (issue #106)", () => {
  const doc = buildFixtureDocument();
  doc.getElementById("hunk-a1").setAttribute("data-moved", "1"); // the added line only
  const { document } = loadCockpit({ doc });

  const hunk = document.getElementById("hunk-a1");
  assert.equal(hunk.querySelectorAll("tr.dl-moved").length, 1, "exactly the one named line dims");
  assert.ok(
    hunk.querySelector("tr.dl-add").className.includes("dl-moved"),
    "the relocated + line is dimmed"
  );
  assert.ok(
    !hunk.querySelector("tr.dl-del").className.includes("dl-moved"),
    "the un-relocated - line stays at full contrast"
  );
});

test("data-moved dimming travels onto the Stage's inline evidence clone (issue #106)", () => {
  const doc = buildFixtureDocument();
  doc.getElementById("hunk-a1").setAttribute("data-moved", "1,2"); // both changed lines
  const { document } = loadCockpit({ doc });
  press(document, "j"); // stop zero → stage t1.s1, which clones hunk-a1 inline

  const inline = document.querySelector(".deck-stage .deck-hunk .diff-table");
  assert.ok(inline, "the evidence hunk is inline on the Stage");
  assert.equal(
    inline.querySelectorAll("tr.dl-moved").length,
    2,
    "both relocated lines are dimmed in the cloned inline evidence"
  );
});

test("a hunk with no data-moved gets no dimming — degrades to today exactly (issue #106)", () => {
  const { document } = loadCockpit(); // the default fixture carries no data-moved
  assert.equal(document.querySelectorAll("tr.dl-moved").length, 0, "nothing is dimmed");
});

test("a malformed data-moved dims nothing rather than throwing (issue #106)", () => {
  const doc = buildFixtureDocument();
  // Includes partially-numeric tokens: "1junk"/"1.5" parseInt to 1 and would wrongly dim
  // row 1 (the +payload line) — only a complete digit run is a valid position.
  doc.getElementById("hunk-a1").setAttribute("data-moved", "nope,,-3,1junk,1.5");
  const { document } = loadCockpit({ doc });
  assert.equal(
    document.getElementById("hunk-a1").querySelectorAll("tr.dl-moved").length,
    0,
    "unparseable and partially-numeric positions are skipped, not applied"
  );
});
