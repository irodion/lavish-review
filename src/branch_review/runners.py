"""Read-only test-runner detection for the Test Checklist (issue #6).

The Test Checklist *suggests* how a reviewer would run the tests; it **never runs
them**. That boundary is the whole point of this module and it is mechanical: every
function here only ever *reads* files (existence checks, and parsing a config file's
text) — there is no :mod:`subprocess`, no ``os.system``, nothing that could execute
a test command. The returned :class:`Runner` carries a *suggested* ``command``
string that the agent places in the checklist verbatim; turning that string into a
process is out of scope here and forbidden by the skill (DESIGN: "checklist +
read-only runner detection, no execution").

Detection is best-effort and conservative: it matches well-known marker files and
returns the **first** ecosystem it recognises (most projects have one test runner;
the agent can always refine in prose). A repo with no recognisable runner yields
``None`` — the checklist then simply omits a concrete command rather than guessing.
"""

from __future__ import annotations

import argparse
import json
import tomllib
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

__all__ = ["Runner", "detect_runner"]


@dataclass(frozen=True)
class Runner:
    """A detected test runner: how the reviewer would *run* tests, not a thing we run.

    ``command`` is a suggestion rendered into the Test Checklist; it is never
    executed by this skill. ``evidence`` is the repo-relative marker file that
    justifies the guess, so the cockpit can show *why* this runner was suggested.
    """

    name: str
    command: str
    evidence: str


def _read_text(path: Path) -> str:
    """File text, or ``""`` if unreadable — detection never raises on a bad file."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _load_toml(path: Path) -> dict[str, object]:
    """Parsed TOML, or ``{}`` if missing/malformed (best-effort, never raises)."""
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _load_json(path: Path) -> dict[str, object]:
    """Parsed JSON object, or ``{}`` if missing/malformed (best-effort)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _detect_python(root: Path) -> Runner | None:
    """pytest when its config markers are present (Python-specific evidence only).

    A bare ``tests``/``test`` directory is *not* enough here: it is ecosystem-agnostic
    (a JS, Go, or Rust repo can have one too), so treating it as Python would shadow the
    Node/Go/Rust detectors that run after this one. That weak signal is handled last by
    :func:`_detect_unittest_fallback`.
    """
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        data = _load_toml(pyproject)
        tool = data.get("tool")
        if isinstance(tool, dict) and "pytest" in tool:
            return Runner("pytest", "pytest", "pyproject.toml")

    for name in ("pytest.ini", "tox.ini", "setup.cfg"):
        marker = root / name
        if marker.is_file() and "pytest" in _read_text(marker):
            return Runner("pytest", "pytest", name)

    # A top-level conftest.py, or a nested one under the usual test roots. Bounded to
    # tests/ and test/ on purpose: a recursive ``root.glob("**/conftest.py")`` would
    # walk the *entire* repo (node_modules, .venv, vendored trees) on every review.
    nested_conftest = any(
        any((root / d).glob("**/conftest.py")) for d in ("tests", "test") if (root / d).is_dir()
    )
    if (root / "conftest.py").is_file() or nested_conftest:
        return Runner("pytest", "pytest", "conftest.py")

    return None


def _detect_node(root: Path) -> Runner | None:
    """jest/vitest by config or devDeps; otherwise an existing ``test`` npm script."""
    package = root / "package.json"
    if not package.is_file():
        return None
    data = _load_json(package)
    deps = {**_dep_section(data, "devDependencies"), **_dep_section(data, "dependencies")}

    for tool, cmd in (("vitest", "npx vitest run"), ("jest", "npx jest")):
        if any((root / f"{tool}.config.{ext}").is_file() for ext in ("js", "ts", "mjs", "cjs")):
            return Runner(tool, cmd, f"{tool}.config.*")
        if tool in deps:
            return Runner(tool, cmd, "package.json")

    scripts = data.get("scripts")
    if isinstance(scripts, dict) and isinstance(scripts.get("test"), str):
        return Runner("npm", "npm test", "package.json")
    return None


def _dep_section(data: dict[str, object], key: str) -> dict[str, object]:
    section = data.get(key)
    return section if isinstance(section, dict) else {}


def _detect_go(root: Path) -> Runner | None:
    return Runner("go", "go test ./...", "go.mod") if (root / "go.mod").is_file() else None


def _detect_rust(root: Path) -> Runner | None:
    return Runner("cargo", "cargo test", "Cargo.toml") if (root / "Cargo.toml").is_file() else None


def _detect_make(root: Path) -> Runner | None:
    """A ``make test`` fallback only if the Makefile actually declares that target."""
    candidates = (root / name for name in ("Makefile", "makefile") if (root / name).is_file())
    makefile = next(candidates, None)
    if makefile is None:
        return None
    name = makefile.name
    for line in _read_text(makefile).splitlines():
        if line.startswith("test:") or line.startswith("test :"):
            return Runner("make", "make test", name)
    return None


def _detect_unittest_fallback(root: Path) -> Runner | None:
    """Last-resort ``unittest`` for a bare ``tests``/``test`` dir with no stronger signal.

    A top-level test directory is ecosystem-agnostic, so this runs **after** every
    ecosystem-specific detector (Python config, Node, Go, Rust, Make) has declined — a JS
    repo with ``package.json`` + ``tests/`` is caught by :func:`_detect_node` first. By the
    time we get here, a ``tests/`` dir most plausibly means a stdlib-``unittest`` project.
    """
    if (root / "tests").is_dir() or (root / "test").is_dir():
        return Runner("unittest", "python -m unittest discover", "tests/")
    return None


# Ordered most-specific ecosystem first; the first detector to match wins. The bare
# ``tests/``-dir unittest guess is deliberately LAST — it is ecosystem-agnostic evidence
# and must not shadow a Node/Go/Rust/Make repo that merely also has a test directory.
_DETECTORS: tuple[Callable[[Path], Runner | None], ...] = (
    _detect_python,
    _detect_node,
    _detect_go,
    _detect_rust,
    _detect_make,
    _detect_unittest_fallback,
)


def detect_runner(root: Path) -> Runner | None:
    """Best-effort, **read-only** guess at the repo's test runner (``None`` if unknown).

    Walks the ordered detectors and returns the first match. Reads marker/config
    files only — it never executes a test command, and never raises on a malformed
    config (an unreadable marker just doesn't match).
    """
    for detector in _DETECTORS:
        runner = detector(root)
        if runner is not None:
            return runner
    return None


def main(argv: list[str] | None = None) -> int:
    """CLI: print the detected runner as JSON (or ``null``) for the Test Checklist.

    Read-only by construction — it only inspects marker files. The agent reads this
    JSON and drops ``command`` into ``test_checklist`` *as a suggestion*; the skill
    never executes it. Always exits 0: "no runner found" is a valid result, not an
    error.
    """
    parser = argparse.ArgumentParser(
        prog="detect_test_runner",
        description="Detect the repo's test runner (read-only; never runs tests).",
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repo path (default: cwd).")
    args = parser.parse_args(argv)

    runner = detect_runner(args.repo)
    print(json.dumps(asdict(runner) if runner is not None else None, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
