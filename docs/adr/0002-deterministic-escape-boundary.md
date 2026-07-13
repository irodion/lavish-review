# Untrusted data crosses a deterministic escape boundary owned by the renderer

The narrator authors structured prose, never markup. The renderer escapes every narrator free-text field and combines it with collector-produced escaped fragments for diff bodies, file paths, commit messages, branch names, and goal evidence. Echoed reviewer feedback is escaped by the close/bake and evidence-injection paths. Defense-in-depth: the cockpit ships a bounded `Content-Security-Policy`, keeps behavior in vendored `app.js` (no inline JS), and a deterministic lint rejects malformed escape regions, unsafe script constructs, weak policy, structural drift, or a remote `src`/`href` under `styling: vendored`.

**Why:** a malicious or careless branch can place markup in any collected or narrated string. Escaping is a representation concern and must be mechanical, unconditional, and centralized. This refines [ADR-0001](./0001-agent-authored-cockpit.md): the narrator's creative freedom lives in structured explanation and reading order; the renderer owns how that content reaches HTML.

## Consequences

- The diff collector pre-renders the diff into a safe `<pre>` fragment; the close/bake step escapes `qa.jsonl` feedback before writing it into `review.html`.
- No third-party sanitizer dependency — stdlib escaping + CSP + no-inline-JS + post-write lint is sufficient.
- Agents never interpolate review content into HTML; renderer and mutation helpers are the only write paths.
