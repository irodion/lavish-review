# Deck Mode is a client-side presentation of the single document cockpit

The layered cockpit ([ADR-0009](./0009-layered-claim-evidence-cockpit.md)) fixed
*what* the reviewer judges — threads and claims instead of a wall of diff — but
kept the *how* a single long document. A visual-direction prototype (four
variants over real review data) surfaced a better working posture: reviewers
read a change piece by piece, the way they read any PR, and the scarce thing
mid-review is a fast loop of *judge this claim → move to the next*. The winning
variant pairs a persistent **Map** (the whole: threads in route order with
per-claim disposition dots, files with change-size bars, overall progress) with
a **Stage** (the piece: one claim at a time — kind and judgment chips, challenge
questions, its evidence hunk inline, an oversized disposition control with
keyboard flow). The obvious implementations — a second generated artifact, or a
served app that renders `analysis.json` — would fork the cockpit's identity and
break the guarantees the skill is built on ([ADR-0001](./0001-agent-authored-cockpit.md)
agent-authored single artifact, [ADR-0002](./0002-deterministic-escape-boundary.md)
escape boundary, the bake's self-contained record).

**Decision.** Deck Mode is a **presentation mode, not a second artifact**. The
agent-authored L0–L3 document remains the single source of truth, the thing the
linter checks, and the form the baked `file://` record keeps. When the cockpit
is *served* (a live review through the host), the vendored script builds the
Map and Stage **by relocating and cloning nodes already in the document DOM** —
it never constructs markup from strings for untrusted data, extending the
text-only discipline the diff colourizer established. Document mode stays one
visible toggle away (and remains the only mode on `file://`), so the full
layered document — and with it the nothing-hidden invariant — is never more
than a step from any claim.

Supporting decisions that make the Stage precise:

- **Hunk anchors.** The Escape Boundary emits deterministic per-hunk ids inside
  each per-file fragment and a hunk index in the fragments manifest; diff
  rendering upgrades client-side to line-numbered, hunk-anchored output (still
  a text-only rebuild). Evidence references may address a hunk
  (`{path, hunk?}`, analysis schema 0.3), so a claim's Stage card shows the
  exact hunk that substantiates it.
- **Structural lint rules.** The deck consumes the document's structure, so the
  Cockpit Linter gains structural tripwires: every evidence anchor resolves,
  claim ids are unique and match the analysis, required seams are present. What
  was instruction-enforced becomes lint-enforced.
- **Judgment-color discipline.** Color is reserved for judgment signals — risk
  level, confidence, reviewer disposition — on a palette validated for contrast
  and CVD on both dark and light surfaces; claim *kinds* are neutral chips.
  Every colored chip carries a glyph and a word, never color alone.

**Guardrails.** Auto-advance moves to the next *unreviewed* claim only — the
deck must not gamify clearing claims; challenge questions stay prominent on the
Stage. The reviewer judges; the surface only reduces navigation cost
([ADR-0012](./0012-reviewer-state-and-verdict-line.md) is untouched: no agent
verdict, dispositions move only by the reviewer's hand).

## Consequences

- The vendored script grows a real presentation layer and earns a minimal
  Node-based DOM test harness — the first JS tests in the repo (the gap the
  analysis flagged as its own omission claim).
- `analysis.json` bumps to `review-analysis/0.3` (optional `hunk` on `{path}`
  evidence refs); collector, validator, analyst definition, and the authoring
  contract move together.
- The bake is untouched in form: it still folds the outcome and Q&A into the
  *document*, stamps dispositions, and swaps to the strict CSP. A baked or
  portable copy renders the document only — Deck Mode requires a live session,
  exactly like disposition controls.
- The document-flow chrome explored in the losing prototype variants (sticky
  claim-tape rail, always-on two-pane navigator) is not pursued; the Map
  subsumes their value. Side-by-side diff (#22) stays deprioritized by
  construction.
- If a future host can't serve the cockpit, Deck Mode degrades to the document
  — consistent with the Host Seam posture: capabilities degrade, the artifact
  does not fork.
