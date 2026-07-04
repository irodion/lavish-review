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
  → orchestrator authors review.html (layered L0–L3 — ADR-0009; escaped fragments at seams;
                                      empty Q&A + per-claim evidence seams planted)
  → copy assets into .review-agent/assets/   (cockpit.css, app.js — relative paths, Lavish requirement)
  → post-write lint                  (fail on unescaped <,> in untrusted regions or remote src/href)
  → lavish-axi@<pinned> review.html  (open, loopback only)
  → blocking answer loop             (poll ⇄ poll --agent-reply; dispositions persisted per poll;
                                      bounded live evidence injection — ADR-0012, amended ADR-0003)
  → /review-close                    (bake: Review outcome + Q&A folded in, strict CSP — self-contained)
```

## Architecture (ADR-0001, ADR-0002, ADR-0011)

- **Deterministic layer = diff + goal collection only.** No template engine, no render script. `analysis.json` is authored structured, `review.html` directly.
- **Orchestrator/analyst split (ADR-0011).** The invoking session is the *orchestrator*: it collects, spawns the analyst, validates, authors the cockpit, and drives the loop — but authors nothing analytical. The *analyst* (`.claude/agents/review-analyst.md` — the inspectable isolation boundary; tools `Read/Glob/Grep/Write` only, never a fork) forms threads, claims, and confidences from the collected artifacts alone, blind to the conversation that wrote the branch. The orchestrator never edits `analysis.json` — the file is the analyst's testimony; disagreements surface to the reviewer as questions.
- **Escape boundary.** Structure and prose are trusted authorship; untrusted data (diff bodies, file paths, commit messages, branch names, goal text, echoed feedback) is emitted by scripts via stdlib `html.escape` and injected at fixed seams. Hardening: strict CSP, vendored `app.js` (no inline JS), post-write lint tripwire.

## Diff collection

- Base **auto-detected**: `git symbolic-ref refs/remotes/origin/HEAD` → first existing of `main`/`develop`/`master`. Explicit arg overrides. **Ask on ambiguity** (detached HEAD, no remote, none found) rather than guess.
- Diff is `merge-base(base, HEAD)...HEAD`.
- **Default excludes** (lockfiles, `node_modules/`/`vendor/`/`third_party/`, `dist/`/`build/`/`*.min.js`/`*.generated.*`/`*.pb.go`; honor `.gitattributes linguist-generated`). Excluded files **omit body but keep existence + stat** in `changed-files.json`.
- **Per-file cap** (~1500 lines) omits body, tags "large change". **Total-diff guard** falls back to file-list + stats banner. **Nothing omitted is ever hidden** — all named in the cockpit.
- Context: diff-only seed; the analyst widens **deliberately** (read full changed file, grep callers of changed public symbols) only around high-risk changes, and must account for it in the required `widened_into` list (ADR-0011). No whole-repo crawl.
- **Goal Evidence** (ADR-0010): the collector also gathers the branch's stated purpose — precedence `--goal` (issue ref / file / text; never guessed over) > issue refs discovered in the branch name and commit messages (resolved via `gh` when `goal_remote_fetch` allows) > the first commit message. Offline-degrading, never blocking; provenance always attributed; `context.json` carries `goal: {text, source, provenance} | null`. Goal text is untrusted data through the same Escape Boundary.

## Cockpit layers (ADR-0009)

The surface is four pre-authored layers, each answering "why should I believe the layer above?", descended at the reviewer's pace by client-side disclosure (native `<details>` — no agent round-trip):

- **L0 — Goal alignment.** The Goal Evidence (or its degraded "no stated goal found" notice), the intent summary, and the `alignment` partition: which threads serve the goal, which are drive-bys.
- **L1 — Threads.** The changeset decomposed into narrative threads (semantic sub-changes, not files), each with per-thread review progress. Threads are the unit of the Review Route.
- **L2 — Claims.** Per thread, the assertions to judge — `behavior | risk | omission | verify` — each with the agent's confidence, ≥1 challenge question, evidence links, in-page Reviewer Disposition controls, and a pre-planted live-evidence seam.
- **L3 — Evidence.** Pre-escaped hunks, excerpts, and caller references per claim; the unified diff is leaf evidence, never the spine. Every changed file stays reachable here (nothing-hidden invariant), omitted bodies listed with reasons.

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

Diagrams: source still captured in `analysis.json`, rendering deferred. Styling: vendored `cockpit.css` default; `styling: cdn` opt-in uses Lavish's Tailwind+DaisyUI fallback. Test integration: **verify claims + read-only runner detection, no execution**.

### Deck Mode ([ADR-0014](./docs/adr/0014-deck-presentation-mode.md), [ADR-0015](./docs/adr/0015-claim-scoped-questions.md) — PRD'd, not yet implemented)

When served, the vendored script re-presents the same document as a **Map** (threads in route order with per-claim disposition dots, files with change-size bars, progress) beside a **Stage** (one claim at a time: chips, challenge questions, its evidence hunk inline via schema 0.3 hunk anchors, keyboard dispositions auto-advancing to the next *unreviewed* claim, and a **Claim-scoped Question** affordance that queues the claim id as structured data). Built strictly by relocating the document's already-escaped DOM nodes; document mode stays one toggle away and is the only mode on `file://` — the baked record is unchanged. The Cockpit Linter gains structural rules (evidence anchors resolve, claim ids unique and matching the analysis, seams present) since the deck consumes the document's structure. Chosen from a four-variant visual prototype (`prototypes/cockpit-visual-direction/`, kept as the implementation reference).

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
- **Reviewer Dispositions (ADR-0012):** in-page per-claim controls (`verified | concern | question-open`, JS-injected by the vendored `app.js`) queue structured updates through the same feedback channel (`tag: choice`, `data` payload, per-claim `queueKey`); each poll is folded into `dispositions.json` by the deterministic `dispositions.py apply` bridge — the agent never hand-parses or authors a disposition, and only the reviewer moves one. Run-scoped: reset on regeneration, carried across `Esc`/resume; delivery is presence-gated and eventually consistent within the session.
- **Controls:** `Esc` (hard interrupt — queued feedback preserved) · `/review-resume` (re-attach to the file-path-keyed session, no regeneration) · `/review-close` (`lavish-axi end`). Optional `pause` sentinel (installer config).
- **Persistence:** `qa.jsonl` appended during the session; folded into `review.html` (+ optional `review.md`) once at close by the **Q&A bake** ([ADR-0007](./docs/adr/0007-bake-prompt-extractor.md)) — the **Review outcome** first (the reviewer's dispositions aggregated with per-thread totals, unreviewed claims listed never hidden, per-claim state stamped onto the claim markup, **no agent verdict** — ADR-0012), then the Q&A log with disposition updates filtered out (state, not conversation); escaped through the Escape Boundary, idempotent via the `<!--brc:qa-log-->` seam, and re-CSP'd to strict so the saved cockpit is self-contained (opens in a plain browser, no Lavish). `review.md` is the pasteable *human's* review account. Mid-session answers render live in the Lavish chat.
- **Resume + staleness:** `session.json` carries `{status, base, branch, head_sha, merge_base, started_at}`, written `open` when the review opens and marked `ended` at close. The **Session Evaluator** (a deep module of pure policy) compares it against the current git branch and the resolved `base...HEAD` diff identity (HEAD, base, and `merge-base(base, HEAD)` — so a base that was switched or has advanced under a fixed HEAD is caught), returning one of `none | fresh | stale | different-branch`; `/review-branch` checks first (step 0) and acts on the verdict — restore a `fresh` review without regenerating, **regenerate by default** on `stale` (HEAD advanced, base changed, or merge-base moved; resume-anyway available), generate on `none`/`different-branch`. v1.1: ambient detection via Lavish's `SessionStart` hook.

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

The collector writes the resolved policy to `.review-agent/resolved-config.json` so the agent threads `styling` (cockpit assets + lint), `focus`/`language_hints` (authoring lenses), and the machine settings into the later steps.

## Packaging & security ([ADR-0013](./docs/adr/0013-self-contained-cross-platform-packaging.md))

- **Self-contained skill** at `.claude/skills/branch-review-cockpit/` (`SKILL.md`, `scripts/` shims, `assets/`, and the vendored `lib/branch_review/` package). All scripts are **agent-agnostic** (git + stdlib); the shims resolve the package via `scripts/_bootstrap.py` — this repo's `src/` in development, the vendored `lib/` when installed. `tools/sync_vendored.py` refreshes the vendored tree; `tests/test_packaging.py` fails on any drift.
- **Distribution: `npx skills add irodion/lavish-review`** (agentskills.io format) — installs on **Claude Code, Cursor, and Codex** (Codex/Cursor also discover `.agents/skills/`). No SKILL.md platform has post-install hooks, so setup beyond the copy is the skill's own idempotent `scripts/install.py`: machine config with the **pinned Lavish** version (`npx -y lavish-axi@<pinned>`; the pin's single source is `branch_review.install.PINNED_LAVISH_VERSION`, drift-tested against SKILL.md), `.gitignore` entries for both state dirs, and per-platform entry points (`/review-*` commands for Claude Code + Cursor, the `review-analyst` agent definition for Claude Code; Codex invokes skills natively). SessionStart-hook ambient resume stays a recorded config key (v1.1 roadmap).
- **Cross-platform analyst posture**: platforms without an isolated-subagent mechanism run the analysis in-context and the cockpit's L0 discloses that independence was not enforced by construction — degrade with disclosure, never silently (the host-seam posture applied to the agent platform).
- **Loopback only** — never set `LAVISH_AXI_HOST` to a wildcard (exposes an unauthenticated local-file server). No MCP, no remote upload of repo code, browser feedback is **untrusted data** (logged, never executed, never used to build a shell command), no auto-apply of code, no auto-commit.

## Scope

**In (implemented):** the full pipeline above — `/review-branch [base] [--goal …]`, Goal Evidence ingestion, isolated analyst + validated claim-centric analysis (`review-analysis/0.2`), layered L0–L3 cockpit, escaping + CSP + lint, blocking loop with the three controls, Reviewer Dispositions, bounded live evidence injection, `qa.jsonl` + outcome-and-Q&A bake-at-close, `session.json` + staleness offer, minimal `.review-agent.yaml`, self-contained packaging + first-run installer across Claude Code/Cursor/Codex (ADR-0013).

**Next (PRD'd):** Deck Mode — the map+stage served presentation, hunk-anchored evidence (`review-analysis/0.3`), claim-scoped questions, structural lint rules, judgment-color restyle with a light theme (ADR-0014, ADR-0015).

**Deferred (roadmap, retained):** the C++/Python/TS Language Lenses (#11, descoped from packaging), the Focus Lens Catalog (security/OWASP, regressions, simplification, supply-chain) with authoring-time + mid-review (Lens Pass) activation — designed (ADR-0005, ADR-0006) but unimplemented, paused pending re-targeting onto the Layered Review v2 claim model (ADR-0009; #31–#34), carrying dispositions across a regeneration (content-matched claim identity, ADR-0012), agent-session transcripts as a Goal Evidence source (ADR-0010), an adversarial multi-analyst verification pass (ADR-0011), Mermaid rendering (vendored), `diff2html` side-by-side (deprioritized by construction — the diff is leaf evidence, #22), Python Lavish fallback, further external-tool-findings lenses (`semgrep`/`ruff`/`clang-tidy` on the substrate the supply-chain lens establishes), additional-skills config, user-defined language hints, user-defined Focus Lenses, ambient SessionStart-hook resume.
