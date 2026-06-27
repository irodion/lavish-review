# Lavish-Based Branch Review — Agent Instructions

A local, AI-assisted skill that turns a Git branch diff into an interactive HTML Review Cockpit, opened through Lavish-AXI. See `DESIGN.md` for the implementation-ready design, `CONTEXT.md` for the domain glossary, and `docs/adr/` for load-bearing decisions.

## Workflow

`main` is protected: **never commit or push directly to `main`**. All changes land through a pull request — branch, push the branch, open a PR, and merge it (no review approval is required for a solo repo, but the PR is). This applies to humans and agents alike.

> Note: `CLAUDE.md` is a symlink to this file, so Claude Code and other agents read the same instructions.

## Agent skills

### Issue tracker

Issues and PRDs live as GitHub issues (via the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical triage vocabulary, defaults unchanged (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
