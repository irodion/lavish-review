"""Tests for the Config Resolver (issue #10).

Two layers, mirroring the module: the YAML-subset loader (:func:`parse_yaml`) and the
pure precedence merge (:func:`resolve`) are exercised table-driven with plain strings and
dicts — no filesystem — and the thin I/O shell (:func:`resolve_config` and the ``load_*``
helpers) is covered against real files in ``tmp_path``, including the "absent → defaults"
path. The classifier wiring (excludes extend by default, ``exclude_reset`` replaces) is
checked through the real :mod:`branch_review.classify` policy so the acceptance criteria
are pinned end to end.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from branch_review.classify import (
    DEFAULT_MAX_FILE_DIFF_LINES,
    DEFAULT_MAX_TOTAL_DIFF_LINES,
    Disposition,
    FileStats,
    classify,
)
from branch_review.config import (
    DEFAULT_STYLING,
    ConfigError,
    load_machine_config,
    load_repo_config,
    parse_yaml,
    resolve,
    resolve_config,
    resolved_config_dict,
)

# --- YAML subset loader -----------------------------------------------------

# (label, yaml text, expected dict) — one row per construct the config schema uses.
_YAML_CASES: list[tuple[str, str, dict[str, object]]] = [
    ("empty", "", {}),
    ("only comments", "# a\n  # b\n", {}),
    ("bare string", "base_branch: develop\n", {"base_branch": "develop"}),
    ("quoted double", 'focus: "security"\n', {"focus": "security"}),
    ("quoted single", "focus: 'regressions'\n", {"focus": "regressions"}),
    ("int", "limits:\n  max_file_diff_lines: 2000\n", {"limits": {"max_file_diff_lines": 2000}}),
    ("bool true", "sessionstart_hook: true\n", {"sessionstart_hook": True}),
    ("bool false", "exclude_reset: false\n", {"exclude_reset": False}),
    ("null tilde", "focus: ~\n", {"focus": None}),
    ("null keyword", "focus: null\n", {"focus": None}),
    ("version stays string", "lavish_version: 0.1.31\n", {"lavish_version": "0.1.31"}),
    ("float d.d", "ratio: 1.5\n", {"ratio": 1.5}),
    ("trailing comment", "styling: cdn  # opt in\n", {"styling": "cdn"}),
    ("hash in quotes", 'pause: "a#b"\n', {"pause": "a#b"}),
    (
        "block sequence",
        "language_hints:\n  - cpp\n  - python\n",
        {"language_hints": ["cpp", "python"]},
    ),
    ("flow sequence", "exclude: [a.py, b.py]\n", {"exclude": ["a.py", "b.py"]}),
    ("empty flow sequence", "exclude: []\n", {"exclude": []}),
    (
        "flow with quoted glob",
        'exclude: ["docs/**/*.snap", vendored/*]\n',
        {"exclude": ["docs/**/*.snap", "vendored/*"]},
    ),
    (
        "nested mapping",
        "limits:\n  max_file_diff_lines: 100\n  max_total_diff_lines: 200\n",
        {"limits": {"max_file_diff_lines": 100, "max_total_diff_lines": 200}},
    ),
    (
        "mixed top level",
        "base_branch: main\nstyling: vendored\nexclude:\n  - x.py\n",
        {"base_branch": "main", "styling": "vendored", "exclude": ["x.py"]},
    ),
]


@pytest.mark.parametrize("label, text, expected", _YAML_CASES, ids=[c[0] for c in _YAML_CASES])
def test_parse_yaml(label: str, text: str, expected: dict[str, object]) -> None:
    assert parse_yaml(text) == expected


_YAML_ERROR_CASES: list[tuple[str, str]] = [
    ("tab indent", "limits:\n\tmax_file_diff_lines: 1\n"),
    ("no colon", "just a bare line\n"),
    ("unterminated flow", "exclude: [a, b\n"),
    ("flow mapping unsupported", "limits: {a: 1}\n"),
    ("indented document start", "  base_branch: main\n"),
    ("bad over-indent", "base_branch: main\n    styling: cdn\n"),
    ("duplicate key", "focus: a\nfocus: b\n"),
]


@pytest.mark.parametrize("label, text", _YAML_ERROR_CASES, ids=[c[0] for c in _YAML_ERROR_CASES])
def test_parse_yaml_rejects(label: str, text: str) -> None:
    with pytest.raises(ConfigError):
        parse_yaml(text)


# --- resolve() precedence (pure) --------------------------------------------


def test_defaults_when_all_absent() -> None:
    resolved = resolve()
    assert resolved.base_branch is None
    assert resolved.styling == DEFAULT_STYLING
    assert resolved.focus is None
    assert resolved.language_hints == ()
    assert resolved.pause is None
    assert resolved.lavish_version is None
    assert resolved.sessionstart_hook is False
    assert resolved.classifier.max_file_diff_lines == DEFAULT_MAX_FILE_DIFF_LINES
    assert resolved.classifier.max_total_diff_lines == DEFAULT_MAX_TOTAL_DIFF_LINES
    assert resolved.classifier.extra_excludes == ()
    assert resolved.classifier.exclude_reset is False


# (label, arg_base, repo, machine, expected base_branch) — base is arg > repo (no machine).
_BASE_CASES: list[tuple[str, str | None, dict[str, object], str | None]] = [
    ("arg wins over repo", "develop", {"base_branch": "main"}, "develop"),
    ("repo when no arg", None, {"base_branch": "main"}, "main"),
    ("none when neither", None, {}, None),
    ("arg alone", "release", {}, "release"),
]


@pytest.mark.parametrize(
    "label, arg_base, repo, expected", _BASE_CASES, ids=[c[0] for c in _BASE_CASES]
)
def test_base_precedence(
    label: str, arg_base: str | None, repo: dict[str, object], expected: str | None
) -> None:
    assert resolve(arg_base=arg_base, repo=repo).base_branch == expected


# (label, repo, machine, expected styling) — styling is repo > machine > default.
_STYLING_CASES: list[tuple[str, dict[str, object], dict[str, object], str]] = [
    ("repo over machine", {"styling": "cdn"}, {"styling": "vendored"}, "cdn"),
    ("machine when no repo", {}, {"styling": "cdn"}, "cdn"),
    ("default when neither", {}, {}, "vendored"),
    ("repo vendored over machine cdn", {"styling": "vendored"}, {"styling": "cdn"}, "vendored"),
]


@pytest.mark.parametrize(
    "label, repo, machine, expected", _STYLING_CASES, ids=[c[0] for c in _STYLING_CASES]
)
def test_styling_precedence(
    label: str, repo: dict[str, object], machine: dict[str, object], expected: str
) -> None:
    assert resolve(repo=repo, machine=machine).styling == expected


def test_machine_scope_fields() -> None:
    machine = {"pause": "PAUSE", "lavish_version": "0.1.31", "sessionstart_hook": True}
    resolved = resolve(machine=machine)
    assert resolved.pause == "PAUSE"
    assert resolved.lavish_version == "0.1.31"
    assert resolved.sessionstart_hook is True


def test_repo_lens_fields() -> None:
    resolved = resolve(repo={"focus": "security", "language_hints": ["cpp", "python"]})
    assert resolved.focus == "security"
    assert resolved.language_hints == ("cpp", "python")


# --- classifier wiring: excludes extend by default; exclude_reset replaces --


def test_exclude_extends_builtins_by_default() -> None:
    """Configured excludes add to the built-ins; a built-in vendored dir still omits."""
    config = resolve(repo={"exclude": ["docs/*.snap"]}).classifier
    # The configured glob now omits...
    assert classify("docs/a.snap", FileStats(added=1), config) is Disposition.OMIT_EXCLUDED
    # ...and the built-in vendored dir is still in force (extend, not replace).
    assert classify("node_modules/x.js", FileStats(added=1), config) is Disposition.OMIT_EXCLUDED


def test_exclude_reset_replaces_builtins() -> None:
    """``exclude_reset: true`` drops the built-in dir/glob excludes, keeping only configured."""
    config = resolve(repo={"exclude": ["docs/*.snap"], "exclude_reset": True}).classifier
    # Built-in vendored dir is no longer excluded — its body is now included...
    assert classify("node_modules/x.js", FileStats(added=1), config) is Disposition.INCLUDE_BODY
    # ...but the configured glob still applies.
    assert classify("docs/a.snap", FileStats(added=1), config) is Disposition.OMIT_EXCLUDED


def test_limits_override_caps() -> None:
    config = resolve(
        repo={"limits": {"max_file_diff_lines": 10, "max_total_diff_lines": 20}}
    ).classifier
    assert config.max_file_diff_lines == 10
    assert config.max_total_diff_lines == 20


# --- validation -------------------------------------------------------------


_INVALID_CONFIGS: list[tuple[str, dict[str, object], dict[str, object]]] = [
    ("unknown repo key", {"base_brnach": "main"}, {}),
    ("unknown machine key", {}, {"styleing": "cdn"}),
    ("bad styling repo", {"styling": "fancy"}, {}),
    ("bad styling machine", {}, {"styling": "fancy"}),
    ("styling wrong type", {"styling": 3}, {}),
    ("exclude not a list", {"exclude": "x.py"}, {}),
    ("language_hints not a list", {"language_hints": "cpp"}, {}),
    ("limits not a mapping", {"limits": 5}, {}),
    ("limits unknown key", {"limits": {"max_lines": 5}}, {}),
    ("limit not int", {"limits": {"max_file_diff_lines": "big"}}, {}),
    ("limit non-positive", {"limits": {"max_file_diff_lines": 0}}, {}),
    ("exclude_reset not bool", {"exclude_reset": "yes"}, {}),
    ("sessionstart_hook not bool", {}, {"sessionstart_hook": "on"}),
    ("focus wrong type", {"focus": ["a"]}, {}),
]


@pytest.mark.parametrize(
    "label, repo, machine", _INVALID_CONFIGS, ids=[c[0] for c in _INVALID_CONFIGS]
)
def test_resolve_rejects_invalid(
    label: str, repo: dict[str, object], machine: dict[str, object]
) -> None:
    with pytest.raises(ConfigError):
        resolve(repo=repo, machine=machine)


# --- I/O shell: files, absence, integration --------------------------------


def test_load_absent_files_return_none(tmp_path: Path) -> None:
    assert load_repo_config(tmp_path) is None
    assert load_machine_config(tmp_path) is None


def test_empty_config_file_is_defaults(tmp_path: Path) -> None:
    (tmp_path / ".review-agent.yaml").write_text("# nothing here\n", encoding="utf-8")
    assert load_repo_config(tmp_path) == {}


def test_resolve_config_reads_both_scopes(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    home = tmp_path / "home"
    root.mkdir()
    (home / ".review-agent").mkdir(parents=True)
    (root / ".review-agent.yaml").write_text(
        "base_branch: develop\nstyling: cdn\nexclude:\n  - docs/*.snap\n"
        "limits:\n  max_file_diff_lines: 42\n",
        encoding="utf-8",
    )
    (home / ".review-agent" / "config.yaml").write_text(
        "pause: PAUSE\nstyling: vendored\nsessionstart_hook: true\n", encoding="utf-8"
    )
    resolved = resolve_config(root, home=home)
    # repo base_branch wins (no arg); repo styling wins over machine.
    assert resolved.base_branch == "develop"
    assert resolved.styling == "cdn"
    assert resolved.classifier.max_file_diff_lines == 42
    assert resolved.classifier.extra_excludes == ("docs/*.snap",)
    # machine-only fields come through.
    assert resolved.pause == "PAUSE"
    assert resolved.sessionstart_hook is True


def test_resolve_config_arg_overrides_repo_base(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".review-agent.yaml").write_text("base_branch: develop\n", encoding="utf-8")
    assert resolve_config(root, arg_base="release", home=tmp_path).base_branch == "release"


def test_resolve_config_absent_is_defaults(tmp_path: Path) -> None:
    resolved = resolve_config(tmp_path, home=tmp_path)
    assert resolved.base_branch is None
    assert resolved.styling == DEFAULT_STYLING


def test_resolved_config_dict_shape() -> None:
    resolved = resolve(repo={"styling": "cdn", "focus": "security"})
    payload = resolved_config_dict(resolved, base="origin/main")
    assert payload == {
        "schema": "review-resolved-config/0.1",
        "base": "origin/main",
        "styling": "cdn",
        "focus": "security",
        "language_hints": [],
        "pause": None,
        "lavish_version": None,
        "sessionstart_hook": False,
    }
