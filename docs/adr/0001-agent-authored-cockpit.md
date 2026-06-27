# Agent authors the Review Cockpit HTML; no template engine

The cockpit `review.html` is written directly by the agent (guided by Lavish playbooks and a bundled `cockpit.css`), not rendered from a fixed template. The original handoff proposed a Python + Jinja2 pipeline (`generate_review_html.py`, `review.html.j2`) fed by `analysis.json`; we dropped both.

**Why:** a fixed template can't adapt to the *shape* of a diff (one risky file vs. a broad mechanical refactor tell very different stories), it duplicates what Lavish's playbooks already do, and it's a rendering pipeline to maintain. Lavish's whole premise is that agents produce rich HTML well. `analysis.json` is still produced as structured intermediate reasoning and as the substrate the feedback loop answers from — only the *rendering* is agent-driven.

## Consequences

- No `templates/` or render script in the skill; the deterministic layer is diff collection only.
- Per-review HTML is **nondeterministic and more token-expensive** than stamping a template. Accepted because this is a review *aid*, not an automated gate.
- The agent writes structure and prose only — see [ADR-0002](./0002-deterministic-escape-boundary.md): untrusted data is injected by scripts, not hand-written by the agent.
