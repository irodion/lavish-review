# Lavish-Based Branch Review

A local, AI-assisted skill that turns a Git branch diff into an interactive HTML **Review Cockpit**, opened through Lavish-AXI, to help a human reviewer audit AI- or human-generated changes faster. It reduces review navigation cost; it does not automate the review decision.

## Language

**Review Cockpit**:
The interactive HTML artifact a review produces — what the reviewer reads and questions in the browser. Authored directly by the agent (not rendered from a fixed template), opened and watched by Lavish-AXI.
_Avoid_: report, dashboard, page.

**Base**:
The branch the changes are compared against (e.g. `main`, `develop`). The diff is `merge-base(base, HEAD)...HEAD`.
_Avoid_: target, trunk, parent.

**Branch Under Review**:
The current branch whose changes are being audited — the `HEAD` side of the diff.
_Avoid_: feature branch, source.

**Review Route**:
The ordered path the cockpit recommends a reviewer follow through the changes ("start here, then these files, then verify tests"). A first-class section, not just a file list.
_Avoid_: walkthrough order, reading order.

**Risk Map**:
The set of changes grouped by risk category (correctness, compatibility, concurrency, security, performance, maintainability, test coverage), each with a level, a reason, and challenge questions.
_Avoid_: risk list, findings.

**Suspicious Omission**:
Something the diff did *not* change but arguably should have — untouched tests, callers, docs, config, or error handling adjacent to a behavioral change.
_Avoid_: gap, missing change.

**Analysis** (`analysis.json`):
The agent's structured intermediate reasoning about the diff (intent, behavior changes, review route, risk map, omissions, test checklist, diagrams). It is the substrate the Review Cockpit is authored from and the substrate the feedback loop answers from.
_Avoid_: report, summary.

**Lens**:
An optional, language-specific risk checklist the agent consults while authoring the Risk Map (e.g. the C++ lens covers ownership, lifetime, threading, ABI). Lenses sharpen a neutral-by-default analysis; they are not separate machinery.
_Avoid_: profile, ruleset, plugin.

**Session**:
A live Lavish-AXI editing/feedback connection, keyed by the canonical path of the Review Cockpit HTML file. There are no opaque session IDs — the file path *is* the identity.
_Avoid_: connection, tab.
