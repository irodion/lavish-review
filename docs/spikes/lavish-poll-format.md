# Spike: `lavish-axi poll` I/O contract (verified against v0.1.31)

Verified by reading `dist/cli.mjs` and running the pinned CLI live (open headless → inject prompts via `POST /api/:key/prompts` → poll → stop).

## Wire format = TOON (not JSON)

`poll` writes **TOON** (YAML-like: `key:` with 2-space nesting, arrays as `name[N]:` + `- ` items, strings quoted only when they contain special chars; `\n` is escaped inside quoted strings) to **stdout**. stderr carries a wait banner + heartbeats during a long poll.

## The four statuses the loop must branch on

| `session.status` | Meaning | What the loop does |
|---|---|---|
| `feedback` | prompts arrived | answer, then re-poll with `--agent-reply` |
| `waiting` | optional timeout elapsed, nothing queued | re-poll (no `--timeout-ms`) |
| `ended` | user ended the session | exit the loop, bake-at-close |
| `missing` | no session for this file | `AxiError NOT_FOUND` → open first |

## Verified `feedback` payload shape

```
session:
  file: <canonical path>          # session identity IS this path
  status: feedback
dom_snapshot: ""                   # optional DOM snapshot if user copied one
prompts[2]:
  - uid: m1
    prompt: Why is the retry loop flagged high-risk?   # the actionable instruction
    selector: ""
    tag: message                   # free-form chat message
    text: ""
  - uid: a1
    prompt: "This cancellation path looks wrong\n\nContext data:\n{...}"
    selector: "#L42"
    tag: annotation                # element/text annotation
    text: if (cancelled) return;   # the selected text / short label
    target:                        # present only for targeted annotations
      file: src/release_runner.cpp
      line: 42
next_step: "Apply the requested changes ... run `lavish-axi poll <file> --agent-reply \"...\"` ..."
```

Per-prompt fields (from `normalizePrompt`): `uid`, `prompt`, `selector`, `tag`, `text`, optional `target`. `tag` dispatches the kind — observed `message`, `annotation`; the `input` playbook also produces `choice` and custom tags. Browser-side `queuePrompt(options.data)` is folded into `prompt` as a trailing `Context data:\n<json>` block.

## Design payoff: no TOON parser needed

TOON is already optimized for agent consumption, and `next_step` literally tells the agent the next command to run. So the answer loop is **not** "script parses TOON → struct → agent." It is:

```
agent runs `lavish-axi poll <file>`   (blocking bash; reads TOON stdout as its own input)
  → reads prompts, consults diff/repo, writes answer
  → agent runs `lavish-axi poll <file> --agent-reply "<answer>"`   (shows answer, blocks for next)
repeat until status: ended  (or Esc)
```

We write **no parser** in the loop. The agent reads the poll stdout directly. `--agent-reply` both displays the prior answer in the browser chat *and* resumes blocking — one call does both.

> Refinement (issue #9): the Q&A bake at close needs the reviewer's questions out of the stored poll TOON to fold them into `review.html`/`review.md`. That is the one bounded exception — an offline, single-block `prompts[N]` extractor scoped to the table documented above, never used in the live loop. See [ADR-0007](../adr/0007-bake-prompt-extractor.md).

## Operational notes

- `--agent-reply` POSTs to `/api/:key/agent-reply` *before* re-polling, so a single command answers + waits.
- Interrupt (SIGINT/SIGTERM) → exits 130/143, prints an interrupt banner to stderr; **queued feedback is never lost** — re-running `poll` resumes. This is the mechanism behind `Esc` + `/review-resume`.
- Long poll with no `--timeout-ms` blocks indefinitely (heartbeat spaces keep the HTTP connection alive). `--timeout-ms` is a test-only escape hatch.
- Server is detached, shared across sessions on a default port; self-shuts after `LAVISH_AXI_IDLE_TIMEOUT_MS` (default 30 min) idle. `lavish-axi stop` kills it.
