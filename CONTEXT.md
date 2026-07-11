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
One of the cockpit's four levels of disclosure ([ADR-0009](./docs/adr/0009-layered-claim-evidence-cockpit.md)), each answering "why should I believe the layer above?": **L0** goal alignment (what the branch is for, on one screen), **L1** Threads, **L2** Review Steps, **L3** Evidence. All four are pre-authored at generation time; descent is client-side progressive disclosure (native `<details>`) and costs no agent round-trip. A served review may traverse the same layers step-by-step instead — **Deck Mode** ([ADR-0014](./docs/adr/0014-deck-presentation-mode.md)). (The L2 unit was the **Claim** through schema 0.3; [ADR-0016](./docs/adr/0016-guided-change-narration-surface.md) replaces it with the **Review Step**.)
_Avoid_: level, tier, section (a layer holds sections; it isn't one).

**Deck Mode**:
The served-only presentation of the Review Cockpit ([ADR-0014](./docs/adr/0014-deck-presentation-mode.md)): a persistent **Map** beside a **Stage** that shows one Review Step at a time. Built client-side by the vendored script **from the document's own DOM** (relocating already-escaped nodes — never string-built markup); the L0–L3 document stays the single artifact, the linted form, and the baked `file://` record. Document mode is the only mode on `file://`. **Status:** the Map/Stage skeleton (#67), click-to-stage, the staged unit's evidence hunk inline, a lossless mode toggle, and keyboard-driven dispositions that auto-advance to the next *unreviewed* unit (#68) have shipped over the Claim substrate. Under the narration reframe ([ADR-0016](./docs/adr/0016-guided-change-narration-surface.md), roadmap #86/#88) the staged unit becomes the **Review Step**, the route's first stop is L0 staged as an orientation card, **relates_to** jumps stage a related step, and **Deck Mode becomes the default on a served review** (document mode one toggle away) — so the guided walk is the landing experience, not a toggle to discover.
_Avoid_: app, SPA, queue view, triage mode.

**Map**:
Deck Mode's persistent navigator — the *whole* while the Stage shows the *piece*: threads in Review Route order with one disposition-tinted dot per step, each thread's **derived impact character** (counts computed from its steps — "3 behavior-change · 1 test · 5 preserving" — never an authored thread badge), the changed files with their add/delete stats, and overall progress (step coverage; Attention Notes are never dotted or counted). Clicking any dot (or a thread) stages that step. *(Roadmap: a **branch-scoped ask affordance** in the Map — the map-side half of the two-channel chat, [ADR-0015](./docs/adr/0015-claim-scoped-questions.md) — is not yet built; branch questions go through the ordinary loop chat for now.)*
_Avoid_: sidebar, tree, index.

**Stage**:
Deck Mode's focus surface: one Review Step rendered whole — impact and judgment chips, summary and detail narration, **why_now**, its **review_prompts**, its Evidence with the anchored hunk inline, any Attention Notes as muted asides, **relates_to** jumps to related steps, the disposition control, and the step-scoped ask affordance. (Stop zero stages L0 as an orientation card rather than a step.)
_Avoid_: card view, detail pane, focus mode.

**Step-scoped Question**:
A reviewer question submitted from a specific Review Step and queued with that step's id as structured data (`kind: step-question`) through the same presence-gated channel as Reviewer Dispositions ([ADR-0015](./docs/adr/0015-claim-scoped-questions.md)). The loop grounds the answer in that step's analysis entry and evidence — no DOM selector to resolve. Conversation, not state: it flows into the Q&A Log like any chat question. Branch-scoped chat remains for questions about the change as a whole. *(This is the **Claim-scoped Question** renamed for the step spine ([ADR-0016](./docs/adr/0016-guided-change-narration-surface.md)); the mechanism is unchanged but for `claim`→`step`. Roadmap: the rename lands with schema 0.4 in #88 — the **current runtime** still queues `kind: claim-question` keyed by claim id.)*
_Avoid_: annotation (that's the element-anchored path), comment, thread (taken).

**Thread**:
One narrative sub-change of the changeset — the feature, the drive-by refactor, the config churn — the L1 unit the analysis decomposes the diff into. Semantic, not file-based: a thread groups the Review Steps that tell one story, and threads (not files) are the unit of the Review Route. A thread carries no authored impact; its character is *derived* from its steps' Behavior Impact (see Map). Ids are `t<N>`.
_Avoid_: topic, group, cluster, commit.

**Review Step**:
One guided stop on the walkthrough, belonging to a thread (L2) — the spine of the narration cockpit ([ADR-0016](./docs/adr/0016-guided-change-narration-surface.md)). Not a finding: it says what changed here, its **Behavior Impact**, **why_now** (why it sits at this point on the route), what the human should compare (its **review_prompts**), and the exact evidence it lands on. It keeps the Claim's structural skeleton — a stable run-scoped id (`t2.s3`), `summary`/`detail`, the agent's per-step confidence ([ADR-0012](./docs/adr/0012-reviewer-state-and-verdict-line.md)), ≥1 Evidence ref, a live-evidence seam, the step-scoped ask affordance, and a Reviewer Disposition — so Deck Mode, the linter, and the disposition bridge port with a rename. Optional **relates_to** links (step ids) connect steps that belong together, e.g. a test step to the behavior it documents. `review_prompts` are required (≥1) on `behavior-change`, `behavior-preserving`, and `unknown-impact` steps, optional on `test-change` and `mechanical-change`. *(Roadmap: schema `review-analysis/0.4` and the code that emits/renders steps land across #84–#89; the current pipeline still speaks Claims.)*
_Avoid_: finding, issue, item, comment, claim.

**Behavior Impact**:
The closed classification every Review Step carries: `behavior-change` (user-visible, API-visible, runtime, config, persistence, error-handling, security, or performance behavior changed) | `behavior-preserving` (refactor, relocation, extraction, naming, or internal simplification that *appears intended* to preserve behavior) | `test-change` (tests added, removed, or re-aimed, with the behavior they document) | `mechanical-change` (generated files, lockfiles, vendored code, formatting, build metadata) | `unknown-impact` (the narrator can't honestly tell without more context, and says what's missing). Impact lives on **steps only** — a thread's character is derived from its steps, never authored. `behavior-preserving` is the *expensive* label (a wrong one invites skimming past a real change), so it must be earned: state what is preserved and what to compare, or fall back to `unknown-impact`. **Intent may narrate, but only evidence may classify** ([ADR-0016](./docs/adr/0016-guided-change-narration-surface.md)): a step's impact derives from the diff and bounded widening alone; a commit that claims "pure refactor" is testimony to check, never a classification input.
_Avoid_: kind, type, category, tag.

**Attention Note**:
A muted, secondary aside on a Review Step — plain `{text, evidence?}`, no severity, no category, no prompt, no disposition of its own; never shown in the Map and never counted in progress. In the default (narrating) analysis there are exactly two kinds: an *untested behavior change* (the test-linkage story told from the negative side) and *goal-unserved work* (surfaced at L0 alignment). All other hunting — risk categories and levels, security/performance checklists, verify steps — returns only through an opt-in Focus Lens; it is never the default spine ([ADR-0016](./docs/adr/0016-guided-change-narration-surface.md)).
_Avoid_: finding, risk, warning, flag.

**Claim** *(historical — schema ≤0.3)*:
Through `review-analysis/0.3` the L2 unit was the Claim: one assertion the reviewer must judge — `kind` ∈ `behavior | risk | omission | verify` — with challenge questions, confidence, and a disposition. [ADR-0016](./docs/adr/0016-guided-change-narration-surface.md) retired it as the spine because that vocabulary pulled the cockpit toward issue-finding; the **Review Step** replaces it, reusing its structural skeleton. Kept here only to read older ADRs and baked reviews.
_Avoid_: (retired term — use Review Step).

**Evidence**:
What substantiates a Review Step at L3: pre-escaped diff hunks, code excerpts, caller references. The unified diff is demoted to leaf-level evidence — never the spine of the review. Under the contract (`review-analysis/0.4`, [ADR-0016](./docs/adr/0016-guided-change-narration-surface.md); mechanics unchanged from 0.3), an Evidence ref carries a `path` and/or a `note`, and a `{path}` ref may add an optional `hunk`: `{path}` names a changed file (a fragment); `{note}` text anchors evidence with no L3 fragment — prose and **widened-into** files; `{path, hunk}` narrows to the **exact hunk** that substantiates the step (`hunk` a 1-based index into that file's hunk sequence — the **Hunk Anchorer**, [ADR-0014](./docs/adr/0014-deck-presentation-mode.md)). The collector emits a deterministic per-hunk id inside each file fragment and a hunk index in `fragments.json`; the evidence link reads the anchor from that manifest (never hand-typed). Validation splits along the module boundary, exactly as it does for `path`: the (pure) validator accepts an optional `hunk` on a `{path}` ref and checks only that it is a **positive integer** — rejecting it on a `{note}`, and rejecting a non-integer or a value `< 1`; it never sees `fragments.json`, so it does **not** bound the index against the file's actual hunk count. That upper bound is the **Cockpit Linter's** job — an out-of-range `hunk` yields a `#hunk-…` link that resolves to no element id and fails the linter's anchor rule (just as it resolves a hand-typed dangling anchor). A `{path}`-only ref keeps file-level anchoring. A mid-review answer that *is* new evidence may be injected live at the step's pre-planted seam (recorded run-scoped in `live-evidence.json`, escaped and linted like everything else); chat remains the default answer path.
_Avoid_: proof, attachment, snippet.

**Goal Evidence**:
The stated purpose the branch was written to serve, ingested by the collector ([ADR-0010](./docs/adr/0010-goal-evidence-ingestion.md)) and measured against at L0. Sources by precedence: explicit `--goal` (issue ref, file, or text — never guessed over) > issue references discovered in the branch name and commit messages (resolved via `gh` when allowed) > the first commit message. Offline-degrading and never blocking; provenance always attributed; when nothing is found L0 says so plainly ("No stated goal found; intent inferred from the diff."). It is untrusted, *unverified* text — what the change is measured against, not ground truth about what the change does.
_Avoid_: intent (that's inferred), requirement, spec.

**Review Route**:
The recommended descent order across the threads ("start with the API change, then the drive-by refactor, then the config churn") — the reading-order guidance of [ADR-0009](./docs/adr/0009-layered-claim-evidence-cockpit.md), expressed through L1 rather than a file list. Thread order *is* the route; there is no global interleaved step sequence — cross-thread links are `relates_to` jumps the reviewer chooses, not an imposed order. Ordering policy ([ADR-0016](./docs/adr/0016-guided-change-narration-surface.md)): behavior-changing threads lead, then test-change, then behavior-preserving, mechanical last; `unknown-impact` slots where its subject belongs. Each step's **why_now** states why it sits at its position. (v1 meaning — an ordered path through *files* — is subsumed: files are L3 evidence now.)
_Avoid_: walkthrough order, reading order, narration route.

**Risk Map** *(lens-gated — not a default surface)*:
v1 term for the flat risks-by-category section. It is **not** part of the default narration cockpit ([ADR-0016](./docs/adr/0016-guided-change-narration-surface.md)): risk categories (correctness, compatibility, concurrency, security, performance, maintainability, test coverage), levels, and their prompts return only through an opt-in **Focus Lens**, never as the default spine. The default analysis narrates and emits at most an untested-behavior or goal-gap Attention Note — it hunts for nothing.
_Avoid_: risk list, findings.

**Suspicious Omission** *(lens-gated, except the two default cases)*:
Something the diff did *not* change but arguably should have — untouched tests, callers, docs, config, or error handling adjacent to a behavioral change. Under [ADR-0016](./docs/adr/0016-guided-change-narration-surface.md) omission-hunting is no longer a default surface: only two cases survive by default, and both as Attention Notes, not adjudicated findings — an *untested behavior change* (on its step) and *goal-unserved work* (something the stated goal asked for that no thread delivers, surfaced at L0 alignment). Broader omission hunting returns only through an opt-in Focus Lens.
_Avoid_: gap, missing change.

**Change Classifier**:
The deterministic noise-control step that decides, per changed file, whether its diff *body* belongs in the cockpit. It keeps noisy branches reviewable without ever silently hiding a change: only bodies are dropped — a file's existence and stats are always kept and listed. Default excludes cover lockfiles, vendored/generated/build trees, and `.gitattributes linguist-generated`; a per-file line cap and a whole-changeset total cap bound the rest.
_Avoid_: filter, noise filter, ignore list.

**Disposition**:
The Change Classifier's verdict for one *file* — one of `include-body`, `omit:lockfile`, `omit:excluded`, or `omit:too-large`. Every omitting disposition carries a human reason shown beside the still-listed file. The total-diff fallback re-stamps included files as `omit:too-large` and flags the changeset so the cockpit shows a file-list + stats banner instead of diffs.
_Avoid_: verdict, status, category. Not to be confused with a **Reviewer Disposition** (per-step review state, below).

**Reviewer Disposition**:
Per-step review state — `unreviewed | looks-right | concern | follow-up | skipped` — set **only by the human** via the cockpit's in-page controls, never by the agent ([ADR-0012](./docs/adr/0012-reviewer-state-and-verdict-line.md), vocabulary reframed by [ADR-0016](./docs/adr/0016-guided-change-narration-surface.md)). `looks-right` attests comprehension + no objection (it replaces `verified`, honest now that verify-steps are gone); `follow-up` replaces `question-open`; **`skipped` is new — a deliberate, attributed act distinct from `unreviewed` (absence)**, so the baked account reports honest coverage rather than an unfinished review. One axis, four active states, one dot color each — no separate understood×approve axes. Persisted run-scoped in `dispositions.json` keyed by step id (reset on regeneration, carried across resume), it drives per-thread progress and the close-time Review outcome — which leads with concerns, then follow-ups, then coverage (looks-right / skipped-with-impacts / unreviewed, listed never hidden), aggregates the *reviewer's* dispositions, and is attributed to the human. The agent states per-step confidence but never an overall verdict, never softens a `concern`, and the only write path is the deterministic bridge that parses the reviewer's own queued feedback. *(Status: the deterministic bridge, the close-time bake, and the Session Evaluator now speak this five-state, step-keyed vocabulary (#87). The served cockpit's in-page controls and `app.js` emission — the side that queues an update keyed by a step id — migrate with the step-shaped cockpit authoring in #86, so an end-to-end served run isn't wired until #86 lands.)*
_Avoid_: approval, verdict, checkmark, status.

**Analysis** (`analysis.json`):
The structured step substrate (`review-analysis/0.4`, [ADR-0016](./docs/adr/0016-guided-change-narration-surface.md)) the Review Cockpit is authored from and the feedback loop answers from: threads, their Review Steps (each with a Behavior Impact, `why_now`, `review_prompts`, confidence, a stable id, evidence references, optional `relates_to` and Attention Notes), the goal-`alignment` partition, the required `widened_into` accountability list, test-runner detection, and diagram sources. It is authored **blind** by an isolated change-narrator subagent that never sees the conversation that wrote the branch ([ADR-0011](./docs/adr/0011-independent-analysis-context.md)); the orchestrator validates it (≤3 repair rounds, fixes only by the analyst) but never authors or edits it — the file is the narrator's testimony, and disagreements surface as questions to the reviewer, never as edits. *(Status: the 0.4 validator has landed (#84); the independent change narrator that authors this substrate (#85) and the step-shaped cockpit rendering + Cockpit Linter that consume it (#86) are the remaining producer slices. The judgment-state consumers — dispositions, bake, session — already speak 0.4 (#87).)*
_Avoid_: report, summary.

**Lens**:
The umbrella term for an analytical frame the agent applies while authoring the cockpit or answering. There are two kinds — a **Language Lens** and a **Focus Lens**. Lenses sharpen a neutral-by-default analysis; they are not separate machinery.
_Avoid_: profile, ruleset, plugin.

**Language Lens**:
An optional, language-specific risk checklist the analyst consults while forming risk claims (e.g. the C++ lens covers ownership, lifetime, threading, ABI). Selected by detected language and config.
_Avoid_: profile, ruleset, plugin.

**Focus Lens**:
A reviewer-chosen *perspective* that reframes the analysis toward a concern, distinct from a Language Lens (which is about the *code's language*, not the reviewer's *concern*). A Focus Lens does not add cockpit sections or new Behavior Impact values — it re-weights and re-frames the threads, steps, and feedback-loop answers toward its concern, and is the sanctioned path by which risk/omission hunting re-enters a review at all ([ADR-0016](./docs/adr/0016-guided-change-narration-surface.md); the Lens principle: sharpen the neutral analysis, don't bolt on machinery). It has two activation paths: **at authoring time**, selected by the `focus` config key / CLI, it shapes the whole cockpit; **mid-review**, invoked through the Feedback Loop ("dig into this from an OWASP angle"), it runs a **Lens Pass** without regenerating the cockpit. The v1 catalog is the **Focus Lens Catalog** below; the catalog is paused pending re-targeting onto the claim model (ADR-0009; #31–#34). Most lenses are pure agent reasoning over the diff; the supply-chain lens is the exception that admits **External-Tool Findings**.
_Avoid_: mode, filter, view, perspective.

**Focus Lens Catalog**:
The designed set of Focus Lenses to be bundled with the skill, each a definition the agent consults to reframe its analysis (the same bundling shape as Language Lenses). **Designed, not yet implemented** — paused pending re-targeting onto the claim model (ADR-0009; #31–#34); the skill currently ships no lens definitions or `vet` integration. The designed catalog is four: **security/OWASP** (reframes toward attack surface; maps risks to OWASP Top 10 / CWE), **regressions** (toward what could break that used to work — changed public surface, untouched callers), **simplification** (advisory *design critique* — "can we do this simpler?"; proposes alternatives, never patches — see [ADR-0005](./docs/adr/0005-design-critique-scope.md)), and **supply-chain** (runs `vet` on changed dependency manifests; opt-in, offline-safe; see [ADR-0006](./docs/adr/0006-external-tool-findings.md)). The first three are pure agent reasoning; supply-chain is the external-tool lens.
_Avoid_: lens registry, plugin list, ruleset.

**Lens Pass**:
A mid-review application of a Focus Lens, invoked by the reviewer through the Feedback Loop ("show me this from a security angle"). The relevant slice is re-analyzed through the chosen lens and the result delivered as a live loop answer, logged in the Q&A Log so it is baked in at close — it does **not** regenerate `review.html` (consistent with [ADR-0003](./docs/adr/0003-single-blocking-poll-loop.md)'s amended rule: no page regeneration; seam-bounded fragment injection only). A Lens Pass that mints *new steps* runs a fresh isolated analyst again ([ADR-0011](./docs/adr/0011-independent-analysis-context.md)) — the orchestrator never appends steps itself. This is what makes a Focus Lens *re-invokable* rather than a one-shot authoring-time choice.
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
The deep module of pure policy at the centre of resume & staleness. Given the persisted Session State (which records the `review-analysis` schema the cockpit was authored against), the *current* git branch, the resolved diff identity (HEAD, base, and `merge-base(base, HEAD)`), and the analysis schema this code speaks, it returns exactly one disposition — `none` (nothing to resume), `fresh` (re-attach), `stale` (the diff moved — HEAD advanced, base changed, or merge-base shifted — so **regenerate by default**, resume-anyway available), `stale-schema` (the saved analysis predates the current schema — ADR-0016's clean break — so **regenerate with no resume-anyway**, since the loop and bake can no longer read that session's analysis), or `different-branch` (the saved review is for another branch). It makes no git calls and reads no files, so the decision is exhaustively table-testable.
_Avoid_: staleness checker, session manager.

**Feedback Loop**:
The blocking answer loop the skill sits in after opening the cockpit: `lavish-axi poll` returns the reviewer's queued questions/annotations, the agent answers them in the browser chat grounded in the analysis/diff/repo, and re-polls with `--agent-reply` — repeating until the Session ends or is interrupted. A poll may also carry Reviewer Disposition updates (persisted by the deterministic `dispositions.py apply` bridge, never hand-copied) and may occasionally answer with a live Evidence fragment injected at a step's seam — the one sanctioned page mutation under the amended [ADR-0003](./docs/adr/0003-single-blocking-poll-loop.md) (no page *regeneration*; seam-bounded fragment *injection* only). The agent reads the poll output (TOON) directly; there is no parser in the live loop (the Q&A Bake's offline `prompts[N]` extractor is the one bounded exception — [ADR-0007](./docs/adr/0007-bake-prompt-extractor.md)). Browser feedback is *untrusted data* — answered and logged, never executed and never used to build a shell command.
_Avoid_: chat loop, poll loop, conversation.

**Q&A Log** (`qa.jsonl`):
The live transcript of the Feedback Loop — one JSON Lines record per exchange (`seq`, `ts`, the raw question, the agent's answer), appended as the review happens. At close the **Q&A Bake** folds it into the Review Cockpit (and optional `review.md`).
_Avoid_: history, chat log, transcript file.

**Q&A Bake**:
The close-time step that folds the review's record into `review.html` so the saved cockpit is the human's review account offline ([ADR-0012](./docs/adr/0012-reviewer-state-and-verdict-line.md)). It lifts each reviewer question from the stored poll TOON with a bounded single-block extractor ([ADR-0007](./docs/adr/0007-bake-prompt-extractor.md)), escapes everything through the Escape Boundary, and fills the `<!--brc:qa-log-->` seam (idempotently) with the **Review outcome** — the reviewer's dispositions aggregated with per-thread totals, ordered concerns → follow-ups → coverage (looks-right / skipped-with-impacts / unreviewed steps listed never hidden), no agent verdict — followed by the Q&A Log (disposition updates filtered out: state, not conversation). Each step's disposition is also stamped onto its markup so the tints show without script, and the cockpit swaps to the strict CSP so it is **self-contained** — opens in a plain browser with no Lavish. Optionally emits `review.md` (review + outcome + Q&A) for pasting into a PR as the *human's* review. *(Status: the concerns-led ordering and the five-state step vocabulary described here have landed — #87.)*
_Avoid_: export, render, regenerate.
