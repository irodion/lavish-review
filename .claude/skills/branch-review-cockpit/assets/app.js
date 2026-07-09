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
// can grab the anchor, and `:target` navigation (from a claim's evidence link)
// highlights the row. The rebuild is text-only — same discipline as before, so a
// `<script>` in a hunk still renders as characters — and it runs identically in
// the served cockpit and the baked/portable `file://` copy.
//
// Layered cockpit (ADR-0009, issue #39): disclosure is native <details> — no JS
// needed to descend. This script only adds navigation glue: following a claim's
// evidence link (or any #anchor) opens the target's ancestor <details> chain so a
// deep link never lands on a collapsed, invisible element. The hash is used solely
// as a getElementById key — never interpolated into markup or selectors.
//
// Reviewer dispositions (ADR-0012, issue #42): each claim gets JS-injected
// controls (verified / concern / question open) that mark the claim locally,
// update the thread's progress line, and queue a structured update through the
// Lavish SDK (`window.lavish.queuePrompt` with a `data` payload and a per-claim
// `queueKey` — the channel the #38 spike verified) for the loop agent to persist.
// State is restored on load by fetching `dispositions.json` beside the cockpit.
// Everything is rendered with createElement/textContent from closed vocabularies —
// no attacker-derived string ever reaches markup. On `file://` (a portable or
// baked artifact) there is no live session, so the controls are not rendered.
//
// Claim-scoped questions (ADR-0015, issue #65): each claim also gets a JS-injected
// ask affordance that queues the reviewer's question through the *same* presence-
// gated channel, carrying the claim id as structured data
// (`{kind: "claim-question", claim}`, `tag: "message"`, per-claim `queueKey` so
// rapid edits collapse) — no DOM selector to resolve. The loop answers it grounded
// in that claim; the exchange bakes into the Q&A Log like any chat question (it is
// conversation, not state — no store, no apply step). The claim id is a closed
// vocabulary (matched against `CLAIM_ID`); the reviewer's free-text question only
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

  // --- Reviewer dispositions (ADR-0012) -------------------------------------

  // Closed vocabularies: these strings are the ONLY values that ever reach the
  // DOM or the feedback channel — reviewer intent is expressed by choosing one.
  // Step ids since review-analysis/0.4 (ADR-0016): `tN.sN`. The L2 panel's DOM hook
  // is still `class="claim"` (renamed to `step` with the deck reframe, #88), but its
  // id is a step id — so this filter must accept `tN.sN`, not the old `tN.cN`.
  const CLAIM_ID = /^t\d+\.s\d+$/;
  const SETTABLE = ["verified", "concern", "question-open"];
  const LABELS = { verified: "✓ verified", concern: "⚠ concern", "question-open": "? question" };

  // Every L2 panel under `root` (the document, or one thread) whose id is in the
  // closed `t\d+.s\d+` vocabulary — the one filter the dispositions, questions, and
  // deck all share.
  function claimsIn(root) {
    return Array.prototype.filter.call(root.querySelectorAll("details.claim"), function (el) {
      return CLAIM_ID.test(el.id);
    });
  }

  function claimElements() {
    return claimsIn(document);
  }

  // Queue one prompt through the SDK and flush it. `window.lavish` is checked at
  // interaction time — the SDK script loads after ours (#38). Sends are presence-
  // gated by the host, so delivery is batched/eventually-consistent within the
  // session (also #38). Returns false when there is no live SDK to accept the
  // prompt, so a caller can tell the reviewer instead of dropping it silently.
  // The shared seam for every structured feedback send — a disposition update and
  // a claim-scoped question differ only in the payload they pass here.
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

  // The per-claim queueKey collapses rapid re-clicks to the last state.
  function sendDisposition(claimId, disposition) {
    queueToSdk("Disposition set: " + claimId + " -> " + disposition, {
      tag: "choice",
      text: "disposition:" + disposition,
      queueKey: "disposition:" + claimId,
      data: { kind: "disposition", claim: claimId, disposition: disposition },
    });
  }

  // Reflect a claim's disposition onto a set of controls keyed by data-disposition:
  // `aria-pressed` is true only on the button whose disposition is the current one.
  // Shared by the in-claim summary controls and the oversized Stage control.
  function syncPressed(buttons, current) {
    Array.prototype.forEach.call(buttons, function (btn) {
      btn.setAttribute("aria-pressed", btn.dataset.disposition === current ? "true" : "false");
    });
  }

  // The one disposition write rule, shared by the in-claim controls and the Stage:
  // re-selecting the active state clears it back to unreviewed, and every local
  // write is mirrored to the feedback channel. Returns the resulting state so a
  // caller (the Stage) can decide whether to auto-advance.
  function toggleDisposition(claim, disposition) {
    const active = claim.getAttribute("data-disposition") === disposition;
    const next = active ? "unreviewed" : disposition;
    applyDisposition(claim, next);
    sendDisposition(claim.id, next);
    return next;
  }

  function applyDisposition(claim, disposition) {
    if (disposition === "unreviewed") {
      claim.removeAttribute("data-disposition");
    } else {
      claim.setAttribute("data-disposition", disposition);
    }
    syncPressed(claim.querySelectorAll(".disposition-controls button"), disposition);
    const thread = claim.closest("section.thread");
    if (thread) {
      updateThreadProgress(thread);
    }
    // Keep the deck views (Map dots and fractions, the oversized Stage control) in
    // step with the disposition (no-op in document mode / before the deck is built).
    refreshDeck();
  }

  function updateThreadProgress(thread) {
    const claims = claimsIn(thread);
    if (!claims.length) {
      return;
    }
    let reviewed = 0;
    let concerns = 0;
    claims.forEach(function (el) {
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
    let text = reviewed + "/" + claims.length + " reviewed";
    if (concerns) {
      text += " · " + concerns + " concern" + (concerns === 1 ? "" : "s");
    }
    progress.textContent = text; // text only — never markup
    progress.classList.toggle("has-concern", concerns > 0);
  }

  function injectDispositionControls(claim) {
    const summary = claim.querySelector("summary");
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
        toggleDisposition(claim, disposition);
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
        claimElements().forEach(function (claim) {
          const value = entries[claim.id];
          if (typeof value === "string" && SETTABLE.indexOf(value) !== -1) {
            applyDisposition(claim, value);
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
    const claims = claimElements();
    if (!claims.length) {
      return;
    }
    claims.forEach(injectDispositionControls);
    document.querySelectorAll("section.thread").forEach(updateThreadProgress);
    loadDispositions();
  }

  // --- Claim-scoped questions (ADR-0015) ------------------------------------

  // Queue the reviewer's question through the SDK, carrying the claim id as
  // structured data — the same presence-gated channel dispositions use, keyed by a
  // per-claim `queueKey` so a rapid edit-and-resend collapses to the latest text.
  // Unlike a disposition (a `tag: "choice"` state update), a question is a plain
  // `tag: "message"` — it flows into the Q&A Log and bakes like any chat question,
  // never filtered out as state. Returns false (via `queueToSdk`) when there is no
  // live SDK, so the caller can say so instead of dropping the question silently.
  function sendClaimQuestion(claimId, text) {
    return queueToSdk(text, {
      tag: "message",
      queueKey: "question:" + claimId, // collapses rapid edits, like dispositions
      data: { kind: "claim-question", claim: claimId },
    });
  }

  function injectAskControl(claim) {
    const body = claim.querySelector(".claim-body");
    if (!body || body.querySelector(".claim-ask")) {
      return;
    }
    const group = document.createElement("div");
    group.className = "claim-ask";

    // A status line, updated with textContent only — never markup. `role="status"`
    // announces "Sent"/"No live session" to assistive tech without stealing focus.
    const status = document.createElement("span");
    status.className = "claim-ask-status";
    status.setAttribute("role", "status");

    const input = document.createElement("textarea");
    input.className = "claim-ask-input";
    input.rows = 2;
    // A fixed placeholder + aria-label — the claim id is a closed vocabulary
    // (CLAIM_ID), so it is safe to name; the reviewer's own text stays in `.value`,
    // never in markup. The aria-label survives once the placeholder disappears on
    // typing, keeping an accessible name for the field.
    input.setAttribute("aria-label", "Ask about " + claim.id);
    input.setAttribute("placeholder", "Ask about " + claim.id + "…");

    const send = document.createElement("button");
    send.type = "button";
    send.className = "claim-ask-send";
    send.textContent = "Ask"; // fixed label, never derived text

    function submit() {
      const text = input.value.trim();
      if (!text) {
        return;
      }
      if (sendClaimQuestion(claim.id, text)) {
        input.value = "";
        status.textContent = "Sent — the answer appears in the chat.";
      } else {
        // No SDK: a portable copy served without a live session. Keep the text so
        // the reviewer loses nothing; just say it could not be sent.
        status.textContent = "No live session — question not sent.";
      }
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

    group.appendChild(input);
    group.appendChild(send);
    group.appendChild(status);
    body.appendChild(group);
  }

  function setupClaimQuestions() {
    // Same served-only gate as dispositions: on file:// there is no loop to answer,
    // so a claim carries no ask affordance (a record, not a review surface).
    if (location.protocol === "file:") {
      return;
    }
    claimElements().forEach(injectAskControl);
  }

  // --- Deck Mode (ADR-0014) -------------------------------------------------
  //
  // When the cockpit is *served* (the same presence gate as dispositions), the
  // vendored script re-presents the L0–L3 document as a **Map** (threads in
  // Review Route order with one disposition-tinted dot per claim, the changed
  // files with their stats, overall progress) beside a **Stage** (one claim at a
  // time, its evidence hunk shown inline). Document mode is one visible toggle
  // away and is the only mode on `file://`; the baked record stays the document,
  // unchanged (ADR-0014).
  //
  // The deck is built strictly by RELOCATING and CLONING nodes already in the
  // document DOM — never by constructing markup from strings for untrusted data.
  // The Stage *moves* the claim's own `<details class="claim">` element (so its
  // injected disposition controls, ask affordance, open state, and disposition
  // tint travel with it and the mode toggle round-trips losslessly), leaving a
  // hidden placeholder to move it back to; the inline evidence *clones* the
  // claim's already-annotated hunk sections. Because every deck node is either a
  // fixed-vocabulary element built with createElement/textContent or a clone of
  // an already-escaped document node, a `<script>` hidden in a diff can still
  // only ever render as visible text — the same discipline the diff rebuild uses.

  // The deck's live state, or null until the deck is built (file:// / no claims).
  let deck = null;

  // Disposition tallies over a set of claims — reviewed/verified/concern/question.
  function dispositionCounts(claims) {
    const totals = { reviewed: 0, verified: 0, concern: 0, "question-open": 0 };
    claims.forEach(function (claim) {
      const state = claim.getAttribute("data-disposition");
      if (state) {
        totals.reviewed++;
      }
      if (state === "verified" || state === "concern" || state === "question-open") {
        totals[state]++;
      }
    });
    return totals;
  }

  // A thread heading's title text, with the id/chip/progress spans stripped —
  // read off a detached clone so the live heading is never disturbed. text only.
  function threadTitleText(heading) {
    if (!heading) {
      return "";
    }
    const clone = heading.cloneNode(true);
    Array.prototype.forEach.call(
      clone.querySelectorAll(".thread-id, .chip, .thread-progress"),
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
  // currently-staged claim (relocated onto the Stage) in its thread's dots and count.
  function renderMap() {
    const map = deck.map;
    map.textContent = "";

    const overall = dispositionCounts(deck.claims);
    const progress = cell("div", "deck-progress", null);
    progress.appendChild(cell("span", "deck-tally", overall.reviewed + "/" + deck.claims.length + " reviewed"));
    progress.appendChild(countBadge("verified", "✓", overall.verified));
    progress.appendChild(countBadge("concern", "⚠", overall.concern));
    progress.appendChild(countBadge("question-open", "?", overall["question-open"]));
    map.appendChild(progress);

    map.appendChild(cell("p", "deck-map-label", "Threads — review route"));

    deck.groups.forEach(function (group) {
      const claims = group.claims;
      if (!claims.length) {
        return;
      }
      const block = cell("div", "deck-thread-block", null);

      const threadButton = document.createElement("button");
      threadButton.type = "button";
      threadButton.className = "deck-thread";
      threadButton.appendChild(cell("span", "deck-thread-id", group.threadId));
      threadButton.appendChild(cell("span", "deck-thread-title", group.title));
      const counts = dispositionCounts(claims);
      threadButton.appendChild(cell("span", "deck-thread-frac", counts.reviewed + "/" + claims.length));
      // Staging a thread lands on its first claim — the entry to that leg of the route.
      threadButton.addEventListener("click", function () {
        stageClaim(claims[0]);
      });
      block.appendChild(threadButton);

      const dots = cell("div", "deck-dots", null);
      claims.forEach(function (claim) {
        const dot = document.createElement("button");
        dot.type = "button";
        dot.className = "deck-dot";
        dot.dataset.claim = claim.id;
        const state = claim.getAttribute("data-disposition");
        if (state) {
          dot.setAttribute("data-disposition", state);
        }
        if (claim === deck.staged) {
          dot.classList.add("current");
        }
        dot.setAttribute("title", claim.id);
        dot.setAttribute("aria-label", "Stage claim " + claim.id);
        dot.addEventListener("click", function () {
          stageClaim(claim);
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

  // Move the claim onto the Stage: record where it lived (a hidden placeholder)
  // and its open state, force it open, relocate the element itself, and clone its
  // evidence hunks inline beneath it. Relocation (not cloning) keeps the claim's
  // live controls and lets the mode toggle move it back byte-for-byte.
  function stageClaim(claim) {
    if (!claim || claim === deck.staged) {
      showDeck();
      return;
    }
    unstageCurrent();

    // Stage bookkeeping lives on the deck record (only one claim is ever staged),
    // not as expando properties on the claim element that round-trips back into the
    // document: where it came from, and the open state to restore when it returns.
    const placeholder = cell("span", "deck-home", null);
    claim.parentNode.insertBefore(placeholder, claim);
    deck.stagedHome = placeholder;
    deck.stagedPriorOpen = claim.open;
    claim.open = true;

    deck.stage.textContent = "";
    deck.stage.appendChild(buildCrumb(claim));
    const host = document.createElement("div");
    host.className = "deck-claim-host";
    host.appendChild(claim); // relocates the live element out of the document flow
    deck.stage.appendChild(host);
    // The oversized V/C/Q control sits below the claim card, so the challenge
    // questions (inside the card) always stay visible above it (ADR-0014 guardrail).
    deck.stage.appendChild(buildStageControl(claim));
    deck.stage.appendChild(buildInlineEvidence(claim));

    deck.staged = claim;
    deck.lastStaged = claim;
    showDeck();
    renderMap();
  }

  // Return the staged claim to exactly where it came from and restore its open
  // state — the document is whole again, ready for document mode or a fresh stage.
  function unstageCurrent() {
    const claim = deck.staged;
    if (!claim) {
      return;
    }
    const placeholder = deck.stagedHome;
    if (placeholder && placeholder.parentNode) {
      placeholder.parentNode.insertBefore(claim, placeholder);
      placeholder.parentNode.removeChild(placeholder);
    }
    claim.open = deck.stagedPriorOpen;
    deck.stagedHome = null;
    deck.staged = null;
    // While the claim was on the Stage it was NOT a child of its thread, so a
    // disposition set from the Stage could not update the document's per-thread
    // progress line (applyDisposition's `closest("section.thread")` was null, and a
    // count then would have missed the relocated claim). Now that it is home and the
    // thread is whole again, recompute that thread's progress so document mode is
    // never stale after a round-trip.
    const thread = claim.closest("section.thread");
    if (thread) {
      updateThreadProgress(thread);
    }
  }

  // The Stage's breadcrumb: the claim's thread (id + title) and the claim id.
  function buildCrumb(claim) {
    const crumb = cell("div", "deck-crumb", null);
    const thread = claim.closest("section.thread");
    const heading = thread ? thread.querySelector("h2") : null;
    const idSource = heading ? heading.querySelector(".thread-id") : null;
    if (idSource) {
      crumb.appendChild(cell("span", "deck-thread-id", idSource.textContent));
    }
    crumb.appendChild(cell("span", "deck-crumb-title", threadTitleText(heading)));
    crumb.appendChild(cell("span", "deck-crumb-claim", claim.id));
    return crumb;
  }

  // Clone the hunk(s) this claim's evidence points at, inline under the Stage
  // card. Each evidence link is an in-page anchor; resolve it to its element and
  // clone it (a hunk section, or a file body for a file-level ref). Cloning keeps
  // the L3 evidence whole in the document and lets several claims cite one hunk.
  function buildInlineEvidence(claim) {
    const wrap = cell("div", "deck-evidence", null);
    const seen = Object.create(null);
    const anchors = claim.querySelectorAll(".evidence-list a");
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
      wrap.appendChild(cell("p", "deck-evidence-none", "No inline hunk — see the evidence links in the claim."));
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
  }

  // Return to the single layered document: move the staged claim home, clear the
  // Stage, drop the `deck-active` flag. Content, open state, and tints round-trip.
  function showDocument() {
    unstageCurrent();
    deck.mode = "document";
    deck.stage.textContent = "";
    document.body.classList.remove("deck-active");
    setToggle(false);
    renderMap();
  }

  function setMode(mode) {
    if (mode === "deck") {
      if (deck.staged) {
        showDeck();
      } else {
        stageClaim(deck.lastStaged || deck.claims[0]);
      }
    } else {
      showDocument();
    }
  }

  // Refresh the deck views after a disposition changes — the Map (dots and
  // fractions) and the oversized Stage control are both derived from the claims'
  // `data-disposition`, so they redraw on the deck's own path. No-op until built.
  function refreshDeck() {
    if (deck) {
      renderMap();
      updateStageControl(deck.staged);
    }
  }

  // --- Deck keyboard flow + Stage dispositions (ADR-0014, issue #68) ---------
  //
  // On the Stage, an oversized V/C/Q control (with visible key hints) sets the
  // staged claim's Reviewer Disposition through the very same write path the
  // document-mode controls use — `applyDisposition` (local tint, dots, fractions,
  // in-claim + Stage control) and `sendDisposition` (the presence-gated channel;
  // the payload is byte-identical, so the disposition bridge needs no change).
  // Setting a disposition auto-advances to the next *unreviewed* claim in Review
  // Route order (never skipping one, never landing on a reviewed claim); J/K move
  // one claim back/forward freely (reviewed or not). Keys never fire while a
  // typing surface is focused — the claim-scoped ask box, or any host input.

  // The Stage control's config, in Review-Route-natural order: each settable
  // disposition with the key that sets it and the visible hint the button renders
  // (key cap + glyph + word — never colour alone, ADR-0014).
  const STAGE_KEYS = [
    { key: "V", disposition: "verified", glyph: "✓", word: "verified" },
    { key: "C", disposition: "concern", glyph: "⚠", word: "concern" },
    { key: "Q", disposition: "question-open", glyph: "?", word: "question" },
  ];
  // The lowercased-key → disposition lookup the keyboard handler uses, derived from
  // STAGE_KEYS so the vocabulary is declared once (a new disposition adds one row).
  const DISPOSITION_FOR_KEY = Object.create(null);
  STAGE_KEYS.forEach(function (entry) {
    DISPOSITION_FOR_KEY[entry.key.toLowerCase()] = entry.disposition;
  });

  // Keyboard staging must never steal a keystroke meant for a text field — the
  // claim-scoped ask box (a <textarea>) or any host input/contenteditable. A
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
  // the button handles live on the deck record only for the current claim.
  function buildStageControl(claim) {
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

    updateStageControl(claim);
    return control;
  }

  // Reflect the staged claim's disposition on the oversized control. The Stage
  // control and the in-claim controls both read `data-disposition` and sync the
  // same way (syncPressed), so they can never disagree — pressing either updates
  // the one source of truth.
  function updateStageControl(claim) {
    if (!deck || !deck.stageControlButtons) {
      return;
    }
    syncPressed(deck.stageControlButtons, claim ? claim.getAttribute("data-disposition") : null);
  }

  function announceStage(message) {
    if (deck && deck.status) {
      deck.status.textContent = message; // text only
    }
  }

  // Set (or clear) the staged claim's disposition through the document-mode write
  // path, then auto-advance. Re-selecting the active state clears it to unreviewed
  // (parity with the in-claim controls, ADR-0014 guardrail) and does NOT advance —
  // there is nothing to move on from.
  function disposeStaged(disposition) {
    const claim = deck && deck.staged;
    if (!claim) {
      return;
    }
    // The same toggle-and-write the in-claim controls use; only the auto-advance
    // is the Stage's own. A re-select clears to unreviewed — stay put, nothing to
    // move on from — so advance only on a real disposition.
    if (toggleDisposition(claim, disposition) === "unreviewed") {
      announceStage("");
      return;
    }
    advanceToNextUnreviewed();
  }

  // Auto-advance target: the next claim with no disposition, searching forward in
  // Review Route order and wrapping once (so claims disposed out of order are still
  // reached). It never lands on a reviewed claim — only unreviewed ones — and with
  // none left anywhere it stays on the current claim and says so.
  function advanceToNextUnreviewed() {
    const claims = deck.claims;
    const start = claims.indexOf(deck.staged);
    // Step through every *other* claim once, forward and wrapping (step < length
    // stops before returning to `start`), landing on the first unreviewed one.
    for (let step = 1; step < claims.length; step++) {
      const candidate = claims[(start + step) % claims.length];
      if (!candidate.getAttribute("data-disposition")) {
        stageClaim(candidate);
        return;
      }
    }
    announceStage("All claims reviewed — nothing left to advance to.");
  }

  // J/K free navigation: one claim forward/back in Review Route order, clamped at
  // the route's ends (no wrap). Unlike auto-advance, this is NOT gated by
  // disposition — it lands on reviewed claims too, so the reviewer can revisit.
  function navigateStage(delta) {
    const claims = deck.claims;
    const from = claims.indexOf(deck.staged);
    const next = from + delta;
    if (from === -1 || next < 0 || next >= claims.length) {
      return;
    }
    stageClaim(claims[next]);
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
    // Capture each thread's claims (and its static id/title) once, now, while every
    // claim still sits in its thread — the Map renders from this stable grouping even
    // after a claim is relocated onto the Stage (a relocated claim would otherwise
    // vanish from it), and the title never needs re-deriving on a disposition change.
    const groups = threads.map(function (thread) {
      const heading = thread.querySelector("h2");
      const idSource = heading && heading.querySelector(".thread-id");
      return {
        thread: thread,
        claims: claimsIn(thread),
        threadId: idSource ? idSource.textContent : thread.id || "",
        title: threadTitleText(heading),
      };
    });
    const claims = [];
    groups.forEach(function (group) {
      group.claims.forEach(function (claim) {
        claims.push(claim);
      });
    });
    if (!claims.length) {
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
      claims: claims,
      fileNodes: buildFileNodes(), // static — built once, re-appended each render
      staged: null,
      stagedHome: null,
      stagedPriorOpen: false,
      lastStaged: null,
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
    // setMode → stageClaim renders the Map, so no separate initial render is needed.
    setMode("deck");
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("pre.diff").forEach(annotateDiff);

    // Re-clicking an evidence link whose hash is already current fires no
    // hashchange, so reveal on click too (capture: before the browser scrolls).
    document.addEventListener(
      "click",
      function (event) {
        const anchor = event.target && event.target.closest && event.target.closest("a[href^='#']");
        if (!anchor) {
          return;
        }
        const id = anchor.getAttribute("href").slice(1);
        const target = id && document.getElementById(id);
        if (target) {
          revealElement(target);
        }
      },
      true
    );

    window.addEventListener("hashchange", revealHashTarget);
    revealHashTarget(); // a deep link into a fresh load

    setupDispositions();
    setupClaimQuestions();
    // Built last: the deck relocates claims that already carry their injected
    // controls, and clones hunk sections the diff rebuild has already annotated.
    buildDeck();
  });
})();
