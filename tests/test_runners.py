"""Tests for read-only test-runner detection (issue #6).

Two contracts: the detector recognises common ecosystems from their marker files,
and it is **read-only** — it never executes anything. The first is table-driven;
the second is asserted structurally (the module imports no process-spawning API).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from branch_review import runners
from branch_review.runners import Runner, detect_runner


def _detected(root: Path) -> Runner:
    """detect_runner that asserts a hit — keeps the runner-present tests terse."""
    runner = detect_runner(root)
    assert runner is not None
    return runner


def _write(root: Path, name: str, content: str = "") -> None:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_no_markers_yields_none(tmp_path: Path) -> None:
    assert detect_runner(tmp_path) is None


def test_pytest_from_pyproject(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", "[tool.pytest.ini_options]\ntestpaths = ['tests']\n")
    runner = _detected(tmp_path)
    assert runner.name == "pytest"
    assert runner.command == "pytest"
    assert runner.evidence == "pyproject.toml"


def test_pyproject_without_pytest_falls_through_to_tests_dir(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", "[tool.ruff]\nline-length = 100\n")
    _write(tmp_path, "tests/test_x.py", "def test_x():\n    assert True\n")
    runner = _detected(tmp_path)
    assert runner.name == "unittest"


def test_pytest_from_ini(tmp_path: Path) -> None:
    _write(tmp_path, "pytest.ini", "[pytest]\n")
    assert detect_runner(tmp_path) == runners.Runner("pytest", "pytest", "pytest.ini")


def test_pytest_from_conftest(tmp_path: Path) -> None:
    _write(tmp_path, "conftest.py", "")
    assert _detected(tmp_path).name == "pytest"


def test_vitest_from_config(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", '{"name": "x"}')
    _write(tmp_path, "vitest.config.ts", "export default {}")
    runner = _detected(tmp_path)
    assert runner.name == "vitest" and runner.command == "npx vitest run"


def test_jest_from_devdeps(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", '{"devDependencies": {"jest": "^29"}}')
    runner = _detected(tmp_path)
    assert runner.name == "jest" and runner.command == "npx jest"


def test_npm_test_script_fallback(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", '{"scripts": {"test": "node t.js"}}')
    runner = _detected(tmp_path)
    assert runner == runners.Runner("npm", "npm test", "package.json")


def test_go_module(tmp_path: Path) -> None:
    _write(tmp_path, "go.mod", "module example.com/x\n")
    assert detect_runner(tmp_path) == runners.Runner("go", "go test ./...", "go.mod")


def test_cargo(tmp_path: Path) -> None:
    _write(tmp_path, "Cargo.toml", "[package]\nname = 'x'\n")
    assert detect_runner(tmp_path) == runners.Runner("cargo", "cargo test", "Cargo.toml")


def test_makefile_test_target(tmp_path: Path) -> None:
    _write(tmp_path, "Makefile", "build:\n\tcc x.c\ntest:\n\t./run\n")
    assert detect_runner(tmp_path) == runners.Runner("make", "make test", "Makefile")


def test_makefile_without_test_target_is_not_matched(tmp_path: Path) -> None:
    _write(tmp_path, "Makefile", "build:\n\tcc x.c\n")
    assert detect_runner(tmp_path) is None


def test_python_wins_over_node_when_both_present(tmp_path: Path) -> None:
    # Ordered detection: a Python project that also ships a package.json (tooling)
    # is reported as pytest, the more specific signal.
    _write(tmp_path, "pyproject.toml", "[tool.pytest.ini_options]\n")
    _write(tmp_path, "package.json", '{"scripts": {"test": "x"}}')
    assert _detected(tmp_path).name == "pytest"


def test_node_repo_with_tests_dir_is_npm_not_unittest(tmp_path: Path) -> None:
    # A JS repo with package.json AND a top-level tests/ dir must report npm, not
    # unittest: the bare tests-dir guess is ecosystem-agnostic and runs last, so it
    # never shadows the more-specific Node detector.
    _write(tmp_path, "package.json", '{"scripts": {"test": "node t.js"}}')
    _write(tmp_path, "tests/app.test.js", "// test\n")
    assert detect_runner(tmp_path) == runners.Runner("npm", "npm test", "package.json")


def test_bare_tests_dir_still_falls_back_to_unittest(tmp_path: Path) -> None:
    # With no ecosystem-specific marker, a lone tests/ dir is still a unittest project.
    _write(tmp_path, "tests/test_x.py", "def test_x():\n    assert True\n")
    assert detect_runner(tmp_path) == runners.Runner(
        "unittest", "python -m unittest discover", "tests/"
    )


def test_go_repo_with_tests_dir_is_go_not_unittest(tmp_path: Path) -> None:
    _write(tmp_path, "go.mod", "module example.com/x\n")
    _write(tmp_path, "tests/x_test.go", "package x\n")
    assert detect_runner(tmp_path) == runners.Runner("go", "go test ./...", "go.mod")


def test_malformed_config_does_not_raise(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", "this is : not [ valid toml")
    _write(tmp_path, "package.json", "{not json")
    # Best-effort: a malformed marker simply doesn't match, never crashes.
    assert detect_runner(tmp_path) is None


def test_detection_is_read_only_no_process_spawning() -> None:
    # The load-bearing guarantee: detection never executes tests. Prove it from the
    # imports — the module can't spawn a process it never imported the means to.
    tree = ast.parse(inspect.getsource(runners))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    forbidden = {"subprocess", "os", "sys", "pty", "multiprocessing", "shlex", "commands"}
    leaked = imported & forbidden
    assert not leaked, f"runners.py imports a process-spawning module: {leaked}"
