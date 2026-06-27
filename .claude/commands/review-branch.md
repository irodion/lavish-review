---
description: Open an interactive Review Cockpit for the current branch's diff (via Lavish).
argument-hint: "[base]"
---

Review the current Git branch using the **branch-review-cockpit** skill.

Base: $ARGUMENTS (empty means auto-detect — do not guess; pass it through only if
the user named one).

Follow the skill's steps exactly: run the collector, author `.review-agent/review.html`
from the pre-escaped fragment, and open it loopback-only with the pinned `lavish-axi`.
Never auto-apply code and never commit.
