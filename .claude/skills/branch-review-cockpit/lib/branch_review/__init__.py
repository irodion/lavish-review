"""Branch Review Cockpit — deterministic core.

The pure, testable Python modules that back the Lavish-based branch-review skill
(Config Resolver, Base Resolver, Change Classifier, Diff Collector, Escape Boundary,
Session Evaluator, Q&A Log + Bake, Cockpit Linter, Analysis Schema Validator).

See ``DESIGN.md`` and ``CONTEXT.md`` at the repo root. This module is greenfield;
the concrete modules land slice by slice via the issue tracker.
"""

__all__ = ["__version__"]

__version__ = "0.0.0"
