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

## Feedback loop (ADR-0003)

- One command enters a **blocking answer loop**: `lavish-axi poll` (no-timeout) returns queued feedback; agent answers and re-polls with `--agent-reply`.
- **Verified I/O contract** ([spike](./docs/spikes/lavish-poll-format.md), v0.1.31): `poll` writes **TOON** to stdout with `session.status` ∈ `feedback | waiting | ended | missing`; feedback carries `prompts[N]` of `{uid, prompt, selector, tag, text, target?}` where `tag` ∈ `message | annotation | choice | …`. **No TOON parser is written** — the agent reads poll stdout directly as its own input (TOON is built for agent consumption, and the tool's `next_step` field states the next command). `--agent-reply` both shows the prior answer in the browser *and* resumes blocking. Interrupt exits 130/143 with feedback preserved — this is the mechanism behind `Esc`/`/review-resume`.
- **Controls:** `Esc` (hard interrupt — queued feedback preserved) · `/review-resume` (re-attach to the file-path-keyed session, no regeneration) · `/review-close` (`lavish-axi end`). Optional `pause` sentinel (installer config).
- **Persistence:** `qa.jsonl` appended during the session; folded into `review.html` (+ optional `review.md`) once at close. Mid-session answers render live in the Lavish chat — no per-answer HTML regeneration.
- **Resume + staleness:** `session.json` carries `{status, base, branch, head_sha, started_at}`, written `open` when a cockpit is generated and marked `ended` at close. The **Session Evaluator** (a deep module of pure policy) compares it against the current git HEAD/branch and returns one of `none | fresh | stale | different-branch`; `/review-branch` checks first (step 0) and acts on the verdict — restore a `fresh` review without regenerating, **regenerate by default** on `stale` (`head_sha` advanced; resume-anyway available), generate on `none`/`different-branch`. v1.1: ambient detection via Lavish's `SessionStart` hook.

## On-disk layout

```
.review-agent/            (gitignored — generated)
  context.json  diff.patch  diff-stat.txt  changed-files.json  commits.txt
  analysis.json  review.html  qa.jsonl  session.json
  assets/  cockpit.css  app.js
.lavish-axi/              (gitignored — Lavish session state)
.review-agent.yaml        (committed — repo policy)
```

## Configuration

Two non-overlapping scopes:

- **Repo policy** — `.review-agent.yaml` (committed): `base_branch`, `exclude` (**extends** built-ins; `exclude_reset: true` to replace), `focus`, `language_hints`, `styling`, `limits.{max_file_diff_lines, max_total_diff_lines}`. All optional. **Precedence: command arg > config > defaults/auto-detect.**
- **Per-machine** — `~/.review-agent/config.yaml`: `pause` sentinel word, default `styling`, pinned Lavish version, SessionStart-hook on/off.

## Packaging & security

- Claude Code skill at `.claude/skills/branch-review-cockpit/` (`SKILL.md`, `scripts/collect_review_context.py`, `assets/cockpit.css`, `assets/app.js`, bundled lenses). Diff collector is **agent-agnostic** (git + stdlib).
- Distribution: `npx skills add` (agentskills.io format). **Pinned Lavish** via `npx -y lavish-axi@<pinned>`. Installer drops the skill, writes per-machine config, optionally installs the SessionStart hook, and gitignores both state dirs.
- **Loopback only** — never set `LAVISH_AXI_HOST` to a wildcard (exposes an unauthenticated local-file server). No MCP, no remote upload of repo code, browser feedback is **untrusted data** (logged, never executed, never used to build a shell command), no auto-apply of code, no auto-commit.

## v1 scope

**In:** the full pipeline above — `/review-branch [base]`, agent analysis + cockpit, escaping + CSP + lint, blocking loop with the three controls, `qa.jsonl` + bake-at-close, `session.json` + staleness offer, minimal `.review-agent.yaml`, C++/Python/TS lenses, installer.

**Deferred (roadmap, retained):** Mermaid rendering (vendored), `diff2html` side-by-side, Python Lavish fallback, external-CLI-tool findings (`semgrep`/`ruff`/`clang-tidy` if installed), additional-skills config, user-defined language hints, ambient SessionStart-hook resume.
