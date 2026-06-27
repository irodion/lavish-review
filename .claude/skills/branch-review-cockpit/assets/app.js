// Branch Review Cockpit — vendored behaviour (walking skeleton, issue #3).
//
// All cockpit behaviour lives here, never inline, so the hardening slice
// (issue #4) can ship a strict CSP that forbids inline script. This script
// treats the diff strictly as TEXT: it rebuilds each line with createElement +
// textContent and NEVER assigns attacker-derived strings to innerHTML, so a
// `<script>` hidden in a diff hunk can only ever render as visible characters.

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

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("pre.diff").forEach(colourizeDiff);
  });
})();
