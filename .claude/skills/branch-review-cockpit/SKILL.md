---
name: branch-review-cockpit
description: >-
  Turn the current Git branch's diff into an interactive HTML Review Cockpit and
  open it in the browser via Lavish-AXI for a human to audit. Use when the user
  asks to review a branch, review the diff, or run /review-branch. Walking
  skeleton (issue #3): dev-only, no feedback loop, no analysis sections yet.
---

# Branch Review Cockpit (walking skeleton)

Turn `merge-base(base, HEAD)...HEAD` into a minimal **Review Cockpit** — an HTML
artifact (title, one-line intent, the unified diff) opened in the browser through
[Lavish-AXI](https://www.npmjs.com/package/lavish-axi). This reduces a reviewer's
navigation cost; it does **not** make the review decision.

> **Scope.** This is the dev-only skeleton that proves the pipeline wires
> together. It deliberately omits the hardened Escape Boundary, strict CSP, and
> post-write lint (issue #4), the feedback loop (issue #5), and the analysis
> sections (issue #6). **Do not review an untrusted branch with this build** —
> escaping here is basic, just sufficient to render. See `DESIGN.md`.

## Hard rules (always)

- **Never auto-apply code and never commit.** This skill only reads the diff and
  renders it; it changes no source and runs no git write commands.
- **Loopback only.** Open with the default Lavish host. Never set
  `LAVISH_AXI_HOST` to a wildcard — that exposes an unauthenticated local-file
  server.
- **The agent authors `review.html`** (ADR-0001) but injects untrusted data only
  through the pre-escaped fragment the collector produces (ADR-0002). Do not
  hand-paste raw diff text, file paths, or commit messages into the HTML.

## Steps

### 1. Collect the deterministic context

Run the collector from the repo you want to review. Pass an explicit base only if
the user named one (precedence: command arg > config > auto-detect):

```sh
python .claude/skills/branch-review-cockpit/scripts/collect_review_context.py [base]
```

It auto-detects the Base (`origin/HEAD`, else `main`/`develop`/`master`), computes
the `base...HEAD` diff, and writes to `.review-agent/`:

- `context.json` — base, branch, head SHA, merge-base, changed-file count
- `diff.patch`, `diff-stat.txt`, `changed-files.json`, `commits.txt`
- `diff.fragment.html` — the diff pre-escaped into a safe `<pre class="diff">`
- `assets/cockpit.css`, `assets/app.js` — vendored, copied for relative reference

If it exits asking for an explicit base (ambiguous repo), relay that to the user
and stop — do not guess.

### 2. Read the context

Read `.review-agent/context.json` (for the title/metadata) and
`.review-agent/diff.fragment.html` (the escaped diff). Skim `commits.txt` and
`diff-stat.txt` to write a single honest sentence of intent — what this branch
does. Do not over-claim; this skeleton has no risk analysis.

### 3. Author `.review-agent/review.html`

Write a minimal, self-contained cockpit that renders when opened directly in a
plain browser (portable artifact). Reference assets by **relative path with no
leading `/`** (Lavish serves the HTML's own directory):

- `<link rel="stylesheet" href="assets/cockpit.css">` in `<head>`
- `<script src="assets/app.js"></script>` before `</body>`
- A header with the branch name as title and your one-line intent
- A metadata line (base, branch, head SHA, file count) from `context.json`
- A "Diff" section whose body is the **verbatim contents of
  `diff.fragment.html`** — paste the pre-escaped `<pre class="diff">…</pre>`
  fragment as-is. Never reconstruct the diff from `diff.patch` by hand.

Keep it to title + intent + metadata + diff. No invented sections.

### 4. Open it in the browser via Lavish

Open (or resume) the cockpit with the pinned Lavish version, loopback default:

```sh
npx -y lavish-axi@0.1.31 .review-agent/review.html
```

Tell the user it's open and summarize what they're looking at. The skeleton stops
here — the blocking feedback loop arrives in issue #5.

## On-disk layout

```
.review-agent/            (gitignored — generated)
  context.json  diff.patch  diff-stat.txt  changed-files.json  commits.txt
  diff.fragment.html  review.html
  assets/  cockpit.css  app.js
```
