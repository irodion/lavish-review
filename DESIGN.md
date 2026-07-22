# Branch Review Cockpit — Design

A local, AI-assisted Claude Code skill that turns a Git branch diff into an interactive HTML **Review Cockpit**, opened and driven through [Lavish-AXI](https://www.npmjs.com/package/lavish-axi), to help a human reviewer audit AI- or human-generated changes faster. It reduces review navigation cost; it does **not** automate the review decision.

See [CONTEXT.md](./CONTEXT.md) for the glossary and [docs/adr/](./docs/adr/) for the load-bearing decisions.

## Pipeline

```text
/review-branch [base] [--goal <issue-ref|file|text>]
  → session evaluator                (step 0: restore fresh, regenerate stale — see "Resume + staleness")
  → collect_review_context.py        (deterministic: git diff, goal evidence, escaping, context.json)
  → isolated analyst authors analysis.json   (fresh subagent, blind to this conversation — ADR-0011;
                                              validated; ≤3 repair rounds, fixes only by the analyst)
  → render_cockpit.py                (deterministic layered L0–L3 representation;
                                      escaped prose/fragments; seams planted; atomic write)
  → copy assets into .review-agent/assets/   (cockpit.css, app.js — relative paths, Lavish requirement)
  → post-write lint                  (fail on unescaped <,> in untrusted regions or remote src/href;
                                      with --analysis, also on claim/seam/anchor drift — ADR-0014)
  → lavish-axi@<pinned> review.html  (open, loopback only)
  → blocking answer loop             (poll ⇄ poll --agent-reply; dispositions persisted per poll;
                                      bounded live evidence injection — ADR-0012, amended ADR-0003)
  → /review-close                    (bake: Review outcome + Q&A folded in, strict CSP — self-contained)
```

## Architecture (ADR-0001, ADR-0002, ADR-0011)

- **Deterministic representation layer.** Collection produces escaped source artifacts; `render_cockpit(run_dir)` validates `analysis.json`, constructs the complete cockpit, resolves evidence, escapes prose, lints, and atomically writes `review.html`.
- **Orchestrator/analyst/renderer split (ADR-0011/0016).** The invoking session is the *orchestrator*: it collects, spawns the analyst, validates, invokes the renderer, and drives the loop — but authors neither analysis nor HTML. The *analyst* is an **independent change narrator** (`.claude/agents/review-analyst.md` — the inspectable isolation boundary; tools `Read/Glob/Grep/Write` only, never a fork): it forms threads, guided Review Steps (thread order is the Review Route), their Behavior Impacts, and confidences from the collected artifacts alone, blind to the conversation that wrote the branch. It narrates by default — no risk/omission/verify hunting unless a Focus Lens is configured — and classifies impact from the diff alone (intent may narrate, only evidence may classify — ADR-0016). The orchestrator never edits `analysis.json` — the file is the narrator's testimony; disagreements surface to the reviewer as questions.
- **Escape boundary.** The narrator's prose and all collected review data are untrusted HTML inputs. The renderer and mutation helpers apply stdlib `html.escape` at fixed seams. Hardening: bounded CSP, vendored `app.js` (no inline JS), post-write lint tripwire.

## Diff collection

- Base **auto-detected**: `git symbolic-ref refs/remotes/origin/HEAD` → first existing of `main`/`develop`/`master`. Explicit arg overrides. **Ask on ambiguity** (detached HEAD, no remote, none found) rather than guess.
- Diff is `merge-base(base, HEAD)...HEAD`.
- **Default excludes** (lockfiles, `node_modules/`/`vendor/`/`third_party/`, `dist/`/`build/`/`*.min.js`/`*.generated.*`/`*.pb.go`; honor `.gitattributes linguist-generated`). Excluded files **omit body but keep existence + stat** in `changed-files.json`.
- **Per-file cap** (~1500 lines) omits body, tags "large change". **Total-diff guard** falls back to file-list + stats banner. **Nothing omitted is ever hidden** — all named in the cockpit.
- Context: diff-only seed; the analyst widens **deliberately** (read full changed file, grep callers of changed public symbols) only around high-risk changes, and must account for it in the required `widened_into` list (ADR-0011). No whole-repo crawl.
- **Goal Evidence** (ADR-0010): the collector also gathers the branch's stated purpose — precedence `--goal` (issue ref / file / text; never guessed over) > issue refs discovered in the branch name and commit messages (resolved via `gh` when `goal_remote_fetch` allows) > the first commit message. Offline-degrading, never blocking; provenance always attributed; `context.json` carries `goal: {text, source, provenance} | null`. Goal text is untrusted data through the same Escape Boundary.

## Cockpit layers (ADR-0009)

The surface is four pre-authored layers, each answering "why should I believe the layer above?", descended at the reviewer's pace by client-side disclosure (native `<details>` — no agent round-trip):

- **L0 — Goal alignment.** The Goal Evidence (or its degraded "no stated goal found" notice), the intent summary, the `alignment` partition (which threads serve the goal, which are drive-bys), and a **derived route reading-weight budget** ("~90 min at reading pace", heuristic stated).
- **L1 — Threads.** The changeset decomposed into narrative threads (semantic sub-changes, not files), each with per-thread review progress and a rolled-up reading-weight total. Threads are the unit of the Review Route.
- **L2 — Claims.** Per thread, the assertions to judge — `behavior | risk | omission | verify` — each with the agent's confidence, ≥1 challenge question, evidence links, in-page Reviewer Disposition controls, a pre-planted live-evidence seam, and a **derived reading weight** (issue #100): a per-step reading-cost chip, never authored. It is derived at render time from the step's own evidence — a hunk-anchored ref contributes that hunk's line count, a file-level ref contributes the file's changed lines capped at a bound, note-only evidence marks the weight an approximate floor — rolled up per thread and for the whole route. The contribution rule lives in `src/branch_review/weight.py`; the Map sizes each dot by its step's weight (emphasis via size, never colour — judgment-color discipline holds).
- **L3 — Evidence.** Pre-escaped hunks, excerpts, and caller references per claim; the unified diff is leaf evidence, never the spine. Every changed file stays reachable here (nothing-hidden invariant), omitted bodies listed with reasons. A **reverse hunk↔step index** (issue #103), computed at render time, names in each hunk's margin the Review Step(s) that narrate it — linked, so a click jumps to the step in document mode and stages it in Deck Mode; a file-level ref annotates the file header instead, and a hunk no step anchors carries a neutral `un-narrated` marker (a narration state, judgment-color discipline). This upgrades nothing-hidden from "reachable at L3" to "visibly accounted for by narration".

Mapping from the v1 section names (for older issues/ADRs):

| v1 section | v2 home |
|---|---|
| Executive Summary | L0 orientation (+ L1 thread summaries) |
| Review Route | the descent order across threads (L1) |
| Behavior Changes | L2 claims, `kind: behavior` |
| Risk Map | L2 claims, `kind: risk` (category, level, challenge questions as claim attributes) |
| Suspicious Omissions | L2 claims, `kind: omission` (kinds now include `goal`) |
| File Walkthrough | L3 evidence |
| Diff | L3 evidence (leaf level) |
| Test Checklist | L2 claims, `kind: verify` (take dispositions like any claim) |

Diagrams: source still captured in `analysis.json`, rendering deferred. Styling: the cockpit always ships its core vendored `cockpit.css`/`app.js`; the retained `styling: cdn` compatibility mode relaxes the asset lint for host/CDN additions but does not replace those core assets. Test integration: **Review Prompts + read-only runner detection, no execution**.

### Deck Mode ([ADR-0014](./docs/adr/0014-deck-presentation-mode.md), [ADR-0015](./docs/adr/0015-claim-scoped-questions.md) — skeleton + keyboard flow landed; Stage-side ask pending)

When served, the vendored script re-presents the same document as a **Map** (threads in route order with per-claim disposition dots, files with change-size bars, progress) beside a **Stage** (one claim at a time: chips, challenge questions, its evidence hunk inline via schema 0.3 hunk anchors, keyboard dispositions auto-advancing to the next *unreviewed* claim, and a **Claim-scoped Question** affordance that queues the claim id as structured data). Built strictly by relocating the document's already-escaped DOM nodes; document mode stays one toggle away and is the only mode on `file://` — the baked record is unchanged. The **skeleton has shipped** (issue #67): the Presenter builds the Map and Stage, dot/thread clicks stage a claim, the evidence hunk renders inline, and the mode toggle round-trips content, open state, and disposition tints — served-only, and covered by the repo's first JS tests (a zero-dependency Node DOM harness under `tests/js/`, run in CI alongside pytest). The **keyboard flow has shipped** (issue #68): an oversized V/C/Q control on the Stage (with visible key hints) sets the staged claim's disposition through the unchanged presence-gated channel — payloads identical to the document-mode controls, the bridge untouched — and setting one auto-advances to the next *unreviewed* claim in Review Route order (never skipping, never landing on a reviewed one, staying put and saying so when none remain); V/C/Q and J/K are single keys, ignored while a typing surface (the claim-scoped ask box, host chat) is focused, and the Map dots, thread fractions, and overall progress update live (restored dispositions tint the Map on resume). Still to come on that skeleton: the Stage-side claim-scoped ask. Two further foundations shipped ahead of the presentation layer (ADR-0014): the Cockpit Linter **enforces** the structural rules the deck relies on (evidence anchors resolve, claim ids unique and matching the analysis, seams present and paired), and the **Hunk Anchorer** emits deterministic per-hunk ids in each file fragment plus a hunk index in `fragments.json`, so schema-0.3 `{path, hunk}` evidence links land on the exact hunk. The visual direction (variant D — B's persistent Map around C's one-claim Stage) was chosen from a four-variant throwaway prototype whose verdict lives in ADR-0014/0015; that prototype was removed once the Map/Stage shipped. The end-to-end dogfood of a real review in the shipped Deck Mode, and its visual sign-off, remain open (#69).

## Lenses

A **Lens** sharpens the neutral-by-default analysis; it is not separate machinery and never adds a cockpit section or risk category. Two kinds:

- **Language Lens** (issue #11): a bundled, language-specific risk checklist (C++/Python/TS) the analyst consults while forming risk claims, selected by detected language + `language_hints`.
- **Focus Lens**: a reviewer-chosen *perspective* that re-weights and re-frames the threads, claims, and feedback-loop answers toward a concern (ADR-0009: a lens re-weights threads and claims, not a flat Risk Map). Two activation paths: **authoring-time** via the `focus` config key / CLI (shapes the whole cockpit), and **mid-review** via a **Lens Pass** through the feedback loop (re-analyzes a slice, answers live, logged in `qa.jsonl` for bake-at-close; a pass that mints *new* claims runs a fresh isolated analyst, ADR-0011 — **no `review.html` regeneration**, per amended ADR-0003). The re-invokable mid-review path is what distinguishes a Focus Lens from a one-shot authoring choice, and is why it waited on the loop (#5).

**Focus Lens Catalog** (designed — bundled definitions, same shape as Language Lenses; **not yet implemented**, paused pending re-targeting onto the Layered Review v2 claim model, ADR-0009 / #31–#34):

- **security / OWASP** — reframes toward attack surface; maps risks to OWASP Top 10 / CWE. Pure agent reasoning.
- **regressions** — reframes toward what could break that used to work (changed public surface, untouched callers; leans on Suspicious Omissions). Pure agent reasoning.
- **simplification** — advisory **design critique** ("what are our choices? can we do this simpler?"). Proposes alternatives as `maintainability`-framed entries and loop answers; **never patches, never decides** (ADR-0005). This is the bounded expansion of the cockpit from change-audit to advisory critique.
- **supply-chain** — runs [`vet`](https://github.com/safedep/vet) on **changed dependency manifests** to surface known-vulnerable / malicious / license-problematic added or bumped deps. **Opt-in, offline-safe**: runs only when selected *and* a manifest changed; degrades to an agent-reasoned note when `vet` is absent or the network is down; tool output is escaped untrusted data; findings fold into the `security` category (ADR-0006). First instance of the external-tool-findings substrate (the PRD's deferred `semgrep`/`ruff`/`clang-tidy` category).

All Focus Lens findings fold into the existing claim model (risk-claim categories) and answers — no new sections, no new claim kinds.

## Feedback loop (ADR-0003, amended by ADR-0009; ADR-0012)

- One command enters a **blocking answer loop**: `lavish-axi poll` (no-timeout) returns queued feedback; agent answers and re-polls with `--agent-reply`.
- **Verified I/O contract** ([spike](./docs/spikes/lavish-poll-format.md), v0.1.31): `poll` writes **TOON** to stdout with `session.status` ∈ `feedback | waiting | ended | missing`; feedback carries `prompts[N]` of `{uid, prompt, selector, tag, text, target?}` where `tag` ∈ `message | annotation | choice | …`. **No TOON parser is written in the live loop** — the agent reads poll stdout directly as its own input (TOON is built for agent consumption, and the tool's `next_step` field states the next command). The one bounded exception is offline at close: the Q&A bake lifts the reviewer's questions from the stored poll TOON with a single-block `prompts[N]` extractor ([ADR-0007](./docs/adr/0007-bake-prompt-extractor.md)). `--agent-reply` both shows the prior answer in the browser *and* resumes blocking. Interrupt exits 130/143 with feedback preserved — this is the mechanism behind `Esc`/`/review-resume`.
- **Amended page-mutation rule (ADR-0009):** "no per-answer HTML regeneration" became "no page *regeneration*; seam-bounded fragment *injection* only." When a mid-review answer *is* new evidence the page should keep, it is injected at the claim's pre-planted `<!--brc:evidence:tN.cM-->` seam — escaped, then the whole candidate page linted **before** anything is written (lint failure → nothing written, answer in chat: the floor). Records live run-scoped in `live-evidence.json`, never appended to `analysis.json` (the analyst's testimony, ADR-0011); the served page re-renders by itself (Lavish chokidar → SSE, verified by the [host-seam spike](./docs/spikes/lavish-live-injection.md), #38). Chat answers remain the default.
- **Reviewer Dispositions (ADR-0012, reframed by ADR-0016):** in-page per-step controls (`looks-right | concern | follow-up | skipped`, plus `unreviewed` as absence — JS-injected by the vendored `app.js`) queue structured updates through the same feedback channel (`tag: choice`, `data` payload with the `step` id, per-step `queueKey`); each poll is folded into `dispositions.json` by the deterministic `dispositions.py apply` bridge — the agent never hand-parses or authors a disposition, and only the reviewer moves one. `skipped` is a deliberate, attributed act, stored and distinct from `unreviewed` absence. Run-scoped: reset on regeneration, carried across `Esc`/resume; delivery is presence-gated and eventually consistent within the session.
- **Controls:** `Esc` (hard interrupt — queued feedback preserved) · `/review-resume` (re-attach to the file-path-keyed session, no regeneration) · `/review-close` (`lavish-axi end`). Optional `pause` sentinel (installer config).
- **Persistence:** `qa.jsonl` appended during the session; folded into `review.html` (+ optional `review.md`) once at close by the **Q&A bake** ([ADR-0007](./docs/adr/0007-bake-prompt-extractor.md)) — the **Review outcome** first (the reviewer's dispositions aggregated with per-thread totals, ordered concerns → follow-ups → coverage (looks-right / skipped-with-impacts / unreviewed steps listed never hidden), per-step state stamped onto the step markup, **no agent verdict** — ADR-0012/0016), then the Q&A log with disposition updates filtered out (state, not conversation); escaped through the Escape Boundary, idempotent via the `<!--brc:qa-log-->` seam, and re-CSP'd to strict so the saved cockpit is self-contained (opens in a plain browser, no Lavish). `review.md` is the pasteable *human's* review account. Mid-session answers render live in the Lavish chat.
- **Resume + staleness:** `session.json` carries `{status, base, branch, head_sha, merge_base, analysis_schema, started_at, resume_seq}`, written `open` when the review opens and marked `ended` at close. The **Session Evaluator** (a deep module of pure policy) compares it against the current git branch, the resolved `base...HEAD` diff identity (HEAD, base, and `merge-base(base, HEAD)` — so a base that was switched or has advanced under a fixed HEAD is caught), and the analysis schema this code speaks, returning one of `none | fresh | stale | stale-schema | different-branch`; `/review-branch` checks first (step 0) and acts on the verdict — restore a `fresh` review without regenerating, **regenerate by default** on `stale` (HEAD advanced, base changed, or merge-base moved; resume-anyway available), **regenerate with no resume-anyway** on `stale-schema` (the saved analysis predates the current `review-analysis` schema, so the loop and bake can no longer read it — ADR-0016's clean break), generate on `none`/`different-branch`. `resume_seq` is a monotonic counter the two resume entry points (`/review-resume`, and the step-0 `fresh` restore) bump via `session.py resume`; it is the served recap's **explicit resume signal** (issue #102) — the page stages a "previously on…" recap only when the counter has advanced beyond what the reviewer's browser tab has acknowledged, so a page reload that *follows* a resume shows the card while a mid-review injection reload does not. v1.1: ambient detection via Lavish's `SessionStart` hook.

## On-disk layout

```text
.review-agent/            (gitignored — generated)
  context.json  diff.patch  diff-stat.txt  changed-files.json  commits.txt
  resolved-config.json                     (context.json carries the goal block — ADR-0010)
  analysis.json  review.html  review.md  qa.jsonl  session.json
  dispositions.json        (reviewer dispositions, keyed by claim id — ADR-0012)
  live-evidence.json       (record of live-injected evidence fragments — ADR-0009)
  assets/  cockpit.css  app.js
.lavish-axi/              (gitignored — Lavish session state)
.review-agent.yaml        (committed — repo policy)
```

Run-scoped artifacts (`qa.jsonl`, `dispositions.json`, `live-evidence.json`, `analysis.json`, the loop's poll/reply files) are reset by the collector on regeneration and carried across `Esc`/resume.

## Configuration

Resolved by the **Config Resolver** (a pure-policy deep module + thin file-reading shell), which layers **command arg > repo `.review-agent.yaml` > machine `~/.review-agent/config.yaml` > defaults**. Absent files fall back to defaults; unknown keys and out-of-range values are located errors, never silent fallbacks. It ships a strict stdlib loader for the flat YAML subset the schema uses — **no third-party YAML dependency** ([ADR-0008](./docs/adr/0008-stdlib-config-loader.md)). Two non-overlapping scopes:

- **Repo policy** — `.review-agent.yaml` (committed): `base_branch`, `exclude` (**extends** built-ins; `exclude_reset: true` to replace), `focus`, `language_hints`, `styling`, `limits.{max_file_diff_lines, max_total_diff_lines}`, `goal_remote_fetch`. All optional.
- **Per-machine** — `~/.review-agent/config.yaml`: `pause` sentinel word, default `styling`, pinned Lavish version, SessionStart-hook on/off, `goal_remote_fetch`.

`goal_remote_fetch` (ADR-0010) lives in both scopes — repo wins, default `true`; set `false` to keep goal resolution strictly local (no `gh` calls).

The collector writes the resolved policy to `.review-agent/resolved-config.json` so the renderer threads `styling` into its asset-policy lint, while the agent threads `focus`/`language_hints` (authoring lenses) and machine settings into later steps. Core cockpit assets remain vendored in both styling modes.

## Packaging & security ([ADR-0013](./docs/adr/0013-self-contained-cross-platform-packaging.md))

- **Self-contained skill** at `.claude/skills/branch-review-cockpit/` (`SKILL.md`, `scripts/` shims, `assets/`, and the vendored `lib/branch_review/` package). All scripts are **agent-agnostic** (git + stdlib); the shims resolve the package via `scripts/_bootstrap.py` — this repo's `src/` in development, the vendored `lib/` when installed. `tools/sync_vendored.py` refreshes the vendored tree; `tests/test_packaging.py` fails on any drift.
- **Distribution: `npx skills add irodion/lavish-review`** (agentskills.io format) — installs on **Claude Code, Cursor, and Codex** (Codex/Cursor also discover `.agents/skills/`). No SKILL.md platform has post-install hooks, so setup beyond the copy is the skill's own idempotent `scripts/install.py`: machine config with the **pinned Lavish** version (`npx -y lavish-axi@<pinned>`; the pin's single source is `branch_review.install.PINNED_LAVISH_VERSION`, drift-tested against SKILL.md), `.gitignore` entries for both state dirs, and per-platform entry points (`/review-*` commands for Claude Code + Cursor, the `review-analyst` agent definition for Claude Code; Codex invokes skills natively). SessionStart-hook ambient resume stays a recorded config key (v1.1 roadmap).
- **Cross-platform analyst posture**: platforms without an isolated-subagent mechanism run the analysis in-context and the cockpit's L0 discloses that independence was not enforced by construction — degrade with disclosure, never silently (the host-seam posture applied to the agent platform).
- **Loopback only** — never set `LAVISH_AXI_HOST` to a wildcard (exposes an unauthenticated local-file server). No MCP, no remote upload of repo code, browser feedback is **untrusted data** (logged, never executed, never used to build a shell command), no auto-apply of code, no auto-commit.

## Scope

**In (implemented):** the full pipeline above — `/review-branch [base] [--goal …]`, Goal Evidence ingestion, isolated analyst + validated claim-centric analysis (`review-analysis/0.3`, hunk-anchored evidence — ADR-0014), layered L0–L3 cockpit, escaping + CSP + a post-write lint that enforces both the escape/CSP hardening **and** structural coherence against the analysis (claim ids match, evidence anchors resolve, Q&A/evidence seams present and paired — ADR-0014; wired into `/review-branch`, `/review-close`, and evidence injection), blocking loop with the three controls, Reviewer Dispositions, claim-scoped questions (the per-claim ask affordance on document-mode claim panels + the loop's `kind: claim-question` grounding — ADR-0015), bounded live evidence injection, `qa.jsonl` + outcome-and-Q&A bake-at-close, `session.json` + staleness offer, minimal `.review-agent.yaml`, self-contained packaging + first-run installer across Claude Code/Cursor/Codex (ADR-0013), judgment-color discipline with a `prefers-color-scheme` light theme (colour reserved for risk level / confidence / disposition, every colored chip glyph-and-word, neutral kind chips — ADR-0014); Deck Mode's served **Map + Stage** presentation with the **keyboard disposition flow** (ADR-0014/0015): the Presenter builds Map and Stage by relocating the document's own already-escaped DOM nodes, dot/thread clicks stage a claim, its evidence hunk renders inline, the mode toggle round-trips content/open-state/tints losslessly, and V/C/Q keyboard dispositions on the Stage auto-advance to the next *unreviewed* claim in Review Route order (#67, #68) — served-only, covered by the repo's zero-dependency Node DOM test harness under `tests/js/`.

**Next (PRD'd):** the Deck Mode **Stage-side claim-scoped ask** affordance, and the end-to-end dogfood of a real review in the shipped Deck Mode plus its visual sign-off (#69) — the remaining work on the shipped Map/Stage skeleton. *(The broader **Change Narrator** reframe — the Review Step replacing the Claim as the L2 spine, `review-analysis/0.4`, narration over issue-finding — is separately PRD'd in #82 / [ADR-0016](./docs/adr/0016-guided-change-narration-surface.md).)*

**Deferred (roadmap, retained):** the C++/Python/TS Language Lenses (#11, descoped from packaging), the Focus Lens Catalog (security/OWASP, regressions, simplification, supply-chain) with authoring-time + mid-review (Lens Pass) activation — designed (ADR-0005, ADR-0006) but unimplemented, paused pending re-targeting onto the Layered Review v2 claim model (ADR-0009; #31–#34), carrying dispositions across a regeneration (content-matched claim identity, ADR-0012), agent-session transcripts as a Goal Evidence source (ADR-0010), an adversarial multi-analyst verification pass (ADR-0011), Mermaid rendering (vendored), `diff2html` side-by-side (deprioritized by construction — the diff is leaf evidence, #22), Python Lavish fallback, further external-tool-findings lenses (`semgrep`/`ruff`/`clang-tidy` on the substrate the supply-chain lens establishes), additional-skills config, user-defined language hints, user-defined Focus Lenses, ambient SessionStart-hook resume.
