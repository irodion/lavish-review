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
  });
})();
