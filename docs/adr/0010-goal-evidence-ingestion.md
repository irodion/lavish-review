# The review ingests Goal Evidence — multi-source, best-effort, offline-degrading

Every cockpit section to date reasons from the diff alone; `intent_summary` is *reconstructed* from the same bytes the reviewer distrusts. For AI-generated branches that inverts the reviewer's actual first question — "does this code serve the goal it was written for?" — into a guess. The change's rationale is the single thing reviewers say they need most (Bacchelli & Bird, ICSE 2013), and it usually exists somewhere: an issue, a PR body, a PRD, the prompt that launched the agent.

**Decision.** The collector gathers **Goal Evidence** — the stated purpose the branch was written to serve — and the cockpit's L0 layer ([ADR-0009](./0009-layered-claim-evidence-cockpit.md)) becomes a goal↔implementation alignment check. Sources by precedence:

1. **Explicit argument** — `/review-branch --goal <issue-ref | file | text>`. Always wins; never guessed over.
2. **Local repo evidence** — commit messages of the branch, issue references in them and in the branch name. No network; always attempted.
3. **Remote tracker evidence** — the linked PR body / referenced GitHub issue via `gh`, attempted only when a reference was found (or configured) and degrading silently to (2) when offline, unauthenticated, or absent — the [ADR-0006](./0006-external-tool-findings.md) posture applied to goal fetching: opt-out-able, never blocking, never a failed review.

When no goal is found, L0 says so plainly — "no stated goal found; intent inferred from the diff" — and the cockpit degrades to today's behavior. An inferred intent is never presented as a stated goal; provenance is always attributed (issue #N, commit trailer, user-provided).

**Goal Evidence is untrusted data.** Issue bodies and commit messages are attacker-writable text; they cross the Escape Boundary ([ADR-0002](./0002-deterministic-escape-boundary.md)) like diff bodies, are rendered and never executed, and are never used to build a shell command. They are also *unverified claims about intent*: the analysis treats the goal as what the change is measured against, not as ground truth about what the change does.

## Consequences

- `context.json` gains a `goal` block (`{text, source, provenance}` or `null`); `analysis.json` (`review-analysis/0.2`) gains an alignment section: which threads serve the goal, which are unrelated to it (drive-bys — themselves worth a claim), and what the goal asked for that no thread delivers (a first-class Suspicious Omission).
- The default `/review-branch` remains network-free unless local evidence names a remote issue and `gh` is available; a config key (repo or machine scope) can disable remote fetching wholesale.
- The Session Evaluator's identity is unchanged — a review is still keyed by the diff; a goal edited upstream does not make a review stale in v1.
- Agent-session transcripts (the prompt that produced the branch) are a recognized future source, deliberately out of scope until a portable way to reference them exists.
