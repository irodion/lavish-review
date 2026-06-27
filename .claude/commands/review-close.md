---
description: End the open Review Cockpit session cleanly.
---

End the current Review Cockpit session using the **branch-review-cockpit** skill:

```sh
python3 .claude/skills/branch-review-cockpit/scripts/review_loop.py end
```

This calls `lavish-axi end` on the cockpit, which closes the session in the browser.
Then leave the answer loop and tell the user the review is closed; the Q&A transcript
remains in `.review-agent/qa.jsonl`. (Folding it back into `review.html` at close is
issue #9 — not yet wired.) Never auto-apply code and never commit.
