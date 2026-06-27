# Lavish-Based Branch Review — Agent Instructions

A local, AI-assisted skill that turns a Git branch diff into an interactive HTML Review Cockpit, opened through Lavish-AXI. See `DESIGN.md` for the implementation-ready design, `CONTEXT.md` for the domain glossary, and `docs/adr/` for load-bearing decisions.

## Agent skills

### Issue tracker

Issues and PRDs live as GitHub issues (via the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical triage vocabulary, defaults unchanged (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
