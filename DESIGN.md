# Branch Review Cockpit — Design

A local, AI-assisted Claude Code skill that turns a Git branch diff into an interactive HTML **Review Cockpit**, opened and driven through [Lavish-AXI](https://www.npmjs.com/package/lavish-axi), to help a human reviewer audit AI- or human-generated changes faster. It reduces review navigation cost; it does **not** automate the review decision.

See [CONTEXT.md](./CONTEXT.md) for the glossary and [docs/adr/](./docs/adr/) for the load-bearing decisions.

## Pipeline

```
/review-branch [base]
  → collect_review_context.py        (deterministic: git diff, escaping, context.json)
  → agent authors analysis.json      (structured reasoning; diff-only seed, bounded widening)
  → agent authors review.html        (frame + prose; escaped data fragments injected at the seam)
  → copy assets into .review-agent/assets/   (cockpit.css, app.js — relative paths, Lavish requirement)
  → post-write lint                  (fail on unescaped <,> in untrusted regions or remote src/href)
  → lavish-axi@<pinned> review.html  (open, loopback only)
  → blocking answer loop             (poll  ⇄  poll --agent-reply)
```

## Architecture (ADR-0001, ADR-0002)

- **Deterministic layer = diff collection only.** No template engine, no render script. The agent authors `analysis.json` (structured) and `review.html` (directly).
- **Escape boundary.** The agent writes structure and prose; untrusted data (diff bodies, file paths, commit messages, branch names, echoed feedback) is emitted by scripts via stdlib `html.escape` and injected at a fixed seam. Hardening: strict CSP, vendored `app.js` (no inline JS), post-write lint tripwire.

## Diff collection

- Base **auto-detected**: `git symbolic-ref refs/remotes/origin/HEAD` → first existing of `main`/`develop`/`master`. Explicit arg overrides. **Ask on ambiguity** (detached HEAD, no remote, none found) rather than guess.
- Diff is `merge-base(base, HEAD)...HEAD`.
- **Default excludes** (lockfiles, `node_modules/`/`vendor/`/`third_party/`, `dist/`/`build/`/`*.min.js`/`*.generated.*`/`*.pb.go`; honor `.gitattributes linguist-generated`). Excluded files **omit body but keep existence + stat** in `changed-files.json`.
- **Per-file cap** (~1500 lines) omits body, tags "large change". **Total-diff guard** falls back to file-list + stats banner. **Nothing omitted is ever hidden** — all named in the cockpit.
- Context: diff-only seed; agent widens **deliberately** (read full changed file, grep callers of changed public symbols) only around high-risk changes. No whole-repo crawl.

## Cockpit sections

Executive Summary · Review Route · Behavior Changes · Risk Map (categories: correctness, compatibility, concurrency, security, performance, maintainability, test coverage; + optional C++/Python/TS **lenses**) · File Walkthrough · Diff (unified, escaped `<pre>`) · Suspicious Omissions · Test Checklist · Diagrams (source captured in `analysis.json`, rendering deferred).

Styling: vendored `cockpit.css` default; `styling: cdn` opt-in uses Lavish's Tailwind+DaisyUI fallback. Test integration: **checklist + read-only runner detection, no execution**.

## Lenses

A **Lens** sharpens the neutral-by-default analysis; it is not separate machinery and never adds a cockpit section or risk category. Two kinds:

- **Language Lens** (issue #11): a bundled, language-specific risk checklist (C++/Python/TS) folded into the Risk Map, selected by detected language + `language_hints`.
- **Focus Lens**: a reviewer-chosen *perspective* that re-weights and re-frames the Risk Map, Review Route, and feedback-loop answers toward a concern. Two activation paths: **authoring-time** via the `focus` config key / CLI (shapes the whole cockpit), and **mid-review** via a **Lens Pass** through the feedback loop (re-analyzes a slice, answers live, appends to `analysis.json` + `qa.jsonl` for bake-at-close — **no `review.html` regeneration**, per ADR-0003). The re-invokable mid-review path is what distinguishes a Focus Lens from a one-shot authoring choice, and is why it waited on the loop (#5).

**v1 Focus Lens Catalog** (bundled definitions, same shape as Language Lenses):

- **security / OWASP** — reframes toward attack surface; maps risks to OWASP Top 10 / CWE. Pure agent reasoning.
- **regressions** — reframes toward what could break that used to work (changed public surface, untouched callers; leans on Suspicious Omissions). Pure agent reasoning.
- **simplification** — advisory **design critique** ("what are our choices? can we do this simpler?"). Proposes alternatives as `maintainability`-framed entries and loop answers; **never patches, never decides** (ADR-0005). This is the bounded expansion of the cockpit from change-audit to advisory critique.
- **supply-chain** — runs [`vet`](https://github.com/safedep/vet) on **changed dependency manifests** to surface known-vulnerable / malicious / license-problematic added or bumped deps. **Opt-in, offline-safe**: runs only when selected *and* a manifest changed; degrades to an agent-reasoned note when `vet` is absent or the network is down; tool output is escaped untrusted data; findings fold into the `security` category (ADR-0006). First instance of the external-tool-findings substrate (the PRD's deferred `semgrep`/`ruff`/`clang-tidy` category).

All Focus Lens findings fold into the existing Risk Map categories and answers — no new sections, no new categories.

## Feedback loop (ADR-0003)

- One command enters a **blocking answer loop**: `lavish-axi poll` (no-timeout) returns queued feedback; agent answers and re-polls with `--agent-reply`.
- **Verified I/O contract** ([spike](./docs/spikes/lavish-poll-format.md), v0.1.31): `poll` writes **TOON** to stdout with `session.status` ∈ `feedback | waiting | ended | missing`; feedback carries `prompts[N]` of `{uid, prompt, selector, tag, text, target?}` where `tag` ∈ `message | annotation | choice | …`. **No TOON parser is written in the live loop** — the agent reads poll stdout directly as its own input (TOON is built for agent consumption, and the tool's `next_step` field states the next command). The one bounded exception is offline at close: the Q&A bake lifts the reviewer's questions from the stored poll TOON with a single-block `prompts[N]` extractor ([ADR-0007](./docs/adr/0007-bake-prompt-extractor.md)). `--agent-reply` both shows the prior answer in the browser *and* resumes blocking. Interrupt exits 130/143 with feedback preserved — this is the mechanism behind `Esc`/`/review-resume`.
- **Controls:** `Esc` (hard interrupt — queued feedback preserved) · `/review-resume` (re-attach to the file-path-keyed session, no regeneration) · `/review-close` (`lavish-axi end`). Optional `pause` sentinel (installer config).
- **Persistence:** `qa.jsonl` appended during the session; folded into `review.html` (+ optional `review.md`) once at close by the **Q&A bake** ([ADR-0007](./docs/adr/0007-bake-prompt-extractor.md)) — escaped through the Escape Boundary, idempotent via a `<!--brc:qa-log-->` seam, and re-CSP'd to strict so the saved cockpit is self-contained (opens in a plain browser, no Lavish). Mid-session answers render live in the Lavish chat — no per-answer HTML regeneration.
- **Resume + staleness:** `session.json` carries `{status, base, branch, head_sha, merge_base, started_at}`, written `open` when the review opens and marked `ended` at close. The **Session Evaluator** (a deep module of pure policy) compares it against the current git branch and the resolved `base...HEAD` diff identity (HEAD, base, and `merge-base(base, HEAD)` — so a base that was switched or has advanced under a fixed HEAD is caught), returning one of `none | fresh | stale | different-branch`; `/review-branch` checks first (step 0) and acts on the verdict — restore a `fresh` review without regenerating, **regenerate by default** on `stale` (HEAD advanced, base changed, or merge-base moved; resume-anyway available), generate on `none`/`different-branch`. v1.1: ambient detection via Lavish's `SessionStart` hook.

## On-disk layout

```
.review-agent/            (gitignored — generated)
  context.json  diff.patch  diff-stat.txt  changed-files.json  commits.txt
  resolved-config.json
  analysis.json  review.html  review.md  qa.jsonl  session.json
  assets/  cockpit.css  app.js
.lavish-axi/              (gitignored — Lavish session state)
.review-agent.yaml        (committed — repo policy)
```

## Configuration

Resolved by the **Config Resolver** (a pure-policy deep module + thin file-reading shell), which layers **command arg > repo `.review-agent.yaml` > machine `~/.review-agent/config.yaml` > defaults**. Absent files fall back to defaults; unknown keys and out-of-range values are located errors, never silent fallbacks. It ships a strict stdlib loader for the flat YAML subset the schema uses — **no third-party YAML dependency** ([ADR-0008](./docs/adr/0008-stdlib-config-loader.md)). Two non-overlapping scopes:

- **Repo policy** — `.review-agent.yaml` (committed): `base_branch`, `exclude` (**extends** built-ins; `exclude_reset: true` to replace), `focus`, `language_hints`, `styling`, `limits.{max_file_diff_lines, max_total_diff_lines}`. All optional.
- **Per-machine** — `~/.review-agent/config.yaml`: `pause` sentinel word, default `styling`, pinned Lavish version, SessionStart-hook on/off.

The collector writes the resolved policy to `.review-agent/resolved-config.json` so the agent threads `styling` (cockpit assets + lint), `focus`/`language_hints` (authoring lenses), and the machine settings into the later steps.

## Packaging & security

- Claude Code skill at `.claude/skills/branch-review-cockpit/` (`SKILL.md`, `scripts/collect_review_context.py`, `assets/cockpit.css`, `assets/app.js`, bundled lenses). Diff collector is **agent-agnostic** (git + stdlib).
- Distribution: `npx skills add` (agentskills.io format). **Pinned Lavish** via `npx -y lavish-axi@<pinned>`. Installer drops the skill, writes per-machine config, optionally installs the SessionStart hook, and gitignores both state dirs.
- **Loopback only** — never set `LAVISH_AXI_HOST` to a wildcard (exposes an unauthenticated local-file server). No MCP, no remote upload of repo code, browser feedback is **untrusted data** (logged, never executed, never used to build a shell command), no auto-apply of code, no auto-commit.

## v1 scope

**In:** the full pipeline above — `/review-branch [base]`, agent analysis + cockpit, escaping + CSP + lint, blocking loop with the three controls, `qa.jsonl` + bake-at-close, `session.json` + staleness offer, minimal `.review-agent.yaml`, C++/Python/TS Language Lenses, the Focus Lens Catalog (security/OWASP, regressions, simplification, supply-chain) with authoring-time + mid-review (Lens Pass) activation, installer.

**Deferred (roadmap, retained):** Mermaid rendering (vendored), `diff2html` side-by-side, Python Lavish fallback, further external-tool-findings lenses (`semgrep`/`ruff`/`clang-tidy` on the substrate the supply-chain lens establishes), additional-skills config, user-defined language hints, user-defined Focus Lenses, ambient SessionStart-hook resume.
