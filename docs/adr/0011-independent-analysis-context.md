# Analysis is authored in an isolated context that never sees the authoring conversation

The skill runs inline in whatever Claude Code session invokes it. When that session (or its ancestors) *wrote* the branch — the normal case for AI-generated code — the "independent reviewer" shares working memory, assumptions, and self-consistency pressure with the author. It knows what the code is *supposed* to do, so it reads what it expects rather than what is there. Nothing in the current pipeline enforces the independence the tool's premise claims.

**Decision.** The analysis — the step that forms threads, claims, and confidences ([ADR-0009](./0009-layered-claim-evidence-cockpit.md)) — is authored in a **fresh, isolated subagent context**. Its inputs are exactly the collected artifacts (`context.json`, the goal block, escaped fragments, `changed-files.json`, commits) plus read access to the repo working tree; it receives nothing of the invoking conversation. Independence holds **by construction**, not by user discipline.

The main session remains the **orchestrator**: it runs the collector, spawns the analysis context, validates the result, authors nothing analytical itself, and then drives the cockpit and the feedback loop. Loop answers and Lens Passes are grounded in the *artifacts* — `analysis.json`, the fragments, the repo — which keeps answering fast without re-contaminating claim formation: the claims were formed blind; answering questions about them afterward is presentation, not analysis. A mid-review re-analysis of a slice (a Lens Pass that mints *new* claims) uses a fresh isolated context again.

**Why a single isolated pass and not an adversarial panel.** Multiple independent analyses with agreement thresholds were considered and deferred: they multiply cost per review for integrity the isolation boundary already buys at the claim-formation stage. The claim-centric schema leaves the door open — a claim already carries confidence and evidence, so a future verification pass can annotate or challenge claims without reshaping the model.

## Consequences

- `SKILL.md`'s pipeline splits into orchestrator steps and an isolated analysis step with an explicit input manifest; the analysis prompt travels with the skill so the isolation boundary is inspectable.
- The analysis context must state what it *widened into* (files read beyond the diff) in `analysis.json` — as a **required, possibly-empty `widened_into` list in `review-analysis/0.2`** ([ADR-0009](./0009-layered-claim-evidence-cockpit.md)), so writer and validator cannot diverge; the current `0.1` validator predates the field and this lands with the schema bump (#39). The reviewer can then see the evidence basis — blind does not mean unaccountable.
- Cost: one additional context's tokens per review. Accepted; it is the price of the premise.
- The orchestrator never edits claims it disagrees with; discrepancies it notices are surfaced to the reviewer as questions, preserving the isolated pass's integrity.
