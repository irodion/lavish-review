---
description: End the open Review Cockpit session cleanly.
---

End the current Review Cockpit session using the **branch-review-cockpit** skill:

```sh
python3 .claude/skills/branch-review-cockpit/scripts/review_loop.py end
python3 .claude/skills/branch-review-cockpit/scripts/bake_review.py --md
python3 .claude/skills/branch-review-cockpit/scripts/lint_cockpit.py .review-agent/review.html --csp-mode strict --analysis .review-agent/analysis.json
python3 .claude/skills/branch-review-cockpit/scripts/session.py end
```

1. `review_loop.py end` calls `lavish-axi end` on the cockpit, closing the session in
   the browser.
2. `bake_review.py --md` folds `.review-agent/qa.jsonl` back into `review.html`
   (escaped via the Escape Boundary, idempotent) and swaps the cockpit to the strict
   CSP, so the saved file is **self-contained** — it opens in a plain browser with no
   Lavish running. `--md` also writes `.review-agent/review.md` (review + Q&A) for
   pasting into a PR. Drop `--md` to skip the Markdown export.
3. `lint_cockpit.py … --csp-mode strict --analysis …` is the post-bake tripwire: it
   confirms the baked artifact is escaped and self-contained (no inline JS, no
   remote/CDN, strict CSP) and, via `--analysis`, that its claims, seams, and evidence
   anchors still match the analysis. If it fails, the bake must be fixed before the
   cockpit is shared.
4. `session.py end` marks `session.json` ended so a later `/review-branch` treats it as
   a finished review (it won't offer to restore a closed one).

Then leave the answer loop and tell the user the review is closed; the baked
`review.html` (and `review.md`, if written) now hold the full Q&A. Never auto-apply
code and never commit.
