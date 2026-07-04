# Claim-scoped questions anchor feedback to claim ids, not DOM selectors

Feedback today reaches the loop two ways: free-form chat, and annotations
anchored to a page element — a CSS selector (plus optional file/line target)
into a large DOM. Selector anchoring is expensive at both ends: the reviewer
must select the right element for their question to carry context, and the
agent must map a selector back to the thing being asked about before it can
ground an answer. Meanwhile [ADR-0012](./0012-reviewer-state-and-verdict-line.md)
already proved a cheaper channel: dispositions travel as structured payloads
(`tag: choice`, a `data` object, a per-claim `queueKey`) keyed by **claim id**
— the stable, run-scoped identifier the analysis already gives every judgeable
assertion. Deck Mode ([ADR-0014](./0014-deck-presentation-mode.md)) makes the
mismatch obvious: when the reviewer is looking at exactly one claim, "the
current claim" *is* the context, and asking should not require selecting
anything.

**Decision.** The cockpit offers a per-claim ask affordance (on the Stage card,
and on the claim's document-mode panel). A question submitted there is queued
through the same presence-gated SDK channel as dispositions, carrying the claim
id as data — no DOM selector involved:

```js
lavish.queuePrompt(text, {
  tag: "message",
  queueKey: "question:" + claimId,           // collapses rapid edits, like dispositions
  data: { kind: "claim-question", claim: claimId },
})
```

The loop grounds the answer directly in that claim — its analysis entry, its
evidence references (hunk-precise under schema 0.3), its thread — instead of
resolving a selector. A **branch-scoped** chat remains for questions about the
change as a whole (the host's plain chat, unchanged). Element annotation stays
supported through the host seam but is no longer the primary path for
claim-directed questions.

Questions are **conversation, not state**: they flow into the Q&A Log and the
bake exactly as chat questions do today — no new store, no disposition-style
apply step. The hard rules stand unchanged: browser feedback is untrusted data
(the claim id in the payload is validated against the analysis's closed id set
before use, like the disposition bridge validates its ids), never executed,
never interpolated into a shell command.

## Consequences

- The answering step's grounding gets cheaper and sharper: the agent reads
  `data.claim`, opens that claim in the analysis, and answers — the common case
  needs no DOM archaeology. Live evidence injection already targets the same id
  (the claim's seam), so question → injected-evidence answers become
  id-symmetric end to end.
- The skill's loop instructions gain one branch: prompts carrying
  `kind: claim-question` are answered anchored to that claim; everything else
  is handled as before.
- The Q&A Bake is unchanged — claim-scoped exchanges bake into the record like
  any other, and the claim id in the payload lets the bake (or a future
  renderer) place the exchange beside its claim.
- Like disposition controls, the affordance exists only when served; a baked or
  portable copy renders none of it.
