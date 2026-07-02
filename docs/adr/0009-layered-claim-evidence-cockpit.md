# The cockpit becomes a layered claim→evidence surface with hybrid dynamics

The Review Cockpit to date is a fixed sequence of fully-expanded sections — Executive Summary through Diff — rendered once and never changed mid-review ([ADR-0001](./0001-agent-authored-cockpit.md), [ADR-0003](./0003-single-blocking-poll-loop.md)). That shape fails the problem the skill exists for: an AI-generated branch rolls thousands of lines before a reviewer at once, and a static report with a chat overlay reproduces that failure — it just adds prose on top. Code-review research is unambiguous that the reviewer's scarcest resource is *understanding*, not diff access (Bacchelli & Bird, ICSE 2013), that decomposing a tangled changeset into semantic partitions measurably helps (Barnett et al., ICSE 2015), and that reading *order* affects review effectiveness (Baum et al., 2017–2019).

**Decision.** The cockpit's surface is restructured into four layers, each answering "why should I believe the layer above?", descended at the reviewer's pace:

- **L0 — Goal alignment.** What the branch is *for* (the ingested Goal Evidence, [ADR-0010](./0010-goal-evidence-ingestion.md)) and how the change relates to it, on one screen.
- **L1 — Threads.** The changeset decomposed into a small number of *narrative threads* (semantic sub-changes: the feature, the drive-by refactor, the config churn) — not files. Threads are the unit of the Review Route.
- **L2 — Claims.** Per thread, the assertions the reviewer must judge — behavior changes, risks with their challenge questions, suspicious omissions — each carrying the agent's confidence ([ADR-0012](./0012-reviewer-state-and-verdict-line.md)) and links to its evidence.
- **L3 — Evidence.** The pre-escaped hunks, code excerpts, and caller references that substantiate each claim. The unified diff demotes to leaf-level evidence; it is never the spine of the review.

**Dynamics are hybrid.** All four layers are pre-authored at generation time and revealed by client-side progressive disclosure (a stateful, still-vendored `app.js` — descent costs no agent round-trip). In addition, one bounded live path exists: a mid-review request through the feedback loop ("what about the callers of this?") may author a **new evidence fragment**, passed through the Escape Boundary and the Cockpit Linter, and injected at a pre-planted seam (the same mechanism as the `<!--brc:qa-log-->` seam). Chat answers remain the default; fragment injection is for when the answer *is* new evidence the page should keep.

**Amendments to prior ADRs.**

- [ADR-0001](./0001-agent-authored-cockpit.md) stands: the agent still authors the cockpit directly, now as a layered document plus, occasionally, later fragments.
- [ADR-0003](./0003-single-blocking-poll-loop.md) is amended, not repealed: the single blocking poll loop remains the only driver, but it gains one page-mutating operation — seam-bounded, lint-verified fragment injection. "No per-answer HTML regeneration" becomes "no page *regeneration*; bounded fragment *injection* only."
- [ADR-0002](./0002-deterministic-escape-boundary.md) is unchanged and becomes more load-bearing: every injected fragment crosses the same boundary and lint as generation-time content.

**Section mapping.** Executive Summary → L0/L1. Review Route → the descent order across threads. Behavior Changes, Risk Map, Suspicious Omissions → L2 claims (the risk categories and challenge-question requirement survive as claim attributes). File Walkthrough and Diff → L3 evidence. Test Checklist → L2 claims of a "verify" kind.

## Consequences

- `analysis.json` moves to a claim-centric schema (`review-analysis/0.2`): threads, claims with confidence and stable IDs, evidence references. The validator's closed vocabularies and the ≥1-challenge-question rule carry over per claim.
- The Change Classifier's nothing-hidden invariant is untouched: every changed file remains reachable at L3, omitted bodies stay listed with reasons. Layering defers detail; it never hides it.
- `app.js` grows real state (reveal/collapse, claim navigation) but stays vendored and CSP-compatible — no inline JS, no framework, no build step.
- The UI host stays Lavish-AXI for now, isolated behind a thin seam; a verification spike must establish whether Lavish live-reloads an edited page and whether injected content participates in annotation. If it cannot, live injection degrades to chat-only answers until the host seam is swapped — the layer model and schema do not depend on the answer.
- Issue #22 (side-by-side diff) is deprioritized by construction: the diff is leaf evidence, not the primary reading surface.
- Focus Lenses (#31–34) re-target: a lens re-weights threads and claims rather than a flat Risk Map. They are paused until the new schema lands.
