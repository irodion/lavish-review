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

**Review Route**:
The ordered path the cockpit recommends a reviewer follow through the changes ("start here, then these files, then verify tests"). A first-class section, not just a file list.
_Avoid_: walkthrough order, reading order.

**Risk Map**:
The set of changes grouped by risk category (correctness, compatibility, concurrency, security, performance, maintainability, test coverage), each with a level, a reason, and challenge questions.
_Avoid_: risk list, findings.

**Suspicious Omission**:
Something the diff did *not* change but arguably should have — untouched tests, callers, docs, config, or error handling adjacent to a behavioral change.
_Avoid_: gap, missing change.

**Change Classifier**:
The deterministic noise-control step that decides, per changed file, whether its diff *body* belongs in the cockpit. It keeps noisy branches reviewable without ever silently hiding a change: only bodies are dropped — a file's existence and stats are always kept and listed. Default excludes cover lockfiles, vendored/generated/build trees, and `.gitattributes linguist-generated`; a per-file line cap and a whole-changeset total cap bound the rest.
_Avoid_: filter, noise filter, ignore list.

**Disposition**:
The Change Classifier's verdict for one file — one of `include-body`, `omit:lockfile`, `omit:excluded`, or `omit:too-large`. Every omitting disposition carries a human reason shown beside the still-listed file. The total-diff fallback re-stamps included files as `omit:too-large` and flags the changeset so the cockpit shows a file-list + stats banner instead of diffs.
_Avoid_: verdict, status, category.

**Analysis** (`analysis.json`):
The agent's structured intermediate reasoning about the diff (intent, behavior changes, review route, risk map, omissions, test checklist, diagrams). It is the substrate the Review Cockpit is authored from and the substrate the feedback loop answers from.
_Avoid_: report, summary.

**Lens**:
The umbrella term for an analytical frame the agent applies while authoring the cockpit or answering. There are two kinds — a **Language Lens** and a **Focus Lens**. Lenses sharpen a neutral-by-default analysis; they are not separate machinery.
_Avoid_: profile, ruleset, plugin.

**Language Lens**:
An optional, language-specific risk checklist the agent consults while authoring the Risk Map (e.g. the C++ lens covers ownership, lifetime, threading, ABI). Selected by detected language and config.
_Avoid_: profile, ruleset, plugin.

**Focus Lens**:
A reviewer-chosen *perspective* that reframes the analysis toward a concern, distinct from a Language Lens (which is about the *code's language*, not the reviewer's *concern*). A Focus Lens does not add cockpit sections or risk categories — it re-weights and re-frames the existing Risk Map, Review Route, and feedback-loop answers toward its concern (the Lens principle: sharpen the neutral analysis, don't bolt on machinery). It has two activation paths: **at authoring time**, selected by the `focus` config key / CLI, it shapes the whole cockpit; **mid-review**, invoked through the Feedback Loop ("dig into this from an OWASP angle"), it runs a **Lens Pass** without regenerating the cockpit. The v1 catalog is the **Focus Lens Catalog** below. Most lenses are pure agent reasoning over the diff; the supply-chain lens is the exception that admits **External-Tool Findings**.
_Avoid_: mode, filter, view, perspective.

**Focus Lens Catalog**:
The bundled set of Focus Lenses shipped with the skill, each a definition the agent consults to reframe its analysis (the same bundling shape as Language Lenses). v1 ships four: **security/OWASP** (reframes toward attack surface; maps risks to OWASP Top 10 / CWE), **regressions** (toward what could break that used to work — changed public surface, untouched callers), **simplification** (advisory *design critique* — "can we do this simpler?"; proposes alternatives, never patches — see [ADR-0005](./docs/adr/0005-design-critique-scope.md)), and **supply-chain** (runs `vet` on changed dependency manifests; opt-in, offline-safe; see [ADR-0006](./docs/adr/0006-external-tool-findings.md)). The first three are pure agent reasoning; supply-chain is the external-tool lens.
_Avoid_: lens registry, plugin list, ruleset.

**Lens Pass**:
A mid-review application of a Focus Lens, invoked by the reviewer through the Feedback Loop ("show me this from a security angle"). The agent re-analyzes the relevant slice through the chosen lens and delivers the result as a live loop answer, appending it to the Analysis and the Q&A Log so it is baked in at close — it does **not** regenerate `review.html` (consistent with [ADR-0003](./docs/adr/0003-single-blocking-poll-loop.md)'s no-per-answer-regeneration). This is what makes a Focus Lens *re-invokable* rather than a one-shot authoring-time choice.
_Avoid_: re-render, re-run, refresh.

**External-Tool Finding**:
A finding produced by an external analyzer (v1: `vet`, via the supply-chain Focus Lens) rather than by agent reasoning over the diff. It enters the cockpit only through an opt-in lens, is captured through the Escape Boundary like any other untrusted data (rendered, never executed), is attributed to its tool, and folds into the existing Risk Map. When the tool is absent or offline the finding degrades to an agent-reasoned note — it never blocks the review (see [ADR-0006](./docs/adr/0006-external-tool-findings.md)). This is the PRD's deferred "external-CLI-tool findings" category; `vet` is its first instance.
_Avoid_: scan result, tool output, report.

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
The blocking answer loop the skill sits in after opening the cockpit: `lavish-axi poll` returns the reviewer's queued questions/annotations, the agent answers them in the browser chat grounded in the diff/repo, and re-polls with `--agent-reply` — repeating until the Session ends or is interrupted. The agent reads the poll output (TOON) directly; there is no parser in the loop (the Q&A Bake's offline `prompts[N]` extractor is the one bounded exception — [ADR-0007](./docs/adr/0007-bake-prompt-extractor.md)). Browser feedback is *untrusted data* — answered and logged, never executed and never used to build a shell command.
_Avoid_: chat loop, poll loop, conversation.

**Q&A Log** (`qa.jsonl`):
The live transcript of the Feedback Loop — one JSON Lines record per exchange (`seq`, `ts`, the raw question, the agent's answer), appended as the review happens. At close the **Q&A Bake** folds it into the Review Cockpit (and optional `review.md`).
_Avoid_: history, chat log, transcript file.

**Q&A Bake**:
The close-time step that folds the Q&A Log into `review.html` so the saved cockpit shows the whole discussion offline. It lifts each reviewer question from the stored poll TOON with a bounded single-block extractor ([ADR-0007](./docs/adr/0007-bake-prompt-extractor.md)), escapes everything through the Escape Boundary, fills a `<!--brc:qa-log-->` seam (idempotently), and swaps the cockpit to the strict CSP so it is **self-contained** — opens in a plain browser with no Lavish. Optionally emits `review.md` (review + Q&A) for pasting into a PR.
_Avoid_: export, render, regenerate.
