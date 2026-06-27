# External-tool findings enter the cockpit through an opt-in, offline-degrading lens — never the default path

Every cockpit section to date is **pure agent reasoning over the local diff**: the deterministic layer collects the diff (git + stdlib, no network), the agent analyzes it, and nothing else runs. The skill makes no network calls, uploads no repo code, and shells out to no third-party analyzer (DESIGN.md "Packaging & security"). That is a deliberate trust and privacy posture, not an accident.

The **supply-chain Focus Lens** breaks that posture on purpose. When the branch under review changes a dependency manifest (`package.json`/lockfiles, `go.mod`/`go.sum`, `requirements.txt`/`pyproject.toml`, `Cargo.toml`, `Gemfile`, …), the most valuable thing a reviewer can know is whether the *added or bumped* dependencies carry known vulnerabilities, malware, or license problems — a question agent reasoning over a diff cannot answer, because the answer lives in an external vulnerability database. We surface it by running [`vet`](https://github.com/safedep/vet) on the manifest change. `vet` **runs a binary** and **makes network calls**. This is the PRD's deferred "external-CLI-tool findings" category (the same shape would later cover `semgrep`/`ruff`/`clang-tidy`), so the decision is about the *category*, not just `vet`.

**Decision.** External-tool findings are admitted to the cockpit only through a Focus Lens, under these invariants:

- **Opt-in, never default.** The supply-chain lens runs only when the reviewer selects it (via `focus` config/CLI or mid-review in the loop) **and** the diff actually touches a dependency manifest. A default `/review-branch` makes no network call and runs no external binary — the existing posture is untouched.
- **Degrade gracefully, never block.** If `vet` is absent, the network is unavailable, or it errors or times out, the lens **says so plainly and continues** — it falls back to agent reasoning about the manifest change (what was added/bumped, what to verify by hand) and the rest of the review proceeds unaffected. A missing tool or a dead network is a degraded result, never a failed review.
- **Tool output is untrusted data.** `vet`'s output is escaped through the Escape Boundary ([ADR-0002](./0002-deterministic-escape-boundary.md)) exactly like diff bodies and reviewer feedback — it is rendered, never executed, and never used to build a shell command.
- **Scoped to the change.** The lens analyzes the **dependencies the diff added or changed**, not a full-tree audit. It reviews *this branch's* supply-chain delta, consistent with the cockpit reviewing a diff.
- **Findings fold into existing structure.** Supply-chain findings render as `security` Risk Map entries (with their challenge questions) and loop answers — clearly attributed to the external tool. No new cockpit section, no new risk category.

**Why a lens and not always-on.** Network access and a third-party binary are exactly the things a security- or privacy-conscious reviewer (the cockpit's core audience) needs to *choose*, not have imposed. Gating them behind an explicit, manifest-triggered, opt-in lens keeps the default review hermetic and the loopback-only/no-upload guarantees intact, while still making the capability one keystroke away when wanted.

## Consequences

- The supply-chain lens is the **first instance of the external-tool-findings substrate**: a contract for invoking an external analyzer, capturing its output through the Escape Boundary, attributing findings to their tool, and degrading when the tool/network is absent. Later analyzers (`semgrep`, `ruff`, `clang-tidy`) reuse this contract rather than each re-inventing it.
- DESIGN.md's "no network calls / no external analyzer" guarantee is refined: it holds for the **default** path and every pure-reasoning lens; external-tool lenses are the bounded, opt-in exception described here.
- Running an external binary that hits the network needs a per-machine and/or repo-policy switch (e.g. `supply_chain: { enabled, tool, timeout }`) so a team can disable it wholesale; the implementation issue defines the exact config surface.
- This lens does not change the "never auto-apply / never auto-commit" rule — it reports vulnerabilities, it does not bump or pin dependencies.
- Refines the security posture in DESIGN.md and complements [ADR-0005](./0005-design-critique-scope.md); together they delimit the two ways v1 Focus Lenses stretch the original model (critique scope, and external evidence).
