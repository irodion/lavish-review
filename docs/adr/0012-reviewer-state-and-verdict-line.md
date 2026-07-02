# Reviewer dispositions are first-class, persisted state; the agent states confidence, never a verdict

A layered, guided review ([ADR-0009](./0009-layered-claim-evidence-cockpit.md)) is a *process*, but the cockpit's only memory of the human today is `session.json`'s `open|ended`. Nothing records which claims the reviewer has examined, accepted, or flagged — so resuming a half-done review restarts it visually, and closing one produces a transcript but no account of what was actually reviewed.

**Decision (state).** Each L2 claim carries a **Reviewer Disposition** — `unreviewed | verified | concern | question-open` — set by the human in the cockpit, never by the agent. Dispositions are persisted on disk beside the session (surviving `Esc`/resume like queued feedback does), drive per-thread progress indicators in the page, and are folded in at close by the bake: the exported `review.html`/`review.md` state what was verified, what raised concerns, and what was never examined. Dispositions are reviewer-originated **untrusted data** and cross the Escape Boundary ([ADR-0002](./0002-deterministic-escape-boundary.md)) like all feedback. The capture channel (in-page controls through Lavish's feedback protocol vs. a state file) is settled by the host-seam spike, not by this ADR.

**Decision (verdict line).** Claim-level review sharpens the temptation [ADR-0005](./0005-design-critique-scope.md) guards against, so the line is drawn explicitly:

- The **agent** may state *per-claim confidence* ("high confidence, evidence attached"; "unverified — challenge this") — that is honest signal the analysis genuinely has, and hiding it wastes it.
- The **agent** never issues an overall recommendation — no "safe to merge", no "approve", no aggregate score. The close-time summary aggregates the **reviewer's** dispositions and is attributed to the human.
- A claim with `concern` is never softened or auto-resolved by the agent; only the reviewer moves a disposition.

**Why.** The tool's founding rule — it reduces navigation cost, it does not make the review decision — survives only if the boundary is mechanical: confidence is *about a claim*, a verdict is *about the change*. Reviewers anchor on bottom lines; the cockpit therefore never prints one it authored.

## Consequences

- New persisted artifact (e.g. `dispositions.json`, keyed by the stable claim IDs of `review-analysis/0.2`), run-scoped like `qa.jsonl` — reset on regeneration, carried across resume. The Session Evaluator is unchanged; staleness still keys on the diff.
- The bake grows a dispositions section; `review.md` becomes a reviewable account ("verified 6, concerns 2, unreviewed 1 — listed") suitable for pasting into a PR as the *human's* review.
- The Test Checklist's checkboxes become real: verify-claims take dispositions like any other claim.
- Unreviewed claims are listed at close, never hidden — the same nothing-hidden invariant the Change Classifier enforces for files, applied to attention.
