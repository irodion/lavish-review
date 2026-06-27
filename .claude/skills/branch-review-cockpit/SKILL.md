---
name: branch-review-cockpit
description: >-
  Turn the current Git branch's diff into an interactive HTML Review Cockpit and
  open it in the browser via Lavish-AXI for a human to audit. Use when the user
  asks to review a branch, review the diff, or run /review-branch. Hardened
  Escape Boundary + strict CSP + post-write lint (issue #4); no feedback loop or
  analysis sections yet.
---

# Branch Review Cockpit

Turn `merge-base(base, HEAD)...HEAD` into a minimal **Review Cockpit** — an HTML
artifact (title, one-line intent, the unified diff) opened in the browser through
[Lavish-AXI](https://www.npmjs.com/package/lavish-axi). This reduces a reviewer's
navigation cost; it does **not** make the review decision.

> **Scope.** The Escape Boundary, strict CSP, and post-write lint (issue #4) are
> in place: a hostile branch's `<script>` in a diff, path, or commit message
> cannot execute. Still deferred: the feedback loop (issue #5) and the analysis
> sections (issue #6). See `DESIGN.md`.

## Hard rules (always)

- **Never auto-apply code and never commit.** This skill only reads the diff and
  renders it; it changes no source and runs no git write commands.
- **Loopback only.** Open with the default Lavish host. Never set
  `LAVISH_AXI_HOST` to a wildcard — that exposes an unauthenticated local-file
  server.
- **The agent authors `review.html`** (ADR-0001) but injects untrusted data only
  through the pre-escaped fragments the collector produces (ADR-0002). Never
  hand-paste raw diff text, file paths, commit messages, or the branch name into
  the HTML — use `diff.fragment.html` and `fragments.html` verbatim. The
  post-write lint (step 5) is a tripwire, not your safety net: author it safe.
- **Strict CSP, no inline JS.** The cockpit ships a `Content-Security-Policy`
  meta with `script-src 'self'` (no `'unsafe-inline'`). All behavior stays in the
  vendored `assets/app.js` — never write an inline `<script>…</script>` block, an
  inline `on*=` handler, or a `javascript:` URI.

## Steps

### 1. Collect the deterministic context

Run the collector from the repo you want to review. Pass an explicit base only if
the user named one (precedence: command arg > config > auto-detect):

```sh
python3 .claude/skills/branch-review-cockpit/scripts/collect_review_context.py [base]
```

It auto-detects the Base (`origin/HEAD`, else `main`/`develop`/`master`), computes
the `base...HEAD` diff, and writes to `.review-agent/`:

- `context.json` — base, branch, head SHA, merge-base, changed-file count
- `diff.patch`, `diff-stat.txt`, `changed-files.json`, `commits.txt`
- `diff.fragment.html` — the diff pre-escaped into a safe `<pre class="diff">`
- `fragments.html` — pre-escaped, ready-to-inject building blocks (title, meta,
  changed-files list, commits) with every untrusted value already escaped
- `assets/cockpit.css`, `assets/app.js` — vendored, copied for relative reference

The escaped fragments carry invisible `<!--brc:untrusted-->…<!--/brc:untrusted-->`
markers. Paste them **verbatim** — do not strip the markers; the linter uses them.

If it exits asking for an explicit base (ambiguous repo), relay that to the user
and stop — do not guess.

### 2. Read the context

Read `.review-agent/context.json` (for non-sensitive metadata like the head SHA),
`.review-agent/fragments.html` (pre-escaped title/meta/files/commits), and
`.review-agent/diff.fragment.html` (the escaped diff). Skim `commits.txt` and
`diff-stat.txt` to write a single honest sentence of intent — what this branch
does. Do not over-claim; this build has no risk analysis.

### 3. Author `.review-agent/review.html`

Write a minimal, self-contained cockpit that renders when opened directly in a
plain browser (portable artifact). In `<head>`, include the strict CSP meta
exactly as below and reference assets by **relative path with no leading `/`**
(Lavish serves the HTML's own directory):

```html
<meta http-equiv="Content-Security-Policy"
      content="default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'">
<link rel="stylesheet" href="assets/cockpit.css">
```

and `<script src="assets/app.js"></script>` before `</body>`. Then:

- Use the **title** and **meta** blocks from `fragments.html` for the header and
  the metadata line (base, branch, head SHA, file count) — paste them verbatim.
  Never type the branch name or any path/commit text yourself.
- Add your one-line intent as prose you author (that text is trusted — it is
  yours, not the branch's).
- A "Diff" section whose body is the **verbatim contents of
  `diff.fragment.html`** — paste the pre-escaped `<pre class="diff">…</pre>`
  fragment as-is. Never reconstruct the diff from `diff.patch` by hand.

Keep it to title + intent + metadata + diff. No invented sections.

### 4. Lint the cockpit (post-write tripwire)

Before opening it, run the Cockpit Linter. It fails on unescaped `<`/`>` in an
untrusted region, inline JS, a remote `src`/`href` under vendored styling, or a
missing/weak CSP:

```sh
python3 .claude/skills/branch-review-cockpit/scripts/lint_cockpit.py .review-agent/review.html
```

If it exits non-zero, **fix the cockpit and re-lint** — never open a cockpit that
fails the lint. Do not silence it by stripping the untrusted markers.

### 5. Open it in the browser via Lavish

Open (or resume) the cockpit with the pinned Lavish version, loopback default:

```sh
npx -y lavish-axi@0.1.31 .review-agent/review.html
```

Tell the user it's open and summarize what they're looking at. This build stops
here — the blocking feedback loop arrives in issue #5.

## On-disk layout

```text
.review-agent/            (gitignored — generated)
  context.json  diff.patch  diff-stat.txt  changed-files.json  commits.txt
  diff.fragment.html  fragments.html  review.html
  assets/  cockpit.css  app.js
```
