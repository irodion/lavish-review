# Additive-optional fields stay within a schema tag; only breaking changes bump it

`analysis.json` carries a schema tag — `review-analysis/0.4` — and two mechanisms
key off it. The Analysis Schema Validator ([ADR-0016](./0016-guided-change-narration-surface.md))
accepts **only** the exact tag it encodes; there is no dual-schema path. And the
Session Evaluator ([ADR-0016](./0016-guided-change-narration-surface.md)'s clean
break, implemented in `session.py`) compares the tag an open session's analysis was
authored against with the tag this code speaks: a mismatch resolves to `stale-schema`
— **regenerate, with no resume-anyway**, because the loop and the bake can no longer
read that session's analysis. That is the harshest verdict the evaluator issues: it
throws away an in-progress review outright.

Adding the before/after contrast card (issue #107) is the first field added to the
schema since the 0.4 clean break, and it forces a call that will recur for every
future field: **bump the tag, or extend within it?** The contrast is purely
**additive-optional** — an optional field, absent from every analysis authored so far,
that older readers safely ignore and newer readers render only when present. Nothing
that reads a 0.4 analysis breaks when the field is absent (it always has been) or when
it is present (the reader is new). Bumping the tag to `0.5` for it would be strictly
destructive: every open 0.4 session would flip to `stale-schema` and be force-regenerated
mid-review, to gain a field that changes nothing about how the existing analysis reads.

**Decision.** A **purely additive-optional** change — a new optional field that older
readers safely ignore and newer readers render only when present — stays within the
**same** schema tag. No version bump, so no Session Evaluator clean break: sessions
authored before the field remain `fresh`/`stale` on the ordinary diff check, never
`stale-schema`. The contrast card lands this way: `review-analysis/0.4` is unchanged,
and the validator simply learns one more optional field.

A version bump is reserved for a **breaking** change — a field renamed, removed, or
re-typed, or a new *required* field, or a changed meaning — anything that makes a reader
of the old shape wrong about the new one (the 0.3→0.4 rename of `claims`→`steps` was
exactly this). A bump deliberately trips `stale-schema`, because in that case an
in-progress session genuinely cannot be read by the new code and *must* regenerate.

The test for "additive-optional" is a reader question, not an author preference: **can
code that predates the field still read every analysis correctly, with the field present
or absent?** If yes, it is additive-optional and stays in-tag. If a reader of the old
shape would be *wrong* about the new one, it is breaking and bumps.

## Consequences

- `review-analysis/0.4` gains an optional `contrast` (a `behavior-change`-only
  `{before, after}`) without a tag change. The validator accepts it as an optional
  field; the renderer and bake render it only when present.
- The Session Evaluator needs no change and gets none: because the tag is unchanged, a
  session authored before the contrast field still resolves through the ordinary diff
  check, and its `test_matching_schema_is_not_schema_stale` invariant now doubles as the
  regression guard that adding the field did not force a clean break. Its `stale-schema`
  path stays reserved for a real breaking bump.
- Already-baked cockpits are self-contained ([ADR-0013](./0013-self-contained-cross-platform-packaging.md))
  and untouched — an older baked record simply has no contrast cards.
- This rule is the standing policy for future fields, not a one-off for the contrast:
  reach for additive-optional first, and bump only when a reader of the old shape would
  be wrong about the new one.
