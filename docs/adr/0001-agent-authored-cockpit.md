# Structured narrator analysis; deterministic Review Cockpit renderer

The isolated narrator authors `analysis.json`; deterministic code renders that analysis and the collector's escaped fragments into `review.html`. The renderer owns the complete L0-L3 document shape, asset references, derived counts, evidence links, seams, escaping, lint gate, and atomic write. The orchestrating agent never authors or repairs HTML.

**Why:** the product value is the narrator's decomposition and guided reading order, not model-generated markup. Direct HTML authorship made one invocation responsible for prose, schema interpretation, escaping, anchor resolution, structural invariants, and presentation. That produced a large, fragile instruction surface whose failures were felt as missing steps, broken links, inconsistent cockpits, or a review that would not open. A deep file-oriented renderer preserves adaptive narration in `analysis.json` while making presentation predictable and testable.

## Consequences

- `render_cockpit(run_dir: Path) -> Path` is the single rendering interface; the installed skill exposes it through `scripts/render_cockpit.py`.
- The narrator remains adaptive: it chooses threads, Review Steps, prose, evidence references, confidence, and reading order under the validated analysis schema.
- The renderer is intentionally opinionated about presentation. New cockpit fields require a schema and renderer change, not extra prompt instructions.
- A candidate is validated and linted before `review.html` is atomically replaced, so failed regeneration leaves the prior cockpit intact.
