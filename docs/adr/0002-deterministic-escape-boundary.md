# Untrusted data crosses a deterministic escape boundary; the agent writes only the frame

The agent authors the cockpit's structure and prose, but it **never hand-interpolates untrusted strings**. Diff bodies, file paths, commit messages, branch names, and echoed reviewer feedback are emitted by the deterministic scripts as HTML-escaped fragments (Python stdlib `html.escape`) and injected at a fixed seam. Defense-in-depth: the cockpit ships a strict `Content-Security-Policy`, keeps all behavior in a vendored `app.js` (no inline JS), and a deterministic post-write lint fails the build if untrusted regions contain unescaped `<`/`>` or a remote `src`/`href` appears under `styling: vendored`.

**Why:** a malicious or careless branch can put `<script>` in a filename, a diff hunk, or a commit message. Relying on the agent to remember to escape every interpolation is a silent-failure XSS risk. Escaping must be mechanical and unconditional, not a matter of agent discretion. This refines [ADR-0001](./0001-agent-authored-cockpit.md): the agent's creative freedom is bounded to layout and explanation; anything attacker-controlled is escaped by code and cannot execute by construction.

## Consequences

- The diff collector pre-renders the diff into a safe `<pre>` fragment; the close/bake step escapes `qa.jsonl` feedback before writing it into `review.html`.
- No third-party sanitizer dependency — stdlib escaping + CSP + no-inline-JS + post-write lint is sufficient.
- The agent cannot freely inline raw diff content even when convenient; it must use the pre-escaped fragments.
