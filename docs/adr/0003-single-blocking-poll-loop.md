# One command drives a blocking long-poll loop, not separate poll/answer commands

`/review-branch` generates the cockpit, opens it, and then enters a blocking answer loop driven by `lavish-axi poll` (no-timeout long-poll) and `lavish-axi poll --agent-reply "..."`. The handoff doc proposed three user-invoked commands (`/review-poll`, `/review-answer`, `/review-close`) as if polling were a discrete manual action. It isn't: the real `poll` blocks until the user sends feedback or ends the session, and `--agent-reply` shows the answer and immediately resumes blocking — so poll and answer are one continuous loop the agent drives, not steps the user triggers.

**Why:** this matches how Lavish is designed to be driven — the agent "sits in" the review answering questions until the user ends the session in the browser. The reviewer never has to remember to trigger polling; they just talk in the browser and answers appear.

## Consequences

- While in the loop, the Claude Code turn is **occupied** — the user can't ask unrelated things until they break out. Mitigated by three controls: `Esc` (hard interrupt; Lavish preserves queued feedback), `/review-resume` (re-attach to the file-path-keyed session, no regeneration), `/review-close` (`lavish-axi end`). An optional `pause` sentinel can soft-break from the browser.
- Sessions are keyed by the canonical HTML file path, which makes resume cheap and idempotent.
