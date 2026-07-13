# Branch Review Cockpit

A local, AI-assisted agent skill that turns a Git branch diff into an interactive
HTML **Review Cockpit**, opened and driven through
[Lavish-AXI](https://www.npmjs.com/package/lavish-axi), to help a human reviewer
audit AI- or human-generated changes faster. It reduces review navigation cost;
it does **not** automate the review decision — the agent states per-step
confidence, never a verdict.

The cockpit is layered: **L0** shows what the branch is *for* (goal alignment),
**L1** decomposes the change into narrative threads, **L2** walks the guided
**Review Steps** — each a stop with its Behavior Impact, the narrator's confidence,
why it sits where it does, and the comparisons to make — and **L3** holds the
evidence — the diff itself, demoted to leaf level. The analysis is formed
**blind**, in an isolated context that never sees the conversation that wrote the
branch. You descend at your own pace, set per-step dispositions
(`looks-right | concern | follow-up | skipped`), ask questions in the page, and
close with a self-contained `review.html` + pasteable `review.md` that record
*your* review.

See [DESIGN.md](./DESIGN.md) for the design, [CONTEXT.md](./CONTEXT.md) for the glossary,
and [docs/adr/](./docs/adr/) for the load-bearing decisions.

## Requirements

- Python 3.11+
- git
- Node.js (`npx` — runs the pinned Lavish-AXI and the `skills` installer)

## Install

The skill ships in the [agentskills.io](https://agentskills.io) format and works on
**Claude Code**, **Cursor**, and **OpenAI Codex**. One command, run in the repo you
want to review in:

```sh
npx -y skills add irodion/lavish-review -a claude-code   # and/or: -a cursor -a codex
```

Run it without `-a` in a terminal to pick agents from a menu instead. The skill is
copied into each selected agent's skills directory:

| Platform | Skill lands in | You invoke it with |
|---|---|---|
| Claude Code | `.claude/skills/branch-review-cockpit/` | `/review-branch [base] [--goal …]` |
| Cursor | `.cursor/skills/branch-review-cockpit/` | `/review-branch` (command file) or by asking for a branch review |
| Codex | `.agents/skills/branch-review-cockpit/` | `$branch-review-cockpit` or just ask ("review this branch") |

> **If an AI agent runs the install for you:** it must pass `-y` and an explicit
> `-a`. Without a terminal the CLI shows no agent picker and silently falls back
> to a "universal" install under `.agents/skills/` — which Cursor and Codex read
> but **Claude Code does not** — so the command reports success while the skill
> stays invisible to Claude Code.

The CLI also writes `skills-lock.json` recording what it installed — commit it if
you want the install reproducible for teammates.

Then run the one-time, idempotent first-run setup from wherever the skill landed:

```sh
python3 <skill-dir>/scripts/install.py
```

`install.py` ([ADR-0013](./docs/adr/0013-self-contained-cross-platform-packaging.md)):

- creates `~/.review-agent/config.yaml` with the **pinned Lavish version** (an
  existing config is never touched),
- adds `.review-agent/` and `.lavish-axi/` to your `.gitignore`,
- writes the per-platform entry points — `/review-*` command files for Claude Code
  and Cursor, plus the `review-analyst` agent definition (the isolated-analyst
  boundary) for Claude Code. Codex needs no files.

Useful flags: `--platforms claude,cursor,codex` (skip auto-detection), `--dry-run`
(print the plan), `--force` (replace locally edited entry points),
`--sessionstart-hook` (record the ambient-resume preference).

On platforms without an isolated-subagent mechanism, the analysis runs in the
invoking context and the renderer records that in the cockpit's L0 — the independence
premise degrades visibly, never silently.

## Using it

- **`/review-branch [base] [--goal <issue-ref|file|text>]`** — collect the diff and
  goal evidence, run the blind analysis, open the cockpit, and enter the feedback
  loop. Ask questions or annotate lines in the page; answers arrive in the page's
  chat.
- **`Esc`** interrupts the loop (queued feedback is preserved); **`/review-resume`**
  re-attaches; **`/review-close`** bakes the outcome + Q&A into a self-contained
  `review.html` and (optionally) `review.md` for pasting into a PR as *your* review.

Review policy can travel with the repo in a committed `.review-agent.yaml` (base
branch, excludes, focus, limits) and per-machine in `~/.review-agent/config.yaml` —
see DESIGN.md's Configuration section.

## Security posture

Everything untrusted — narrator prose, diff bodies, file paths, commit messages,
goal text, browser feedback — crosses a deterministic Escape Boundary. A deterministic
renderer builds and lints the cockpit under a bounded CSP before atomically writing it.
Loopback only; no remote upload of repo code; browser feedback is answered and logged,
never executed. The tool never applies code, never commits, and never prints a merge
recommendation.

## Development

```sh
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy && pytest
```

CI runs the same four gates on every pull request. `src/branch_review/` is the
single source of truth; the skill ships a vendored copy at
`.claude/skills/branch-review-cockpit/lib/` kept byte-identical by
`tools/sync_vendored.py` — `tests/test_packaging.py` fails on any drift.

`main` is protected; all changes land through a pull request.

## License

[MIT](./LICENSE). Installed skill copies bundle the same LICENSE file, so the
terms travel with the code that `npx skills add` drops into your repo.
