# The cockpit's scope expands from change-audit to advisory design-critique — for one bounded lens

The Review Cockpit's founding purpose is **change-audit**: given `merge-base(base, HEAD)...HEAD`, does this diff do what it claims, safely? Every section answers a question *about the change as written* — what it does (Executive Summary), where to look (Review Route), what observably moves (Behavior Changes), where the danger is (Risk Map), what it forgot (Suspicious Omissions). None of them ask *should this change have been written this way at all?*

The **simplification Focus Lens** ("what are our choices? can we do this simpler?") asks exactly that. It is **design critique**, not change-audit: it evaluates the *approach*, proposes alternatives, and may conclude the cleanest fix is one the diff didn't take. That is a different question from "is this diff correct," and answering it expands what the cockpit is *for*.

**Decision.** We support design-critique, but **only through the simplification Focus Lens**, and only as **advisory** output. The cockpit's default frame stays change-audit; design-critique is an opt-in perspective the reviewer chooses (via `focus` config/CLI or mid-review through the feedback loop — see [ADR-0003](./0003-single-blocking-poll-loop.md)).

**Why allow it.** "Can this be simpler?" is one of the highest-value questions a reviewer asks, and the cockpit already holds the context to answer it well (the diff, the widened surrounding code, the intent read). Refusing to answer it would send the reviewer back to a separate conversation for the question they most want help with. The expansion is bounded to one named lens, so the default audit framing is never diluted.

**Why keep it advisory and bounded.** The skill's hard rule is that it **never modifies code and never makes the review decision** (it is a review *aid*). Design-critique amplifies the temptation to cross that line — a proposed "simpler" rewrite is a hair's breadth from auto-applying it. So:

- The simplification lens **proposes, never patches.** It surfaces alternatives and trade-offs as prose the reviewer weighs; it issues no edits, writes no code to disk, and runs no git write. This is the same advisory posture as every other section.
- Design-critique findings are **clearly distinguished from defects.** A simpler-alternative suggestion is not a bug — it folds into the Risk Map's `maintainability` framing and the feedback-loop answers, explicitly labeled as an *advisory alternative*, so a reviewer never mistakes "here's another way" for "this is wrong."
- It introduces **no new cockpit section and no new risk category** (consistent with the Lens principle: lenses sharpen the neutral analysis, they are not separate machinery). If experience shows critique needs its own surface, that is a later, separate decision.

## Consequences

- CONTEXT.md's **Focus Lens** definition is broadened: a Focus Lens may reframe the analysis toward *critique of the approach*, not only audit of the change. The cockpit's stated purpose (DESIGN.md, CONTEXT.md, SKILL.md) gains an explicit, bounded "advisory design-critique" clause.
- The simplification lens is built as a normal Focus Lens (bundled definition, `focus`-selected, re-invokable in the loop). Its output lands in existing structure — `maintainability` Risk Map entries framed as options, and loop answers — never a new section.
- The "never modifies code / never decides" hard rule is **unchanged and load-bearing** here: it is what keeps critique advisory. The simplification lens is the place that rule is most likely to be tested, so its bundled definition restates it.
- This decision answers the scope question raised in the Focus Lens design tracker (#15). The other v1 Focus Lenses (security/OWASP, regressions) stay within change-audit and are unaffected.
