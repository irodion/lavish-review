// Contract tests for the minimal DOM shim (dom.mjs) itself. The deck tests trust
// the shim to behave like a browser DOM; these pin the corners that are easy to get
// subtly wrong, so a shim regression surfaces here rather than as a confusing deck
// test failure.

import { test } from "node:test";
import assert from "node:assert/strict";
import { Document } from "./dom.mjs";

const childTags = (parent) => parent.children.map((c) => c.tagName);

test("insertBefore moving an existing earlier sibling preserves order", () => {
  // Regression: the index of `ref` must be read AFTER detaching the moved node, or
  // moving a node that sits before `ref` in the same parent lands it one slot late.
  const doc = new Document();
  const parent = doc.createElement("div");
  const a = doc.createElement("a-el");
  const b = doc.createElement("b-el");
  const c = doc.createElement("c-el");
  parent.appendChild(a);
  parent.appendChild(b);
  parent.appendChild(c);

  // Move A (currently first) to just before C → expected order: B, A, C.
  parent.insertBefore(a, c);
  assert.deepEqual(childTags(parent), ["B-EL", "A-EL", "C-EL"]);
  assert.equal(a.parentNode, parent);
});

test("insertBefore of a fresh node lands it before the reference", () => {
  const doc = new Document();
  const parent = doc.createElement("div");
  const b = doc.createElement("b-el");
  parent.appendChild(b);
  parent.insertBefore(doc.createElement("a-el"), b);
  assert.deepEqual(childTags(parent), ["A-EL", "B-EL"]);
});

test("insertBefore with a null reference appends", () => {
  const doc = new Document();
  const parent = doc.createElement("div");
  parent.appendChild(doc.createElement("a-el"));
  parent.insertBefore(doc.createElement("b-el"), null);
  assert.deepEqual(childTags(parent), ["A-EL", "B-EL"]);
});
