# The Config Resolver ships a strict stdlib YAML-subset loader, not a third-party YAML dependency

The Config Resolver (issue #10) makes review policy travel with the repo by reading two YAML files — the committed `.review-agent.yaml` and the per-machine `~/.review-agent/config.yaml` — and merging them with the built-in defaults (`arg > repo > machine > defaults`). Reading YAML normally means depending on PyYAML. This skill does not.

**Decision.** The Config Resolver parses the **flat YAML subset the config schema actually uses** with a small, strict, stdlib-only loader (`branch_review.config.parse_yaml`), rather than adding a runtime dependency. The supported subset is exactly: `key: value` mappings, one level of nesting via an indented block (`limits:`), block sequences (`- item`) and inline flow sequences (`[a, b]`), `# comments`, and typed scalars (quoted strings, `true`/`false`, `null`/`~`, ints, `d.d` floats). Everything else — a tab in indentation, a flow mapping, an unterminated sequence, an unknown key — is a **located `ConfigError`**, never a silent mis-parse or a quiet fallback.

**Why not depend on PyYAML.**

- **The diff collector is agent-agnostic — git + stdlib only** (DESIGN "Packaging & security"). The skill is distributed via `npx skills add` and run as `python3 <script>` with no install step (the entry shim puts `src/` on `sys.path` itself). A third-party import would either crash a fresh checkout with `ModuleNotFoundError` or force an install step the skill deliberately avoids. Zero dependencies is a load-bearing property here, not an aesthetic one.
- **The schema is small and closed.** The config is a shallow, fixed set of keys with scalar, one-level-mapping, and list values. A full YAML engine (anchors, multi-document streams, merge keys, custom tags, block scalars) is far more surface than the schema needs — and more attack surface for a file a repo commits.
- **Strictness is a feature.** Following the same discipline as `validate_analysis` and the Change Classifier, the loader rejects the unrecognized rather than guessing. A misspelled `base_brnach` fails loudly instead of silently reverting to auto-detect; `styling: fancy` is a clean error, not a cockpit that renders wrong. PyYAML's permissive coercions (e.g. `0.1.31` is fine as a string here; a bare `no` would *not* silently become `False` because the schema's booleans are validated by key) would blur those edges.

**Consequences.**

- The loader is **not** a general YAML parser and must not be reused as one. Its remit is the config subset; a value shape the schema doesn't use is out of scope by design, and its docstring says so.
- The purity split mirrors the other deep modules: `resolve(arg, repo, machine)` is pure over already-parsed dicts (table-tested for precedence and validation), and a thin I/O shell reads the files. The loader and the merge are tested independently.
- If the config schema ever grows a construct outside the subset (a nested list-of-mappings, say), the choice is revisited explicitly — extend the loader for that one construct, or reconsider a dependency — rather than silently accepting whatever a general parser would allow.
- Every value read from either file is still treated as data: strings are validated against closed sets (`styling ∈ {vendored, cdn}`), ints must be positive, and unknown keys are rejected — the loader parses, the resolver validates.
