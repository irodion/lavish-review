# Branch Review Cockpit

A local, AI-assisted Claude Code skill that turns a Git branch diff into an interactive
HTML **Review Cockpit**, opened and driven through
[Lavish-AXI](https://www.npmjs.com/package/lavish-axi). It reduces review navigation cost;
it does **not** automate the review decision.

See [DESIGN.md](./DESIGN.md) for the design, [CONTEXT.md](./CONTEXT.md) for the glossary,
and [docs/adr/](./docs/adr/) for the load-bearing decisions.

## Development

```sh
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy && pytest
```

CI runs the same four gates on every pull request.

> Status: greenfield — the deterministic core is built module by module via the issue
> tracker. `main` is protected; all changes land through a pull request.
