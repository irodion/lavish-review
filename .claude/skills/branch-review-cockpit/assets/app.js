// Branch Review Cockpit — vendored behaviour.
//
// All cockpit behaviour lives here, never inline, so the cockpit ships a strict
// CSP (`script-src 'self'`) that forbids inline script (issue #4). This script
// treats the diff strictly as TEXT: it rebuilds each line with createElement +
// textContent and NEVER assigns attacker-derived strings to innerHTML, so a
// `<script>` hidden in a diff hunk can only ever render as visible characters.
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

(function () {
  "use strict";

  function colourizeDiff(pre) {
    const lines = pre.textContent.split("\n");
    pre.textContent = "";
    for (let i = 0; i < lines.length; i++) {
      const text = lines[i];
      const span = document.createElement("span");
      const head = text.charCodeAt(0); // 43:'+' 45:'-' 64:'@'
      if (head === 43) {
        span.className = "ln-add";
      } else if (head === 45) {
        span.className = "ln-del";
      } else if (head === 64) {
        span.className = "ln-hunk";
      }
      span.textContent = text; // text only — never markup
      pre.appendChild(span);
      if (i < lines.length - 1) {
        pre.appendChild(document.createTextNode("\n"));
      }
    }
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
  const CLAIM_ID = /^t\d+\.c\d+$/;
  const SETTABLE = ["verified", "concern", "question-open"];
  const LABELS = { verified: "✓ verified", concern: "⚠ concern", "question-open": "? question" };

  function claimElements() {
    return Array.prototype.filter.call(
      document.querySelectorAll("details.claim"),
      function (el) {
        return CLAIM_ID.test(el.id);
      }
    );
  }

  // Queue the update through the SDK. `window.lavish` is checked at interaction
  // time — the SDK script loads after ours (#38). The per-claim queueKey collapses
  // rapid re-clicks to the last state; sends are presence-gated by the host, so
  // delivery is batched/eventually-consistent within the session (also #38).
  function sendDisposition(claimId, disposition) {
    const sdk = window.lavish;
    if (!sdk || typeof sdk.queuePrompt !== "function") {
      return;
    }
    sdk.queuePrompt("Disposition set: " + claimId + " -> " + disposition, {
      tag: "choice",
      text: "disposition:" + disposition,
      queueKey: "disposition:" + claimId,
      data: { kind: "disposition", claim: claimId, disposition: disposition },
    });
    if (typeof sdk.sendQueuedPrompts === "function") {
      sdk.sendQueuedPrompts();
    }
  }

  function applyDisposition(claim, disposition) {
    if (disposition === "unreviewed") {
      claim.removeAttribute("data-disposition");
    } else {
      claim.setAttribute("data-disposition", disposition);
    }
    const buttons = claim.querySelectorAll(".disposition-controls button");
    Array.prototype.forEach.call(buttons, function (btn) {
      btn.setAttribute("aria-pressed", btn.dataset.disposition === disposition ? "true" : "false");
    });
    const thread = claim.closest("section.thread");
    if (thread) {
      updateThreadProgress(thread);
    }
  }

  function updateThreadProgress(thread) {
    const claims = Array.prototype.filter.call(
      thread.querySelectorAll("details.claim"),
      function (el) {
        return CLAIM_ID.test(el.id);
      }
    );
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
        // Re-clicking the active state clears it back to unreviewed.
        const active = claim.getAttribute("data-disposition") === disposition;
        const next = active ? "unreviewed" : disposition;
        applyDisposition(claim, next);
        sendDisposition(claim.id, next);
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

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("pre.diff").forEach(colourizeDiff);

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
  });
})();
