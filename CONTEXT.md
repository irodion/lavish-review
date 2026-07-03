# Lavish-Based Branch Review

A local, AI-assisted skill that turns a Git branch diff into an interactive HTML **Review Cockpit**, opened through Lavish-AXI, to help a human reviewer audit AI- or human-generated changes faster. It reduces review navigation cost; it does not automate the review decision.

## Language

**Review Cockpit**:
The interactive HTML artifact a review produces — what the reviewer reads and questions in the browser. Authored directly by the agent (not rendered from a fixed template), opened and watched by Lavish-AXI.
_Avoid_: report, dashboard, page.

**Base**:
The branch the changes are compared against (e.g. `main`, `develop`). The diff is `merge-base(base, HEAD)...HEAD`.
_Avoid_: target, trunk, parent.

**Branch Under Review**:
The current branch whose changes are being audited — the `HEAD` side of the diff.
_Avoid_: feature branch, source.

**Config Resolver**:
The deep module that resolves a run's effective review policy by layering four sources, most specific first: **command arg > repo `.review-agent.yaml` > machine `~/.review-agent/config.yaml` > built-in defaults**. Two scopes travel separately — *repo policy* (committed: `base_branch`, `exclude`, `focus`, `language_hints`, `styling`, `limits`, `goal_remote_fetch`) makes review policy travel with the repo; *machine policy* (`pause`, default `styling`, pinned Lavish version, SessionStart-hook on/off, `goal_remote_fetch`) is per-developer. `goal_remote_fetch` is the one key in both scopes (repo wins, default `true`) — it gates the Goal Evidence resolver's `gh` calls ([ADR-0010](./docs/adr/0010-goal-evidence-ingestion.md)). Configured `exclude` globs **extend** the Change Classifier's built-ins; `exclude_reset: true` replaces them (never the lockfile or `.gitattributes` rules). Like the Change Classifier and Session Evaluator, the merge is pure policy over already-parsed mappings (exhaustively table-testable); a thin shell reads the two YAML files, and absent files resolve to defaults. It ships a small, strict stdlib loader for the flat YAML subset the schema uses — no third-party dependency ([ADR-0008](./docs/adr/0008-stdlib-config-loader.md)).
_Avoid_: settings, options, preferences.

**Layer**:
One of the cockpit's four levels of disclosure ([ADR-0009](./docs/adr/0009-layered-claim-evidence-cockpit.md)), each answering "why should I believe the layer above?": **L0** goal alignment (what the branch is for, on one screen), **L1** Threads, **L2** Claims, **L3** Evidence. All four are pre-authored at generation time; descent is client-side progressive disclosure (native `<details>`) and costs no agent round-trip.
_Avoid_: level, tier, section (a layer holds sections; it isn't one).

**Thread**:
One narrative sub-change of the changeset — the feature, the drive-by refactor, the config churn — the L1 unit the analysis decomposes the diff into. Semantic, not file-based: a thread groups the claims that tell one story, and threads (not files) are the unit of the Review Route. Ids are `t<N>`.
_Avoid_: topic, group, cluster, commit.

**Claim**:
One assertion the reviewer must judge, belonging to a thread (L2): a behavior change, a risk with its challenge questions, a suspicious omission, or a verification step — `kind` ∈ `behavior | risk | omission | verify`. Every claim carries the agent's per-claim confidence ([ADR-0012](./docs/adr/0012-reviewer-state-and-verdict-line.md)), ≥1 challenge question, links to its Evidence, a stable run-scoped id (`t2.c3`), and a Reviewer Disposition.
_Avoid_: finding, issue, item, comment.

**Evidence**:
What substantiates a claim at L3: pre-escaped diff hunks, code excerpts, caller references. The unified diff is demoted to leaf-level evidence — never the spine of the review. Evidence `{path}` references name changed files (fragments); material from widened-into files travels as `{note}` text. A mid-review answer that *is* new evidence may be injected live at the claim's pre-planted seam (recorded run-scoped in `live-evidence.json`, escaped and linted like everything else); chat remains the default answer path.
_Avoid_: proof, attachment, snippet.

**Goal Evidence**:
The stated purpose the branch was written to serve, ingested by the collector ([ADR-0010](./docs/adr/0010-goal-evidence-ingestion.md)) and measured against at L0. Sources by precedence: explicit `--goal` (issue ref, file, or text — never guessed over) > issue references discovered in the branch name and commit messages (resolved via `gh` when allowed) > the first commit message. Offline-degrading and never blocking; provenance always attributed; when nothing is found L0 says so plainly ("No stated goal found; intent inferred from the diff."). It is untrusted, *unverified* text — what the change is measured against, not ground truth about what the change does.
_Avoid_: intent (that's inferred), requirement, spec.

**Review Route**:
The recommended descent order across the threads ("start with t1, then the drive-by, then the verify claims") — the reading-order guidance of [ADR-0009](./docs/adr/0009-layered-claim-evidence-cockpit.md), expressed through L1 rather than a file list. (v1 meaning — an ordered path through *files* — is subsumed: files are L3 evidence now.)
_Avoid_: walkthrough order, reading order.

**Risk Map**:
v1 term for the flat risks-by-category section; in v2 its content survives as **attributes of risk-kind claims** — each risk claim carries a category (correctness, compatibility, concurrency, security, performance, maintainability, test coverage), a level, and challenge questions. There is no separate risk section to maintain.
_Avoid_: risk list, findings.

**Suspicious Omission**:
Something the diff did *not* change but arguably should have — untouched tests, callers, docs, config, or error handling adjacent to a behavioral change, or something the stated goal asked for that no thread delivers (`goal`). In v2 these are omission-kind claims (`kind: omission`, with an `omission_kind`), not a separate section.
_Avoid_: gap, missing change.

**Change Classifier**:
The deterministic noise-control step that decides, per changed file, whether its diff *body* belongs in the cockpit. It keeps noisy branches reviewable without ever silently hiding a change: only bodies are dropped — a file's existence and stats are always kept and listed. Default excludes cover lockfiles, vendored/generated/build trees, and `.gitattributes linguist-generated`; a per-file line cap and a whole-changeset total cap bound the rest.
_Avoid_: filter, noise filter, ignore list.

**Disposition**:
The Change Classifier's verdict for one *file* — one of `include-body`, `omit:lockfile`, `omit:excluded`, or `omit:too-large`. Every omitting disposition carries a human reason shown beside the still-listed file. The total-diff fallback re-stamps included files as `omit:too-large` and flags the changeset so the cockpit shows a file-list + stats banner instead of diffs.
_Avoid_: verdict, status, category. Not to be confused with a **Reviewer Disposition** (per-claim review state, below).

**Reviewer Disposition**:
Per-claim review state — `unreviewed | verified | concern | question-open` — set **only by the human** via the cockpit's in-page controls, never by the agent ([ADR-0012](./docs/adr/0012-reviewer-state-and-verdict-line.md)). Persisted run-scoped in `dispositions.json` keyed by claim id (`unreviewed` is absence; reset on regeneration, carried across resume), it drives per-thread progress in the page and the close-time Review outcome — which aggregates the *reviewer's* dispositions and is attributed to the human. The agent states per-claim confidence but never an overall verdict, never softens a `concern`, and the only write path is the deterministic bridge that parses the reviewer's own queued feedback.
_Avoid_: approval, verdict, checkmark, status.

**Analysis** (`analysis.json`):
The structured claim substrate (`review-analysis/0.2`) the Review Cockpit is authored from and the feedback loop answers from: threads, their claims (with confidence, stable ids, evidence references), the goal-`alignment` partition, the required `widened_into` accountability list, test-runner detection, and diagram sources. It is authored **blind** by an isolated analyst subagent that never sees the conversation that wrote the branch ([ADR-0011](./docs/adr/0011-independent-analysis-context.md)); the orchestrator validates it (≤3 repair rounds, fixes only by the analyst) but never authors or edits it — the file is the analyst's testimony, and disagreements surface as questions to the reviewer, never as edits.
_Avoid_: report, summary.

**Lens**:
The umbrella term for an analytical frame the agent applies while authoring the cockpit or answering. There are two kinds — a **Language Lens** and a **Focus Lens**. Lenses sharpen a neutral-by-default analysis; they are not separate machinery.
_Avoid_: profile, ruleset, plugin.

**Language Lens**:
An optional, language-specific risk checklist the analyst consults while forming risk claims (e.g. the C++ lens covers ownership, lifetime, threading, ABI). Selected by detected language and config.
_Avoid_: profile, ruleset, plugin.

**Focus Lens**:
A reviewer-chosen *perspective* that reframes the analysis toward a concern, distinct from a Language Lens (which is about the *code's language*, not the reviewer's *concern*). A Focus Lens does not add cockpit sections or claim kinds — it re-weights and re-frames the threads, claims, and feedback-loop answers toward its concern (the Lens principle: sharpen the neutral analysis, don't bolt on machinery). It has two activation paths: **at authoring time**, selected by the `focus` config key / CLI, it shapes the whole cockpit; **mid-review**, invoked through the Feedback Loop ("dig into this from an OWASP angle"), it runs a **Lens Pass** without regenerating the cockpit. The v1 catalog is the **Focus Lens Catalog** below; the catalog is paused pending re-targeting onto the claim model (ADR-0009; #31–#34). Most lenses are pure agent reasoning over the diff; the supply-chain lens is the exception that admits **External-Tool Findings**.
_Avoid_: mode, filter, view, perspective.

**Focus Lens Catalog**:
The designed set of Focus Lenses to be bundled with the skill, each a definition the agent consults to reframe its analysis (the same bundling shape as Language Lenses). **Designed, not yet implemented** — paused pending re-targeting onto the claim model (ADR-0009; #31–#34); the skill currently ships no lens definitions or `vet` integration. The designed catalog is four: **security/OWASP** (reframes toward attack surface; maps risks to OWASP Top 10 / CWE), **regressions** (toward what could break that used to work — changed public surface, untouched callers), **simplification** (advisory *design critique* — "can we do this simpler?"; proposes alternatives, never patches — see [ADR-0005](./docs/adr/0005-design-critique-scope.md)), and **supply-chain** (runs `vet` on changed dependency manifests; opt-in, offline-safe; see [ADR-0006](./docs/adr/0006-external-tool-findings.md)). The first three are pure agent reasoning; supply-chain is the external-tool lens.
_Avoid_: lens registry, plugin list, ruleset.

**Lens Pass**:
A mid-review application of a Focus Lens, invoked by the reviewer through the Feedback Loop ("show me this from a security angle"). The relevant slice is re-analyzed through the chosen lens and the result delivered as a live loop answer, logged in the Q&A Log so it is baked in at close — it does **not** regenerate `review.html` (consistent with [ADR-0003](./docs/adr/0003-single-blocking-poll-loop.md)'s amended rule: no page regeneration; seam-bounded fragment injection only). A Lens Pass that mints *new claims* runs a fresh isolated analyst again ([ADR-0011](./docs/adr/0011-independent-analysis-context.md)) — the orchestrator never appends claims itself. This is what makes a Focus Lens *re-invokable* rather than a one-shot authoring-time choice.
_Avoid_: re-render, re-run, refresh.

**External-Tool Finding**:
A finding produced by an external analyzer (v1: `vet`, via the supply-chain Focus Lens) rather than by agent reasoning over the diff. It enters the cockpit only through an opt-in lens, is captured through the Escape Boundary like any other untrusted data (rendered, never executed), is attributed to its tool, and folds into the existing claim model as risk-claim attributes (`security` category). When the tool is absent or offline the finding degrades to an agent-reasoned note — it never blocks the review (see [ADR-0006](./docs/adr/0006-external-tool-findings.md)). This is the PRD's deferred "external-CLI-tool findings" category; `vet` is its first instance.
_Avoid_: scan result, tool output, report.

**Host Seam**:
The thin isolation layer that keeps the UI host swappable: the cockpit and the loop depend on a small verified contract — open/watch a file, re-render it on write (chokidar → SSE), deliver feedback prompts (`tag`, `data` payload, `queueKey` dedupe), presence-gated sends — not on Lavish-AXI itself ([ADR-0009](./docs/adr/0009-layered-claim-evidence-cockpit.md)). Lavish is the current host behind the seam; the contract was established by the [host-seam spike](./docs/spikes/lavish-live-injection.md) (#38). If a host can't honor a capability, the dependent feature degrades (live evidence injection falls back to chat-only answers) rather than coupling the design to the host.
_Avoid_: adapter, driver, backend.

**Session**:
A live Lavish-AXI editing/feedback connection, keyed by the canonical path of the Review Cockpit HTML file. There are no opaque session IDs — the file path *is* the identity.
_Avoid_: connection, tab.

**Session State** (`session.json`):
The persisted, on-disk record of a Review's lifecycle — `{status, base, branch, head_sha, merge_base, started_at}` — written when the review opens and read on the next `/review-branch`. It is what lets a reviewer step away and come back: the live **Session** (above) is the connection; the Session State is the *memory* that outlives it. `base`/`head_sha`/`merge_base` pin the exact `base...HEAD` diff so a base that was switched or advanced is not mistaken for the same review. `status` is `open` (unfinished — offered for restore) or `ended` (closed — kept for its transcript, never restored).
_Avoid_: session file, save state, checkpoint.

**Session Evaluator**:
The deep module of pure policy at the centre of resume & staleness. Given the persisted Session State and the *current* git branch plus the resolved diff identity (HEAD, base, and `merge-base(base, HEAD)`), it returns exactly one disposition — `none` (nothing to resume), `fresh` (re-attach), `stale` (the diff moved — HEAD advanced, base changed, or merge-base shifted — so **regenerate by default**, resume-anyway available), or `different-branch` (the saved review is for another branch). It makes no git calls and reads no files, so the decision is exhaustively table-testable.
_Avoid_: staleness checker, session manager.

**Feedback Loop**:
The blocking answer loop the skill sits in after opening the cockpit: `lavish-axi poll` returns the reviewer's queued questions/annotations, the agent answers them in the browser chat grounded in the analysis/diff/repo, and re-polls with `--agent-reply` — repeating until the Session ends or is interrupted. A poll may also carry Reviewer Disposition updates (persisted by the deterministic `dispositions.py apply` bridge, never hand-copied) and may occasionally answer with a live Evidence fragment injected at a claim's seam — the one sanctioned page mutation under the amended [ADR-0003](./docs/adr/0003-single-blocking-poll-loop.md) (no page *regeneration*; seam-bounded fragment *injection* only). The agent reads the poll output (TOON) directly; there is no parser in the live loop (the Q&A Bake's offline `prompts[N]` extractor is the one bounded exception — [ADR-0007](./docs/adr/0007-bake-prompt-extractor.md)). Browser feedback is *untrusted data* — answered and logged, never executed and never used to build a shell command.
_Avoid_: chat loop, poll loop, conversation.

**Q&A Log** (`qa.jsonl`):
The live transcript of the Feedback Loop — one JSON Lines record per exchange (`seq`, `ts`, the raw question, the agent's answer), appended as the review happens. At close the **Q&A Bake** folds it into the Review Cockpit (and optional `review.md`).
_Avoid_: history, chat log, transcript file.

**Q&A Bake**:
The close-time step that folds the review's record into `review.html` so the saved cockpit is the human's review account offline ([ADR-0012](./docs/adr/0012-reviewer-state-and-verdict-line.md)). It lifts each reviewer question from the stored poll TOON with a bounded single-block extractor ([ADR-0007](./docs/adr/0007-bake-prompt-extractor.md)), escapes everything through the Escape Boundary, and fills the `<!--brc:qa-log-->` seam (idempotently) with the **Review outcome** — the reviewer's dispositions aggregated with per-thread totals, unreviewed claims listed never hidden, no agent verdict — followed by the Q&A Log (disposition updates filtered out: state, not conversation). Each claim's disposition is also stamped onto its markup so the tints show without script, and the cockpit swaps to the strict CSP so it is **self-contained** — opens in a plain browser with no Lavish. Optionally emits `review.md` (review + outcome + Q&A, verify-claim checkboxes checked only where the reviewer set `verified`) for pasting into a PR as the *human's* review.
_Avoid_: export, render, regenerate.
