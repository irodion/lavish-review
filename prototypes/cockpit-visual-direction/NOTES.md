# PROTOTYPE — Cockpit visual direction

**Throwaway.** Delete this directory once the verdict below is filled in and the
winning direction is folded into the real cockpit assets
(`.claude/skills/branch-review-cockpit/assets/`).

## Question

What should the improved Review Cockpit look like — sticky progress/filter
chrome, L0 stat tiles, risk matrix, hunk-anchored diffs with line numbers, and
polished disposition controls?

## Run

```sh
open prototypes/cockpit-visual-direction/prototype.html
```

Switch variants with the floating purple bar (or `←` / `→`). Disposition state
is in-memory and shared across variants — triage a few claims, then flip.

- **A — Instrument rail** (`#v=a`): dark, evolved document flow. Sticky verdict
  rail with the *claim tape* (one cell per claim, tinted by disposition,
  click-to-jump), filters, next-unreviewed (`N`). L0 = stat tiles + risk matrix.
- **B — Navigator workbench** (`#v=b`): light two-pane. Fixed left rail with
  progress ring, thread tree (per-claim dots), file list with stat bars.
  Doubles as the light-theme test.
- **C — Triage deck** (`#v=c`): near-black focus queue. One claim at a time,
  oversized `V`/`C`/`Q` disposition control (auto-advances), `J`/`K` to move,
  progress dot-rail. Evidence hunks render inline under the claim.

All variants share: real review data (feat/39-layered-skeleton — 4 threads,
12 claims, 10 files, 2 real hunks), hunk-anchored diffs with line-number
gutters, neutral kind marks + color reserved for judgment signals
(risk level / confidence / disposition — validated for contrast & CVD on both
surfaces with the dataviz palette validator).

- **D — Map + deck (C×B)** (`#v=d`): added after the first review round.
  B's persistent left map (thread tree with disposition dots, file stat bars)
  around C's one-claim-at-a-time stage, plus mock affordances for the two-channel
  chat idea (branch-scoped in the map, claim-scoped under the card).

## Verdict

**D wins** (round 1 ranking was C > B > A; D = C's stage × B's map, built and
confirmed in round 2). Why: the piece-by-piece flow matches how reviewers
actually read PRs, the persistent map answers the "hides the whole shape"
objection, and a claim-focused surface makes feedback-loop chat cheaper and
sharper — the current claim IS the context (questions anchor to a claim id +
its evidence refs instead of a DOM selector).

Preserved constraints from the debate: the baked `file://` artifact stays a
one-page document (deck is the *served* presentation only), full document one
toggle away, auto-advance goes to the next *unreviewed* claim (no
inbox-zero rubber-stamping).

Captured durably in: ADR-0014 (deck presentation mode), ADR-0015 (claim-scoped
questions), the "Deck Mode" PRD on the issue tracker, and CONTEXT.md terms
(Deck Mode, Map, Stage, Claim-scoped Question). This prototype stays as the
visual reference for the implementing agent; delete the directory once Deck
Mode ships.
