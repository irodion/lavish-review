// Branch Review Cockpit — vendored behaviour.
//
// All cockpit behaviour lives here, never inline, so the cockpit ships a strict
// CSP (`script-src 'self'`) that forbids inline script (issue #4). This script
// treats the diff strictly as TEXT: it rebuilds each line with createElement +
// textContent and NEVER assigns attacker-derived strings to innerHTML, so a
// `<script>` hidden in a diff hunk can only ever render as visible characters.
//
// Diff Annotator (Deck Mode, ADR-0014, issue #64): each `<pre class="diff">` is
// rebuilt as a `<table class="diff-table">` with dual old/new line-number gutters
// (computed client-side from the `@@` hunk headers) and visually distinct hunk
// header rows. When the diff is a Hunk Anchorer section (`<section class="hunk"
// id="hunk-…">`, issue #63), the header row self-links to that id so a reviewer
// can grab the anchor, and `:target` navigation (from a step's evidence link)
// highlights the row. The rebuild is text-only — same discipline as before, so a
// `<script>` in a hunk still renders as characters — and it runs identically in
// the served cockpit and the baked/portable `file://` copy.
//
// Layered cockpit (ADR-0009/0016, issue #39): disclosure is native <details> — no
// JS needed to descend. This script only adds navigation glue: following a step's
// evidence link (or any #anchor) opens the target's ancestor <details> chain so a
// deep link never lands on a collapsed, invisible element. The hash is used solely
// as a getElementById key — never interpolated into markup or selectors.
//
// Reviewer dispositions (ADR-0012, reframed by ADR-0016, issues #42/#86): each
// Review Step gets JS-injected controls (looks-right / concern / follow-up /
// skipped — the five-state vocabulary, `unreviewed` being absence) that mark the
// step locally, update the thread's progress line, and queue a structured update
// through the Lavish SDK (`window.lavish.queuePrompt` with a `data` payload and a
// per-step `queueKey` — the channel the #38 spike verified) for the loop agent to
// persist. State is restored on load by fetching `dispositions.json` beside the
// cockpit. Everything is rendered with createElement/textContent from closed
// vocabularies — no attacker-derived string ever reaches markup. On `file://` (a
// portable or baked artifact) there is no live session, so the controls are not
// rendered.
//
// Step-scoped questions (ADR-0015, reframed by ADR-0016, issues #65/#86): each
// step also gets a JS-injected ask affordance that queues the reviewer's question
// through the *same* presence-gated channel, carrying the step id as structured
// data (`{kind: "step-question", step}`, `tag: "message"`, per-step `queueKey` so
// rapid edits collapse) — no DOM selector to resolve. The loop answers it grounded
// in that step; the exchange bakes into the Q&A Log like any chat question (it is
// conversation, not state — no store, no apply step). The step id is a closed
// vocabulary (matched against `STEP_ID`); the reviewer's free-text question only
// ever reaches an `<input>`/`<textarea>` value, never markup. Same served-only gate
// as dispositions: absent on `file://`.

(function () {
  "use strict";

  // A unified-diff hunk header — `@@ -oldStart[,oldLen] +newStart[,newLen] @@`.
  // Two-way (base…HEAD) diffs only ever open a hunk with `@@`; the capture groups
  // seed the old/new line counters. A line that begins `@@` but doesn't match
  // (e.g. a combined-merge `@@@`) is still shown as a header row, just without a
  // counter reset — never mis-numbered as content.
  const HUNK_NUMS = /^@@+ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/;

  function cell(tag, className, text) {
    const el = document.createElement(tag);
    el.className = className;
    if (text !== null) {
      el.textContent = text; // text only — never markup
    }
    return el;
  }

  // A full-width row that spans both gutters and the code column — used for the
  // preamble (diff --git / index / --- / +++ / rename headers) and hunk headers.
  function spanRow(className, child) {
    const tr = document.createElement("tr");
    tr.className = className;
    const td = cell("td", "code", null);
    td.colSpan = 3;
    td.appendChild(child);
    tr.appendChild(td);
    return tr;
  }

  function annotateDiff(pre) {
    const section = pre.closest ? pre.closest("section.hunk") : null;
    const anchor = section && section.id ? section.id : null;

    const lines = pre.textContent.split("\n");
    // A unified diff ends in a trailing newline, so the final split element is an
    // empty string — dropping it avoids a spurious blank row at the diff's foot.
    if (lines.length && lines[lines.length - 1] === "") {
      lines.pop();
    }

    const tbody = document.createElement("tbody");
    let oldNo = 0;
    let newNo = 0;
    let inHunk = false;

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];

      if (line.slice(0, 2) === "@@") {
        const nums = HUNK_NUMS.exec(line);
        if (nums) {
          oldNo = parseInt(nums[1], 10);
          newNo = parseInt(nums[2], 10);
          inHunk = true;
        }
        // The header row self-links to the hunk's anchor when one exists (a Hunk
        // Anchorer section, #63); otherwise it is plain text (e.g. the whole-diff
        // fallback, which carries no per-hunk ids).
        let head;
        if (anchor) {
          head = document.createElement("a");
          head.href = "#" + anchor; // anchor is [0-9a-f-] from the Hunk Anchorer
          head.textContent = line; // text only — never markup — styled via `tr.dl-hunk a`
        } else {
          head = document.createTextNode(line);
        }
        tbody.appendChild(spanRow("dl-hunk", head));
        continue;
      }

      // Anything before the first hunk header is preamble — file/mode/rename lines
      // (`---`/`+++` included, which are headers here, never deletions/additions).
      if (!inHunk) {
        tbody.appendChild(spanRow("dl-meta", document.createTextNode(line)));
        continue;
      }

      const marker = line.charAt(0);
      let cls;
      let oldCell = "";
      let newCell = "";
      if (marker === "+") {
        cls = "dl-add";
        newCell = String(newNo++);
      } else if (marker === "-") {
        cls = "dl-del";
        oldCell = String(oldNo++);
      } else if (marker === " ") {
        cls = "dl-ctx";
        oldCell = String(oldNo++);
        newCell = String(newNo++);
      } else {
        // `\ No newline at end of file` and any other in-hunk oddity: no numbers.
        tbody.appendChild(spanRow("dl-meta", document.createTextNode(line)));
        continue;
      }

      const tr = document.createElement("tr");
      tr.className = cls;
      tr.appendChild(cell("td", "lno", oldCell));
      tr.appendChild(cell("td", "lno", newCell));
      tr.appendChild(cell("td", "code", line)); // full raw line, prefix kept, text only
      tbody.appendChild(tr);
    }

    const table = document.createElement("table");
    table.className = "diff-table";
    table.appendChild(tbody);
    pre.textContent = "";
    pre.appendChild(table);
  }

  // Open every <details> from the element up to the root, so a navigation target
  // inside collapsed layers becomes visible before the browser scrolls to it.
  function revealElement(el) {
    for (let node = el; node; node = node.parentElement) {
      if (node.tagName === "DETAILS") {
        node.open = true;
      }
    }
  }

  function revealHashTarget() {
    if (!location.hash || location.hash.length < 2) {
      return;
    }
    let id;
    try {
      id = decodeURIComponent(location.hash.slice(1));
    } catch (_err) {
      return; // a malformed hash is simply not a target
    }
    const target = document.getElementById(id);
    if (target) {
      revealElement(target);
    }
  }

  // --- Reviewer dispositions (ADR-0012/0016) --------------------------------

  // Closed vocabularies: these strings are the ONLY values that ever reach the
  // DOM or the feedback channel — reviewer intent is expressed by choosing one.
  // `unreviewed` is the default (absence); the four SETTABLE states persist. The
  // labels carry a glyph + word so a state never reads by colour alone (ADR-0014),
  // and `looks-right` deliberately avoids a checkmark — an attest of comprehension,
  // not an approval stamp (ADR-0016; CONTEXT "avoid checkmark/verdict").
  const STEP_ID = /^t\d+\.s\d+$/;
  const SETTABLE = ["looks-right", "concern", "follow-up", "skipped"];
  const LABELS = {
    "looks-right": "● looks right",
    concern: "⚠ concern",
    "follow-up": "? follow-up",
    skipped: "↷ skip",
  };

  // Every step panel under `root` (the document, or one thread) whose id is in the
  // closed `t\d+.s\d+` vocabulary — the one filter the dispositions, questions, and
  // deck all share.
  function stepsIn(root) {
    return Array.prototype.filter.call(root.querySelectorAll("details.step"), function (el) {
      return STEP_ID.test(el.id);
    });
  }

  function stepElements() {
    return stepsIn(document);
  }

  // Queue one prompt through the SDK and flush it. `window.lavish` is checked at
  // interaction time — the SDK script loads after ours (#38). Sends are presence-
  // gated by the host, so delivery is batched/eventually-consistent within the
  // session (also #38). Returns false when there is no live SDK to accept the
  // prompt, so a caller can tell the reviewer instead of dropping it silently.
  // The shared seam for every structured feedback send — a disposition update and
  // a step-scoped question differ only in the payload they pass here.
  function queueToSdk(message, options) {
    const sdk = window.lavish;
    if (!sdk || typeof sdk.queuePrompt !== "function") {
      return false;
    }
    sdk.queuePrompt(message, options);
    if (typeof sdk.sendQueuedPrompts === "function") {
      sdk.sendQueuedPrompts();
    }
    return true;
  }

  // The per-step queueKey collapses rapid re-clicks to the last state. The message
  // line and the `data` payload are exactly what `dispositions.py` parses (a step
  // id + a five-state value) — the deterministic bridge, never re-typed by hand.
  function sendDisposition(stepId, disposition) {
    queueToSdk("Disposition set: " + stepId + " -> " + disposition, {
      tag: "choice",
      text: "disposition:" + disposition,
      queueKey: "disposition:" + stepId,
      data: { kind: "disposition", step: stepId, disposition: disposition },
    });
  }

  // Reflect a step's disposition onto a set of controls keyed by data-disposition:
  // `aria-pressed` is true only on the button whose disposition is the current one.
  // Shared by the in-step summary controls and the oversized Stage control.
  function syncPressed(buttons, current) {
    Array.prototype.forEach.call(buttons, function (btn) {
      btn.setAttribute("aria-pressed", btn.dataset.disposition === current ? "true" : "false");
    });
  }

  // The one disposition write rule, shared by the in-step controls and the Stage:
  // re-selecting the active state clears it back to unreviewed, and every local
  // write is mirrored to the feedback channel. Returns the resulting state so a
  // caller (the Stage) can decide whether to auto-advance.
  function toggleDisposition(step, disposition) {
    const active = step.getAttribute("data-disposition") === disposition;
    const next = active ? "unreviewed" : disposition;
    applyDisposition(step, next);
    sendDisposition(step.id, next);
    return next;
  }

  function applyDisposition(step, disposition) {
    if (disposition === "unreviewed") {
      step.removeAttribute("data-disposition");
    } else {
      step.setAttribute("data-disposition", disposition);
    }
    syncPressed(step.querySelectorAll(".disposition-controls button"), disposition);
    const thread = step.closest("section.thread");
    if (thread) {
      updateThreadProgress(thread);
    }
    // Keep the deck views (Map dots and fractions, the oversized Stage control) in
    // step with the disposition (no-op in document mode / before the deck is built).
    refreshDeck();
  }

  function updateThreadProgress(thread) {
    const steps = stepsIn(thread);
    if (!steps.length) {
      return;
    }
    let reviewed = 0;
    let concerns = 0;
    steps.forEach(function (el) {
      const state = el.getAttribute("data-disposition");
      if (state) {
        reviewed++;
      }
      if (state === "concern") {
        concerns++;
      }
    });
    let progress = thread.querySelector(".thread-progress");
    if (!progress) {
      const heading = thread.querySelector("h2");
      if (!heading) {
        return;
      }
      progress = document.createElement("span");
      progress.className = "thread-progress";
      heading.appendChild(progress);
    }
    let text = reviewed + "/" + steps.length + " reviewed";
    if (concerns) {
      text += " · " + concerns + " concern" + (concerns === 1 ? "" : "s");
    }
    progress.textContent = text; // text only — never markup
    progress.classList.toggle("has-concern", concerns > 0);
  }

  function injectDispositionControls(step) {
    const summary = step.querySelector("summary");
    if (!summary || summary.querySelector(".disposition-controls")) {
      return;
    }
    const group = document.createElement("span");
    group.className = "disposition-controls";
    SETTABLE.forEach(function (disposition) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.dataset.disposition = disposition;
      btn.setAttribute("aria-pressed", "false");
      btn.textContent = LABELS[disposition]; // fixed label, never derived text
      btn.addEventListener("click", function (event) {
        // A button inside <summary> must not toggle the disclosure panel.
        event.preventDefault();
        event.stopPropagation();
        toggleDisposition(step, disposition);
      });
      group.appendChild(btn);
    });
    summary.appendChild(group);
  }

  // Restore persisted state (Esc → /review-resume → reload): the loop agent
  // maintains dispositions.json beside the cockpit; only well-shaped entries are
  // applied — a hostile or corrupt store can at most select an enum value.
  function loadDispositions() {
    fetch("dispositions.json", { cache: "no-store" })
      .then(function (response) {
        return response.ok ? response.json() : null;
      })
      .then(function (payload) {
        if (!payload || typeof payload !== "object") {
          return;
        }
        const entries = payload.dispositions;
        if (!entries || typeof entries !== "object") {
          return;
        }
        stepElements().forEach(function (step) {
          const value = entries[step.id];
          if (typeof value === "string" && SETTABLE.indexOf(value) !== -1) {
            applyDisposition(step, value);
          }
        });
      })
      .catch(function (_err) {
        // No store yet (fresh review) or no fetch permission — everything unreviewed.
      });
  }

  function setupDispositions() {
    // file:// means no live session (no SDK, no store to fetch): a portable or
    // baked artifact is a record, not a review surface — render no controls.
    if (location.protocol === "file:") {
      return;
    }
    const steps = stepElements();
    if (!steps.length) {
      return;
    }
    steps.forEach(injectDispositionControls);
    document.querySelectorAll("section.thread").forEach(updateThreadProgress);
    loadDispositions();
  }

  // --- Step-scoped questions (ADR-0015/0016) --------------------------------

  // Queue the reviewer's question through the SDK, carrying the step id as
  // structured data — the same presence-gated channel dispositions use, keyed by a
  // per-step `queueKey` so a rapid edit-and-resend collapses to the latest text.
  // Unlike a disposition (a `tag: "choice"` state update), a question is a plain
  // `tag: "message"` — it flows into the Q&A Log and bakes like any chat question,
  // never filtered out as state. Returns false (via `queueToSdk`) when there is no
  // live SDK, so the caller can say so instead of dropping the question silently.
  function sendStepQuestion(stepId, text) {
    return queueToSdk(text, {
      tag: "message",
      queueKey: "question:" + stepId, // collapses rapid edits, like dispositions
      data: { kind: "step-question", step: stepId },
    });
  }

  function injectAskControl(step) {
    const body = step.querySelector(".step-body");
    if (!body || body.querySelector(".step-ask")) {
      return;
    }
    const group = document.createElement("div");
    group.className = "step-ask";

    // A status line, updated with textContent only — never markup. `role="status"`
    // announces "Sent"/"No live session" to assistive tech without stealing focus.
    const status = document.createElement("span");
    status.className = "step-ask-status";
    status.setAttribute("role", "status");

    const input = document.createElement("textarea");
    input.className = "step-ask-input";
    input.rows = 2;
    // A fixed placeholder + aria-label — the step id is a closed vocabulary
    // (STEP_ID), so it is safe to name; the reviewer's own text stays in `.value`,
    // never in markup. The aria-label survives once the placeholder disappears on
    // typing, keeping an accessible name for the field.
    input.setAttribute("aria-label", "Ask about " + step.id);
    input.setAttribute("placeholder", "Ask about " + step.id + "…");

    const send = document.createElement("button");
    send.type = "button";
    send.className = "step-ask-send";
    send.textContent = "Ask"; // fixed label, never derived text

    function submit() {
      const text = input.value.trim();
      if (!text) {
        return;
      }
      if (sendStepQuestion(step.id, text)) {
        input.value = "";
        status.textContent = "Sent — the answer appears in the chat.";
      } else {
        // No SDK: a portable copy served without a live session. Keep the text so
        // the reviewer loses nothing; just say it could not be sent.
        status.textContent = "No live session — question not sent.";
      }
      persistUiState(); // a sent question clears the draft; a failed one keeps it
    }

    send.addEventListener("click", submit);
    // ⌘/Ctrl+Enter sends; a bare Enter keeps inserting newlines (a question may run
    // to a sentence or two). Typing again clears a stale status so it never lingers.
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        submit();
      } else if (status.textContent) {
        status.textContent = "";
      }
    });
    // Persist the half-typed question so an injection reload never loses it. The
    // draft only ever lives in `.value` (never markup) and never leaves this tab.
    input.addEventListener("input", persistUiState);

    group.appendChild(input);
    group.appendChild(send);
    group.appendChild(status);
    body.appendChild(group);
  }

  // Inject a served-only control onto every step. file:// is a portable record, not a
  // review surface, so its steps carry no injected controls — the same gate dispositions
  // use (dispositions just do extra per-step wiring, so they don't route through here).
  function forEachServedStep(inject) {
    if (location.protocol === "file:") {
      return;
    }
    stepElements().forEach(inject);
  }

  function setupStepQuestions() {
    forEachServedStep(injectAskControl); // no ask loop on a file:// record
  }

  // --- Tickable review prompts (issue #99) ----------------------------------
  //
  // Each `review_prompt` is a comparison the reviewer works through, so it gets a
  // tick affordance to check off — turning a Review Step into a sequence of micro-
  // completions instead of one dreaded essay. A tick is EPHEMERAL served-session UI
  // state: it is NOT a Reviewer Disposition, never queued through the feedback
  // channel, and never touches dispositions, progress counts, or auto-advance —
  // completing every prompt does not adjudicate the step (judgment stays the
  // reviewer's explicit act). The tick lives on the prompt's own `<li>` (a `ticked`
  // class + the button's `aria-pressed`), so it relocates onto the Stage and back
  // with the step, losslessly round-tripping the mode toggle; the UI store carries
  // it across an injection reload (snapshotTicks). On file:// (a baked/portable
  // record) no control is injected — the prompts stay plain list items, the same
  // served-only gate the deck and dispositions use.

  // Reflect a prompt's ticked state on its `<li>` and the tick button together, so the
  // class the stylesheet reads and the `aria-pressed` assistive tech reads never disagree.
  function setPromptTick(li, ticked) {
    li.classList.toggle("ticked", ticked);
    const btn = li.querySelector(".prompt-tick");
    if (btn) {
      btn.setAttribute("aria-pressed", ticked ? "true" : "false");
      btn.textContent = ticked ? "✓" : "○"; // ✓ / ○ — a shape change, never colour alone
    }
  }

  function injectPromptTicks(step) {
    Array.prototype.forEach.call(step.querySelectorAll(".review-prompts li"), function (li) {
      if (li.querySelector(".prompt-tick")) {
        return; // already injected (idempotent, like the disposition/ask controls)
      }
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "prompt-tick";
      btn.setAttribute("aria-pressed", "false");
      btn.setAttribute("aria-label", "Mark this review prompt as done");
      btn.textContent = "○"; // ○ unticked; setPromptTick swaps to ✓
      btn.addEventListener("click", function () {
        setPromptTick(li, !li.classList.contains("ticked"));
        persistUiState(); // a tick is ephemeral state to carry across the injection reload
      });
      li.insertBefore(btn, li.firstChild);
      // Mark the row interactive so the stylesheet can lay it out as a tick line
      // without a relational (:has) selector — JS sets the state class, CSS reads it,
      // the same contract `ticked`/`data-disposition`/the deck classes follow. The
      // baked file:// record never reaches here, so its prompts stay plain <li> items.
      li.classList.add("tickable");
    });
  }

  function setupPromptTicks() {
    forEachServedStep(injectPromptTicks); // a file:// record shows prompts as plain list items
  }

  // --- Deck Mode (ADR-0014/0016) --------------------------------------------
  //
  // When the cockpit is *served* (the same presence gate as dispositions), the
  // vendored script re-presents the L0–L3 document as a **Map** (threads in
  // Review Route order with one disposition-tinted dot per step, the changed
  // files with their stats, overall progress) beside a **Stage** (L0 stop zero,
  // then one step at a time with its evidence hunk inline). Document mode is one
  // visible toggle away and is the only mode on `file://`; the baked record stays
  // the document, unchanged (ADR-0014).
  //
  // The deck is built strictly by RELOCATING and CLONING nodes already in the
  // document DOM — never by constructing markup from strings for untrusted data.
  // The Stage *moves* the step's own `<details class="step">` element (so its
  // injected disposition controls, ask affordance, open state, and disposition
  // tint travel with it and the mode toggle round-trips losslessly), leaving a
  // hidden placeholder to move it back to; the inline evidence *clones* the
  // step's already-annotated hunk sections. Because every deck node is either a
  // fixed-vocabulary element built with createElement/textContent or a clone of
  // an already-escaped document node, a `<script>` hidden in a diff can still
  // only ever render as visible text — the same discipline the diff rebuild uses.

  // The deck's live state, or null until the deck is built (file:// / no steps).
  let deck = null;

  // Disposition tallies over a set of steps — reviewed + one count per settable state.
  function dispositionCounts(steps) {
    const totals = { reviewed: 0 };
    SETTABLE.forEach(function (d) {
      totals[d] = 0;
    });
    steps.forEach(function (step) {
      const state = step.getAttribute("data-disposition");
      if (state) {
        totals.reviewed++;
      }
      if (SETTABLE.indexOf(state) !== -1) {
        totals[state]++;
      }
    });
    return totals;
  }

  // A thread heading's title text, with the id/chip/impacts/progress spans stripped
  // — read off a detached clone so the live heading is never disturbed. text only.
  function threadTitleText(heading) {
    if (!heading) {
      return "";
    }
    const clone = heading.cloneNode(true);
    Array.prototype.forEach.call(
      clone.querySelectorAll(".thread-id, .chip, .thread-impacts, .thread-weight, .thread-progress"),
      function (node) {
        node.remove();
      }
    );
    return clone.textContent.trim();
  }

  // A tinted count fragment for the progress line: a fixed glyph + number, its
  // colour carried by the class (never colour alone — ADR-0014). text only.
  function countBadge(className, glyph, value) {
    return cell("span", "deck-count " + className, glyph + " " + value);
  }

  // (Re)draw the Map's dynamic parts — the overall progress, and each thread's dots
  // and fraction, which all move with the reviewer's dispositions. The static file
  // rail (built once at deck-build time) is simply re-appended. Iterating the
  // grouping captured at build time — not the live thread subtree — keeps the
  // currently-staged step (relocated onto the Stage) in its thread's dots and count.
  function renderMap() {
    const map = deck.map;
    map.textContent = "";

    const overall = dispositionCounts(deck.steps);
    const progress = cell("div", "deck-progress", null);
    progress.appendChild(cell("span", "deck-tally", overall.reviewed + "/" + deck.steps.length + " reviewed"));
    progress.appendChild(countBadge("looks-right", "●", overall["looks-right"]));
    progress.appendChild(countBadge("concern", "⚠", overall.concern));
    progress.appendChild(countBadge("follow-up", "?", overall["follow-up"]));
    progress.appendChild(countBadge("skipped", "↷", overall.skipped));
    map.appendChild(progress);

    map.appendChild(cell("p", "deck-map-label", "Threads — review route"));

    deck.groups.forEach(function (group) {
      const steps = group.steps;
      if (!steps.length) {
        return;
      }
      const block = cell("div", "deck-thread-block", null);

      const threadButton = document.createElement("button");
      threadButton.type = "button";
      threadButton.className = "deck-thread";
      threadButton.appendChild(cell("span", "deck-thread-id", group.threadId));
      threadButton.appendChild(cell("span", "deck-thread-title", group.title));
      if (group.impactSummary) {
        // Thread impact character is renderer-owned. Reuse its already-rendered
        // text and attention class instead of deriving a second count in JS.
        threadButton.appendChild(group.impactSummary.cloneNode(true));
      }
      if (group.threadWeight) {
        // Per-thread reading weight is renderer-derived too — clone the rendered node
        // (its time label + title) rather than re-summing step weights in JS.
        threadButton.appendChild(group.threadWeight.cloneNode(true));
      }
      const counts = dispositionCounts(steps);
      threadButton.appendChild(cell("span", "deck-thread-frac", counts.reviewed + "/" + steps.length));
      // Staging a thread lands on its first step — the entry to that leg of the route.
      threadButton.addEventListener("click", function () {
        stageStep(steps[0]);
      });
      block.appendChild(threadButton);

      const dots = cell("div", "deck-dots", null);
      steps.forEach(function (step) {
        const dot = document.createElement("button");
        dot.type = "button";
        dot.className = "deck-dot";
        dot.dataset.step = step.id;
        const state = step.getAttribute("data-disposition");
        if (state) {
          dot.setAttribute("data-disposition", state);
        }
        // The dot also carries its step's Behavior Impact so the Map tints by the
        // change's character, not just the reviewer's disposition (ADR-0016).
        const impact = step.getAttribute("data-impact");
        if (impact) {
          dot.setAttribute("data-impact", impact);
        }
        // Relay the renderer's Map-dot size tier verbatim (issue #100), the same way
        // data-impact is relayed above — the size-bucket policy lives in the renderer
        // (weight.py), never re-derived here. Absent (an older page) → default width.
        const weightBucket = step.getAttribute("data-weight-bucket");
        if (weightBucket) {
          dot.setAttribute("data-weight-bucket", weightBucket);
        }
        if (step === deck.staged) {
          dot.classList.add("current");
        }
        dot.setAttribute("title", step.id);
        dot.setAttribute("aria-label", "Stage step " + step.id);
        dot.addEventListener("click", function () {
          stageStep(step);
        });
        dots.appendChild(dot);
      });
      block.appendChild(dots);
      map.appendChild(block);
    });

    // The file rail never changes after build; re-append the cached nodes (which
    // appendChild moves back into place) instead of re-deriving them each render.
    deck.fileNodes.forEach(function (node) {
      map.appendChild(node);
    });
  }

  // Every changed file, listed with its stats — the nothing-hidden invariant in
  // the Map (ADR-0014). Built once from the L3 file panels (which carry the stats);
  // a click returns to document mode on that file so its diff is one step away.
  function buildFileNodes() {
    const files = Array.prototype.slice.call(document.querySelectorAll("details.file"));
    if (!files.length) {
      return [];
    }
    const nodes = [cell("p", "deck-map-label", "Files")];
    files.forEach(function (file) {
      const summary = file.querySelector("summary");
      const stats = summary ? summary.querySelector(".file-stats") : null;
      const pathText = fileSummaryPath(summary, stats);

      const row = document.createElement("button");
      row.type = "button";
      row.className = "deck-file";
      row.setAttribute("title", pathText);

      // Show the basename to fit the rail; the full path is the button's title.
      const slash = pathText.lastIndexOf("/");
      row.appendChild(cell("span", "deck-file-name", slash === -1 ? pathText : pathText.slice(slash + 1)));
      if (stats) {
        row.appendChild(stats.cloneNode(true)); // pre-escaped +N/−M, text only
      }
      row.addEventListener("click", function () {
        setMode("document");
        const target = document.getElementById(file.id);
        if (target) {
          revealElement(target);
          if (typeof target.scrollIntoView === "function") {
            target.scrollIntoView();
          }
        }
      });
      nodes.push(row);
    });
    return nodes;
  }

  // The file's path from its L3 summary: the summary's text with the trailing
  // stats removed. The path arrived pre-escaped (a text node); read it as text.
  function fileSummaryPath(summary, stats) {
    if (!summary) {
      return "";
    }
    const full = summary.textContent;
    const statsText = stats ? stats.textContent : "";
    const cut = statsText ? full.lastIndexOf(statsText) : -1;
    return (cut === -1 ? full : full.slice(0, cut)).trim();
  }

  // Move L0 onto the Stage as the Review Route's stop zero. The deterministic
  // renderer already authored the complete orientation; Deck Mode only relocates
  // that existing node, just as it does for Review Steps.
  function stageOrientation() {
    const orientation = deck.orientation;
    if (!orientation || deck.orientationStaged) {
      showDeck();
      return;
    }
    unstageCurrent();

    const placeholder = cell("span", "deck-home", null);
    orientation.parentNode.insertBefore(placeholder, orientation);
    deck.orientationHome = placeholder;

    deck.stage.textContent = "";
    const host = document.createElement("div");
    host.className = "deck-orientation-host";
    host.appendChild(orientation);
    deck.stage.appendChild(host);

    deck.orientationStaged = true;
    deck.lastStop = orientation;
    deck.stageControlButtons = null;
    deck.status = null;
    showDeck();
    renderMap();
  }

  // Move the step onto the Stage: record where it lived (a hidden placeholder)
  // and its open state, force it open, relocate the element itself, and clone its
  // evidence hunks inline beneath it. Relocation (not cloning) keeps the step's
  // live controls and lets the mode toggle move it back byte-for-byte.
  function stageStep(step) {
    if (!step || step === deck.staged) {
      showDeck();
      return;
    }
    unstageCurrent();

    // Stage bookkeeping lives on the deck record (only one step is ever staged),
    // not as expando properties on the step element that round-trips back into the
    // document: where it came from, and the open state to restore when it returns.
    const placeholder = cell("span", "deck-home", null);
    step.parentNode.insertBefore(placeholder, step);
    deck.stagedHome = placeholder;
    deck.stagedPriorOpen = step.open;
    step.open = true;

    deck.stage.textContent = "";
    deck.stage.appendChild(buildCrumb(step));
    const host = document.createElement("div");
    host.className = "deck-step-host";
    host.appendChild(step); // relocates the live element out of the document flow
    deck.stage.appendChild(host);
    // The oversized disposition control sits below the step card, so the review
    // prompts (inside the card) always stay visible above it (ADR-0014 guardrail).
    deck.stage.appendChild(buildStageControl(step));
    deck.stage.appendChild(buildInlineEvidence(step));

    deck.staged = step;
    deck.lastStaged = step;
    deck.lastStop = step;
    showDeck();
    renderMap();
  }

  // Return the staged step to exactly where it came from and restore its open
  // state — the document is whole again, ready for document mode or a fresh stage.
  function unstageCurrent() {
    if (deck.orientationStaged) {
      const placeholder = deck.orientationHome;
      if (placeholder && placeholder.parentNode) {
        placeholder.parentNode.insertBefore(deck.orientation, placeholder);
        placeholder.parentNode.removeChild(placeholder);
      }
      deck.orientationHome = null;
      deck.orientationStaged = false;
      return;
    }

    const step = deck.staged;
    if (!step) {
      return;
    }
    const placeholder = deck.stagedHome;
    if (placeholder && placeholder.parentNode) {
      placeholder.parentNode.insertBefore(step, placeholder);
      placeholder.parentNode.removeChild(placeholder);
    }
    step.open = deck.stagedPriorOpen;
    deck.stagedHome = null;
    deck.staged = null;
    // While the step was on the Stage it was NOT a child of its thread, so a
    // disposition set from the Stage could not update the document's per-thread
    // progress line (applyDisposition's `closest("section.thread")` was null, and a
    // count then would have missed the relocated step). Now that it is home and the
    // thread is whole again, recompute that thread's progress so document mode is
    // never stale after a round-trip.
    const thread = step.closest("section.thread");
    if (thread) {
      updateThreadProgress(thread);
    }
  }

  // The Stage's breadcrumb: the step's thread (id + title) and the step id.
  function buildCrumb(step) {
    const crumb = cell("div", "deck-crumb", null);
    const thread = step.closest("section.thread");
    const heading = thread ? thread.querySelector("h2") : null;
    const idSource = heading ? heading.querySelector(".thread-id") : null;
    if (idSource) {
      crumb.appendChild(cell("span", "deck-thread-id", idSource.textContent));
    }
    crumb.appendChild(cell("span", "deck-crumb-title", threadTitleText(heading)));
    crumb.appendChild(cell("span", "deck-crumb-step", step.id));
    return crumb;
  }

  // Clone the hunk(s) this step's evidence points at, inline under the Stage
  // card. Each evidence link is an in-page anchor; resolve it to its element and
  // clone it (a hunk section, or a file body for a file-level ref). Cloning keeps
  // the L3 evidence whole in the document and lets several steps cite one hunk.
  function buildInlineEvidence(step) {
    const wrap = cell("div", "deck-evidence", null);
    const seen = Object.create(null);
    const anchors = step.querySelectorAll(".evidence-list a");
    Array.prototype.forEach.call(anchors, function (anchor) {
      const href = anchor.getAttribute("href") || "";
      if (href.charAt(0) !== "#" || href.length < 2) {
        return;
      }
      let id;
      try {
        id = decodeURIComponent(href.slice(1));
      } catch (_err) {
        return; // a malformed anchor addresses nothing
      }
      if (seen[id]) {
        return;
      }
      seen[id] = true;
      const target = document.getElementById(id);
      if (!target) {
        return;
      }
      const figure = cell("figure", "deck-hunk", null);
      figure.appendChild(cell("figcaption", "", anchor.textContent)); // the label, text only
      const clone = evidenceBody(target).cloneNode(true); // already-escaped nodes, text only
      stripIds(clone); // the original keeps the anchors; a clone must not duplicate ids
      figure.appendChild(clone);
      wrap.appendChild(figure);
    });
    if (!wrap.querySelector(".deck-hunk")) {
      wrap.appendChild(cell("p", "deck-evidence-none", "No inline hunk — see the evidence links in the step."));
    }
    return wrap;
  }

  // The node to clone for a resolved evidence anchor: a hunk `<section>` clones
  // whole; a whole-file `<details>` panel contributes just its body (the diff).
  function evidenceBody(target) {
    if (target.tagName === "DETAILS") {
      return target.querySelector(".file-body") || target;
    }
    return target;
  }

  // Remove every id in a cloned subtree so the inline copy never collides with the
  // live L3 element it was cloned from (the original keeps the anchor the evidence
  // link and :target navigation resolve to).
  function stripIds(node) {
    if (node.removeAttribute) {
      node.removeAttribute("id");
    }
    const children = node.children || [];
    for (let i = 0; i < children.length; i++) {
      stripIds(children[i]);
    }
  }

  // Point the toggle at the *other* mode: pressed = currently in deck mode.
  function setToggle(pressed) {
    deck.toggle.textContent = pressed ? "Document view" : "Deck view";
    deck.toggle.setAttribute("aria-pressed", pressed ? "true" : "false");
  }

  // Show the Map + Stage; the document is hidden by CSS while `deck-active` is set.
  function showDeck() {
    deck.mode = "deck";
    document.body.classList.add("deck-active");
    setToggle(true);
    persistUiState(); // mode + staged step (staging routes through here)
  }

  // Return to the single layered document: move the staged step home, clear the
  // Stage, drop the `deck-active` flag. Content, open state, and tints round-trip.
  function showDocument() {
    unstageCurrent();
    deck.mode = "document";
    deck.stage.textContent = "";
    document.body.classList.remove("deck-active");
    setToggle(false);
    renderMap();
    persistUiState(); // the reviewer chose the full document — remember it across a reload
  }

  function setMode(mode) {
    if (mode === "deck") {
      if (deck.staged || deck.orientationStaged) {
        showDeck();
      } else if (deck.lastStop === deck.orientation) {
        stageOrientation();
      } else {
        stageStep(deck.lastStaged || deck.steps[0]);
      }
    } else {
      showDocument();
    }
  }

  // Refresh the deck views after a disposition changes — the Map (dots and
  // fractions) and the oversized Stage control are both derived from the steps'
  // `data-disposition`, so they redraw on the deck's own path. No-op until built.
  function refreshDeck() {
    if (deck) {
      renderMap();
      updateStageControl(deck.staged);
    }
  }

  // --- Deck keyboard flow + Stage dispositions (ADR-0014/0016, issue #68) ----
  //
  // On the Stage, an oversized L/C/F/S control (with visible key hints) sets the
  // staged step's Reviewer Disposition through the very same write path the
  // document-mode controls use — `applyDisposition` (local tint, dots, fractions,
  // in-step + Stage control) and `sendDisposition` (the presence-gated channel;
  // the payload is byte-identical, so the disposition bridge needs no change).
  // Setting a disposition auto-advances to the next *unreviewed* step in Review
  // Route order (never skipping one, never landing on a reviewed step); J/K move
  // one step back/forward freely (reviewed or not). Keys never fire while a
  // typing surface is focused — the step-scoped ask box, or any host input.

  // The Stage control's config, in Review-Route-natural order: each settable
  // disposition with the key that sets it and the visible hint the button renders
  // (key cap + glyph + word — never colour alone, ADR-0014). `looks-right` uses no
  // checkmark (an attest, not an approval — ADR-0016).
  const STAGE_KEYS = [
    { key: "L", disposition: "looks-right", glyph: "●", word: "looks right" },
    { key: "C", disposition: "concern", glyph: "⚠", word: "concern" },
    { key: "F", disposition: "follow-up", glyph: "?", word: "follow-up" },
    { key: "S", disposition: "skipped", glyph: "↷", word: "skip" },
  ];
  // The lowercased-key → disposition lookup the keyboard handler uses, derived from
  // STAGE_KEYS so the vocabulary is declared once (a new disposition adds one row).
  const DISPOSITION_FOR_KEY = Object.create(null);
  STAGE_KEYS.forEach(function (entry) {
    DISPOSITION_FOR_KEY[entry.key.toLowerCase()] = entry.disposition;
  });

  // Keyboard staging must never steal a keystroke meant for a text field — the
  // step-scoped ask box (a <textarea>) or any host input/contenteditable. A
  // non-element target (e.g. the document itself) is never a typing surface.
  function isTypingContext(target) {
    if (!target || typeof target.closest !== "function") {
      return false;
    }
    return !!target.closest("input, textarea, select, [contenteditable]");
  }

  // The oversized Stage control: one big button per settable disposition, each
  // showing its key cap + glyph + word, plus a status line for the "nothing left
  // to advance to" announcement. Rebuilt with the Stage on every stage change, so
  // the button handles live on the deck record only for the current step.
  function buildStageControl(step) {
    const control = cell("div", "deck-control", null);
    control.appendChild(cell("p", "deck-control-label", "Set disposition"));

    const buttons = cell("div", "deck-control-buttons", null);
    deck.stageControlButtons = [];
    STAGE_KEYS.forEach(function (entry) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "deck-control-btn";
      btn.dataset.disposition = entry.disposition; // colour tint keyed off this, ADR-0014
      btn.setAttribute("aria-pressed", "false");
      btn.appendChild(cell("kbd", "deck-key", entry.key)); // the visible key hint
      btn.appendChild(cell("span", "deck-control-glyph", entry.glyph));
      btn.appendChild(cell("span", "deck-control-word", entry.word));
      btn.addEventListener("click", function () {
        disposeStaged(entry.disposition);
      });
      buttons.appendChild(btn);
      deck.stageControlButtons.push(btn);
    });
    control.appendChild(buttons);

    // `role="status"` announces the auto-advance boundary to assistive tech without
    // stealing focus; text only, never markup.
    const status = cell("p", "deck-stage-status", null);
    status.setAttribute("role", "status");
    control.appendChild(status);
    deck.status = status;

    updateStageControl(step);
    return control;
  }

  // Reflect the staged step's disposition on the oversized control. The Stage
  // control and the in-step controls both read `data-disposition` and sync the
  // same way (syncPressed), so they can never disagree — pressing either updates
  // the one source of truth.
  function updateStageControl(step) {
    if (!deck || !deck.stageControlButtons) {
      return;
    }
    syncPressed(deck.stageControlButtons, step ? step.getAttribute("data-disposition") : null);
  }

  function announceStage(message) {
    if (deck && deck.status) {
      deck.status.textContent = message; // text only
    }
  }

  // Set (or clear) the staged step's disposition through the document-mode write
  // path, then auto-advance. Re-selecting the active state clears it to unreviewed
  // (parity with the in-step controls, ADR-0014 guardrail) and does NOT advance —
  // there is nothing to move on from.
  function disposeStaged(disposition) {
    const step = deck && deck.staged;
    if (!step) {
      return;
    }
    // The same toggle-and-write the in-step controls use; only the auto-advance
    // is the Stage's own. A re-select clears to unreviewed — stay put, nothing to
    // move on from — so advance only on a real disposition.
    if (toggleDisposition(step, disposition) === "unreviewed") {
      announceStage("");
      return;
    }
    advanceToNextUnreviewed();
  }

  // Auto-advance target: the next step with no disposition, searching forward in
  // Review Route order and wrapping once (so steps disposed out of order are still
  // reached). It never lands on a reviewed step — only unreviewed ones — and with
  // none left anywhere it stays on the current step and says so.
  function advanceToNextUnreviewed() {
    const steps = deck.steps;
    const start = steps.indexOf(deck.staged);
    // Step through every *other* step once, forward and wrapping (step < length
    // stops before returning to `start`), landing on the first unreviewed one.
    for (let step = 1; step < steps.length; step++) {
      const candidate = steps[(start + step) % steps.length];
      if (!candidate.getAttribute("data-disposition")) {
        stageStep(candidate);
        return;
      }
    }
    announceStage("All steps reviewed — nothing left to advance to.");
  }

  // J/K free navigation: one step forward/back in Review Route order, clamped at
  // the route's ends (no wrap). Unlike auto-advance, this is NOT gated by
  // disposition — it lands on reviewed steps too, so the reviewer can revisit.
  function navigateStage(delta) {
    if (deck.orientationStaged) {
      if (delta > 0) {
        stageStep(deck.steps[0]);
      }
      return;
    }
    const steps = deck.steps;
    const from = steps.indexOf(deck.staged);
    if (from === 0 && delta < 0 && deck.orientation) {
      stageOrientation();
      return;
    }
    const next = from + delta;
    if (from === -1 || next < 0 || next >= steps.length) {
      return;
    }
    stageStep(steps[next]);
  }

  // The single global keydown handler, active only while the deck is showing (the
  // Stage owns the keys; document mode leaves them to the browser). Modifier chords
  // pass through so host/browser shortcuts (⌘K, etc.) are never swallowed.
  function onDeckKeydown(event) {
    if (!deck || deck.mode !== "deck") {
      return;
    }
    if (isTypingContext(event.target)) {
      return;
    }
    if (event.metaKey || event.ctrlKey || event.altKey) {
      return;
    }
    const key = (event.key || "").toLowerCase();
    if (key === "j") {
      event.preventDefault();
      navigateStage(1); // vim-convention: j is down/forward
    } else if (key === "k") {
      event.preventDefault();
      navigateStage(-1); // k is up/backward
    } else if (DISPOSITION_FOR_KEY[key]) {
      event.preventDefault();
      disposeStaged(DISPOSITION_FOR_KEY[key]);
    }
  }

  // --- Run-scoped UI-state store (issue #112) --------------------------------
  //
  // The one sanctioned page mutation — live-evidence injection — writes review.html,
  // which the host answers with an SSE reload that resets the iframe. Disposition
  // tints (re-fetched from dispositions.json) and scroll (host chrome) survive that,
  // but the deck's *ephemeral* state does not: which step is staged, deck vs document
  // mode, a half-typed step question, and document-mode disclosure. This store carries
  // exactly that across the reload, keyed by the artifact path AND the renderer's run
  // identity (`<meta name="brc-run">`), so a regenerated run's state self-invalidates
  // instead of leaking across the clean break.
  //
  // It is deliberately narrow: sessionStorage (per-tab, survives the reload — the same
  // mechanism the host uses for queued pills), served-only (inert on file:// and in the
  // baked record), and never a channel for Reviewer Dispositions (those stay server
  // state, fetched from dispositions.json) or any feedback send. Restore is defensive:
  // a missing/mismatched run identity, a corrupt blob, or a staged-step id that no
  // longer resolves is discarded, never guessed.

  const UI_STORE_KEY_PREFIX = "brc:ui:";

  // The store handle — {backend, key, run} when persistence is live, else null (file://,
  // no sessionStorage, or no run identity stamped). Set once by restoreUiState().
  let uiStore = null;
  // Persistence stays suppressed until the initial restore completes, so the deck's
  // own build-time staging (setMode → stageOrientation) can't clobber stored state
  // before it is read back.
  let uiReady = false;

  function runIdentity() {
    const meta = document.querySelector('meta[name="brc-run"]');
    const content = meta && meta.getAttribute("content");
    return content || null;
  }

  function storageBackend() {
    // file:// is a record, not a review surface — never persist (the deck's gate too).
    if (location.protocol === "file:") {
      return null;
    }
    // Access itself can throw under strict privacy settings; a missing or unusable
    // backend simply means the store is inert, never an error.
    try {
      return window.sessionStorage || null;
    } catch (_err) {
      return null;
    }
  }

  function buildUiStore() {
    const backend = storageBackend();
    if (!backend) {
      return null;
    }
    const run = runIdentity();
    if (!run) {
      return null; // no run identity → inert (nothing keyed, nothing restored)
    }
    return { backend: backend, key: UI_STORE_KEY_PREFIX + (location.pathname || ""), run: run };
  }

  function readUiState() {
    if (!uiStore) {
      return null;
    }
    let raw;
    try {
      raw = uiStore.backend.getItem(uiStore.key);
    } catch (_err) {
      return null;
    }
    if (!raw) {
      return null;
    }
    let parsed;
    try {
      parsed = JSON.parse(raw);
    } catch (_err) {
      return null; // a corrupt blob is discarded, never trusted
    }
    // A run-identity mismatch is the clean-break signal: the stored state belongs to a
    // different diff, so discard it rather than restore stale positions and drafts.
    if (!parsed || typeof parsed !== "object" || parsed.run !== uiStore.run) {
      return null;
    }
    return parsed;
  }

  function writeUiState(state) {
    if (!uiStore) {
      return;
    }
    state.run = uiStore.run;
    try {
      uiStore.backend.setItem(uiStore.key, JSON.stringify(state));
    } catch (_err) {
      // Quota or a read-only backend: persistence is best-effort, never fatal.
    }
  }

  // Non-empty per-step ask drafts, keyed by step id — the reviewer's in-progress text.
  function snapshotDrafts() {
    const drafts = Object.create(null);
    stepElements().forEach(function (step) {
      const input = step.querySelector(".step-ask-input");
      if (input && input.value) {
        drafts[step.id] = input.value;
      }
    });
    return drafts;
  }

  // Ticked review prompts (issue #99), keyed by step id → the ticked prompt indices
  // within that step. Index is stable within a run (prompts are rendered once and the
  // run identity keys the whole store, so a regenerated run's ticks self-invalidate).
  // Ephemeral UI state only — never a Reviewer Disposition, never a feedback send.
  function snapshotTicks() {
    const ticks = Object.create(null);
    stepElements().forEach(function (step) {
      const on = [];
      Array.prototype.forEach.call(
        step.querySelectorAll(".review-prompts li"),
        function (li, index) {
          if (li.classList.contains("ticked")) {
            on.push(index);
          }
        }
      );
      if (on.length) {
        ticks[step.id] = on;
      }
    });
    return ticks;
  }

  // Document-mode disclosure per <details> id. The staged step is force-open on the
  // Stage, so record its *document* truth (stagedPriorOpen), not its transient state.
  function snapshotOpen() {
    const open = Object.create(null);
    document.querySelectorAll("details.step, details.file").forEach(function (panel) {
      if (!panel.id) {
        return;
      }
      open[panel.id] = deck && deck.staged === panel ? deck.stagedPriorOpen : panel.open;
    });
    return open;
  }

  // The reviewer's current stop on the Review Route — "l0" for orientation, else the
  // step id — read from `lastStop` so it holds even in document mode (where nothing is
  // *staged* but the reviewer still has a position the deck returns to). This is what
  // must survive a reload, not the transient `staged`, which is null in document mode.
  function currentStopId() {
    if (!deck || deck.orientationStaged || deck.lastStop === deck.orientation) {
      return "l0";
    }
    const stop = deck.lastStop;
    return stop && stop.id ? stop.id : null;
  }

  // Persist the whole ephemeral deck state. Inert until the initial restore has run
  // (uiReady) and only when a live store and a built deck exist.
  function persistUiState() {
    if (!uiReady || !uiStore || !deck) {
      return;
    }
    writeUiState({
      mode: deck.mode,
      stop: currentStopId(),
      drafts: snapshotDrafts(),
      open: snapshotOpen(),
      ticks: snapshotTicks(),
    });
  }

  // Restore the ephemeral deck state saved before an injection reload. Ordered so the
  // final view matches what the reviewer left: disclosure first (document truth), then
  // drafts, then prompt ticks, then the staged stop, then the mode. Every step is
  // defensive — an unknown id, a stale run, or an out-of-range tick index is discarded,
  // never guessed. Runs once, after the deck is built.
  function restoreUiState() {
    uiStore = buildUiStore();
    if (!uiStore || !deck) {
      return; // inert: nothing to restore, and persistence stays off (uiReady false)
    }
    const state = readUiState();
    if (state) {
      if (state.open && typeof state.open === "object") {
        Object.keys(state.open).forEach(function (id) {
          const panel = document.getElementById(id);
          if (panel && (panel.matches("details.step") || panel.matches("details.file"))) {
            panel.open = !!state.open[id];
          }
        });
      }
      if (state.drafts && typeof state.drafts === "object") {
        stepElements().forEach(function (step) {
          const draft = state.drafts[step.id];
          if (typeof draft === "string") {
            const input = step.querySelector(".step-ask-input");
            if (input) {
              input.value = draft;
            }
          }
        });
      }
      if (state.ticks && typeof state.ticks === "object") {
        stepElements().forEach(function (step) {
          const on = state.ticks[step.id];
          if (!Array.isArray(on)) {
            return;
          }
          const items = step.querySelectorAll(".review-prompts li");
          on.forEach(function (index) {
            if (typeof index === "number" && items[index]) {
              setPromptTick(items[index], true);
            }
          });
        });
      }
      // Stage the reviewer's last stop first — in *either* mode — so the deck's
      // return memory (lastStop/lastStaged) is set through the real staging paths.
      // Then, if the reviewer had toggled to the document, unstage back to it: the
      // memory stays, so a later toggle to the deck lands where they left off.
      if (state.stop === "l0") {
        stageOrientation();
      } else if (typeof state.stop === "string") {
        const target = document.getElementById(state.stop);
        if (target && deck.steps.indexOf(target) !== -1) {
          stageStep(target);
        }
      }
      if (state.mode === "document") {
        setMode("document");
      }
    }
    // Persistence is live from here — snapshot the restored (or default) state so a
    // subsequent reload has a fresh record even if the reviewer changes nothing.
    uiReady = true;
    persistUiState();
  }

  function buildDeck() {
    // file:// is a record, not a review surface — no live session, so no deck
    // (the exact gate dispositions and the ask affordance use).
    if (location.protocol === "file:") {
      return;
    }
    const main = document.querySelector("main");
    if (!main) {
      return;
    }
    const threads = Array.prototype.slice.call(document.querySelectorAll("section.thread"));
    const orientation = main.querySelector("section.l0");
    // Capture each thread's steps (and its static id/title) once, now, while every
    // step still sits in its thread — the Map renders from this stable grouping even
    // after a step is relocated onto the Stage (a relocated step would otherwise
    // vanish from it), and the title never needs re-deriving on a disposition change.
    const groups = threads.map(function (thread) {
      const heading = thread.querySelector("h2");
      const idSource = heading && heading.querySelector(".thread-id");
      return {
        thread: thread,
        steps: stepsIn(thread),
        threadId: idSource ? idSource.textContent : thread.id || "",
        title: threadTitleText(heading),
        impactSummary: heading ? heading.querySelector(".thread-impacts") : null,
        // The renderer-derived per-thread reading weight (issue #100), reused in the
        // Map rather than re-summed in JS — the same posture as the impact summary.
        threadWeight: heading ? heading.querySelector(".thread-weight") : null,
      };
    });
    const steps = [];
    groups.forEach(function (group) {
      group.steps.forEach(function (step) {
        steps.push(step);
      });
    });
    if (!steps.length) {
      return; // nothing to stage — leave the document as-is
    }

    const container = document.createElement("div");
    container.className = "deck";
    const map = document.createElement("nav");
    map.className = "deck-map";
    map.setAttribute("aria-label", "Review map");
    const stageWrap = document.createElement("div");
    stageWrap.className = "deck-stage-wrap";
    const stage = document.createElement("div");
    stage.className = "deck-stage";
    stageWrap.appendChild(stage);
    container.appendChild(map);
    container.appendChild(stageWrap);

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "deck-toggle";
    toggle.addEventListener("click", function () {
      setMode(deck.mode === "deck" ? "document" : "deck");
    });

    deck = {
      map: map,
      stage: stage,
      toggle: toggle,
      groups: groups,
      steps: steps,
      orientation: orientation,
      orientationStaged: false,
      orientationHome: null,
      fileNodes: buildFileNodes(), // static — built once, re-appended each render
      staged: null,
      stagedHome: null,
      stagedPriorOpen: false,
      lastStaged: null,
      lastStop: orientation || steps[0],
      mode: "document",
      stageControlButtons: null, // the current Stage control's disposition buttons
      status: null, // the Stage's `role="status"` announcement line
    };

    // The deck sits after the document; document mode simply hides the deck and
    // shows <main>, deck mode hides <main> and shows the deck (both via CSS).
    main.parentNode.insertBefore(container, main.nextSibling);
    document.body.appendChild(toggle);

    // Single global keydown handler — inert unless the deck is showing and the
    // focus is not in a typing surface (both gated inside onDeckKeydown).
    document.addEventListener("keydown", onDeckKeydown);

    // Served → the deck is the presentation; the full document is one toggle away.
    // setMode → stageStep renders the Map, so no separate initial render is needed.
    setMode("deck");
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("pre.diff").forEach(annotateDiff);

    // Re-clicking an evidence link whose hash is already current fires no
    // hashchange, so reveal on click too (capture: before the browser scrolls).
    document.addEventListener(
      "click",
      function (event) {
        const anchor = event.target && event.target.closest && event.target.closest('a[href^="#"]');
        if (!anchor) {
          return;
        }
        const id = anchor.getAttribute("href").slice(1);
        const target = id && document.getElementById(id);
        // A renderer-authored relates_to link is a Deck navigation affordance:
        // stage that already-rendered Review Step directly, even across threads.
        // All other anchors keep the document's normal reveal behavior below.
        if (
          deck &&
          deck.mode === "deck" &&
          anchor.closest(".step-relations") &&
          STEP_ID.test(id) &&
          target &&
          deck.steps.indexOf(target) !== -1
        ) {
          event.preventDefault();
          event.stopPropagation();
          stageStep(target);
          return;
        }
        if (target) {
          revealElement(target);
        }
      },
      true
    );

    window.addEventListener("hashchange", revealHashTarget);
    revealHashTarget(); // a deep link into a fresh load

    setupDispositions();
    setupStepQuestions();
    setupPromptTicks();
    // Built last: the deck relocates steps that already carry their injected
    // controls, and clones hunk sections the diff rebuild has already annotated.
    buildDeck();
    // A <details> toggle carries no other signal, so persist document-mode disclosure
    // on its own event (capture: the `toggle` event does not bubble). Inert until the
    // restore below turns persistence on, and on file:// (no store) throughout.
    document.addEventListener("toggle", persistUiState, true);
    // Finally, restore any state a prior injection reload left behind — after the deck
    // exists to receive it, and after the ask boxes exist to hold restored drafts.
    restoreUiState();
  });
})();
