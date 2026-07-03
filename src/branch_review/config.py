"""The Config Resolver — repo + machine review policy, merged by precedence (issue #10).

Review policy travels with the repo. This module resolves the effective policy for a
run by layering four sources, **most specific first**:

    command arg  >  repo ``.review-agent.yaml``  >  machine ``~/.review-agent/config.yaml``
    >  built-in defaults

Two non-overlapping scopes (DESIGN "Configuration"):

- **Repo policy** (committed ``.review-agent.yaml``): ``base_branch``, ``exclude``,
  ``focus``, ``language_hints``, ``styling`` (``vendored``|``cdn``), and
  ``limits.{max_file_diff_lines, max_total_diff_lines}``. Configured ``exclude`` globs
  **extend** the Change Classifier's built-ins; ``exclude_reset: true`` replaces them
  (the built-in dir/glob excludes — never lockfiles or ``.gitattributes``; see
  :mod:`branch_review.classify`).
- **Machine policy** (``~/.review-agent/config.yaml``): the ``pause`` sentinel, a
  default ``styling``, the pinned Lavish version, and whether the SessionStart hook is on.

``goal_remote_fetch`` (Goal Evidence's tracker access, ADR-0010) is the one key both
scopes accept — a repo can pin its policy, a machine can go network-free wholesale;
repo wins when both set it.

Like the Change Classifier (:mod:`branch_review.classify`) and the Session Evaluator
(:mod:`branch_review.session`), the merge itself — :func:`resolve` — is **pure policy**:
it takes already-parsed mappings and returns a :class:`ResolvedConfig`, making the
precedence exhaustively table-testable with no filesystem. The thin I/O shell
(:func:`resolve_config` and the ``load_*`` helpers) reads the two YAML files and hands
their contents to :func:`resolve`; absent files simply resolve to defaults.

**No third-party YAML dependency** (ADR-0008): the diff collector is agent-agnostic
(git + stdlib), so this module ships a small, strict loader for the flat YAML subset the
config schema uses — scalars, one level of nested mapping (``limits``), and block/flow
sequences (``exclude``, ``language_hints``). Anything outside that subset, and any
unknown key, is a **located error**, never a silent mis-parse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from branch_review.classify import (
    DEFAULT_MAX_FILE_DIFF_LINES,
    DEFAULT_MAX_TOTAL_DIFF_LINES,
    ClassifierConfig,
)

# The two config files, by their fixed locations. The repo file sits at the repo root
# (committed policy); the machine file lives under the user's home (per-machine).
REPO_CONFIG_NAME = ".review-agent.yaml"
MACHINE_CONFIG_REL = Path(".review-agent") / "config.yaml"

# Styling controls whether the cockpit renders from local vendored assets (the default,
# enforced by the Cockpit Linter's no-remote rule) or opts into Lavish's CDN stack.
VALID_STYLING = ("vendored", "cdn")
DEFAULT_STYLING = "vendored"

# The recognized keys per scope. Anything else is a typo or an unsupported key and is
# rejected loudly (never silently ignored) so a misspelled ``base_brnach`` can't quietly
# fall back to auto-detect.
_REPO_KEYS = frozenset(
    {
        "base_branch",
        "exclude",
        "exclude_reset",
        "focus",
        "language_hints",
        "styling",
        "limits",
        "goal_remote_fetch",
    }
)
_LIMITS_KEYS = frozenset({"max_file_diff_lines", "max_total_diff_lines"})
_MACHINE_KEYS = frozenset(
    {"pause", "styling", "lavish_version", "sessionstart_hook", "goal_remote_fetch"}
)

_RESOLVED_CONFIG_SCHEMA = "review-resolved-config/0.1"


class ConfigError(ValueError):
    """A config file is malformed, has an unknown key, or carries an invalid value."""


# --- YAML subset loader -----------------------------------------------------
#
# A deliberately small, strict reader for the flat config subset (ADR-0008). It is NOT a
# general YAML parser: it accepts ``key: value`` mappings, one level of nesting via an
# indented block, block sequences (``- item``) and inline flow sequences (``[a, b]``),
# ``# comments``, and quoted/typed scalars. Tabs in indentation, flow mappings, and other
# YAML constructs are errors — the config schema needs none of them, and refusing them
# keeps the reader honest rather than approximate.

# A scalar is an int only if it is *all* digits, and a float only for ``d.d`` — so
# ``0.1.31`` (a version) and ``develop`` stay strings instead of being coerced.
_INT_RE = re.compile(r"[+-]?[0-9]+$")
_FLOAT_RE = re.compile(r"[+-]?[0-9]+\.[0-9]+$")


@dataclass(frozen=True)
class _Line:
    """One significant (non-blank, non-comment) config line: its indent and content."""

    indent: int
    content: str
    lineno: int


def _strip_comment(line: str) -> str:
    """Drop a trailing ``#`` comment, honoring quotes and YAML's whitespace-before rule.

    A ``#`` starts a comment only at the line start or after whitespace, and never inside
    a quoted scalar — so ``pause: "a#b"`` and ``exclude: ["a#b"]`` keep their literal ``#``.
    """
    quote: str | None = None
    for i, ch in enumerate(line):
        if quote is not None:
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def _tokenize(text: str) -> list[_Line]:
    """Split ``text`` into significant lines, rejecting tab-indentation up front."""
    lines: list[_Line] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        leading = stripped[: len(stripped) - len(stripped.lstrip(" \t"))]
        if "\t" in leading:
            raise ConfigError(f"line {lineno}: tab in indentation — use spaces")
        lines.append(_Line(len(leading), stripped.strip(), lineno))
    return lines


def _split_flow(inner: str) -> list[str]:
    """Split a flow-sequence body on top-level commas, respecting quotes."""
    items: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    for ch in inner:
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == ",":
            items.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail or items:
        items.append(tail)
    return items


def _parse_scalar(text: str, lineno: int) -> object:
    """Parse a single scalar: quoted string, bool, null, int, float, or bare string."""
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1].replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t")
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")
    if text.startswith(("[", "{")):
        raise ConfigError(f"line {lineno}: unexpected flow value {text!r}")
    low = text.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "~"):
        return None
    if _INT_RE.match(text):
        return int(text)
    if _FLOAT_RE.match(text):
        return float(text)
    return text


def _parse_value(text: str, lineno: int) -> object:
    """Parse a scalar or an inline flow sequence (``[a, b, c]``)."""
    if text.startswith("["):
        if not text.endswith("]"):
            raise ConfigError(f"line {lineno}: unterminated flow sequence {text!r}")
        body = text[1:-1].strip()
        if not body:
            return []
        return [_parse_scalar(tok, lineno) for tok in _split_flow(body)]
    return _parse_scalar(text, lineno)


def _parse_block(lines: list[_Line], start: int, indent: int) -> tuple[object, int]:
    """Parse the sibling nodes at ``indent`` starting at ``lines[start]``.

    Returns ``(collection, next_index)``. A block is a **sequence** when its first line is
    a ``- `` item, otherwise a **mapping**. Recursion handles the one nested level the
    schema uses (``limits:``) and block sequences; deeper structures are still parsed
    generically, and unexpected indentation is an error rather than a silent drop.
    """
    if lines[start].content == "-" or lines[start].content.startswith("- "):
        return _parse_sequence(lines, start, indent)
    return _parse_mapping(lines, start, indent)


def _parse_mapping(lines: list[_Line], start: int, indent: int) -> tuple[dict[str, object], int]:
    result: dict[str, object] = {}
    i = start
    while i < len(lines):
        line = lines[i]
        if line.indent < indent:
            break
        if line.indent > indent:
            raise ConfigError(f"line {line.lineno}: unexpected indentation")
        if line.content.startswith("- "):
            raise ConfigError(f"line {line.lineno}: sequence item where a mapping key was expected")
        key, sep, rest = line.content.partition(":")
        if not sep:
            raise ConfigError(f"line {line.lineno}: expected 'key: value'")
        key = key.strip()
        if key in result:
            raise ConfigError(f"line {line.lineno}: duplicate key {key!r}")
        rest = rest.strip()
        if rest:
            result[key] = _parse_value(rest, line.lineno)
            i += 1
        elif i + 1 < len(lines) and lines[i + 1].indent > indent:
            result[key], i = _parse_block(lines, i + 1, lines[i + 1].indent)
        else:
            result[key] = None
            i += 1
    return result, i


def _parse_sequence(lines: list[_Line], start: int, indent: int) -> tuple[list[object], int]:
    items: list[object] = []
    i = start
    while i < len(lines):
        line = lines[i]
        if line.indent < indent:
            break
        if line.indent > indent:
            raise ConfigError(f"line {line.lineno}: unexpected indentation")
        if not (line.content == "-" or line.content.startswith("- ")):
            raise ConfigError(f"line {line.lineno}: expected a '- ' sequence item")
        rest = line.content[1:].strip()
        if rest:
            items.append(_parse_value(rest, line.lineno))
            i += 1
        elif i + 1 < len(lines) and lines[i + 1].indent > indent:
            child, i = _parse_block(lines, i + 1, lines[i + 1].indent)
            items.append(child)
        else:
            items.append(None)
            i += 1
    return items, i


def parse_yaml(text: str) -> dict[str, object]:
    """Parse the config YAML subset into a mapping (an empty document is ``{}``)."""
    lines = _tokenize(text)
    if not lines:
        return {}
    if lines[0].indent != 0:
        raise ConfigError(f"line {lines[0].lineno}: unexpected indentation at document start")
    value, consumed = _parse_block(lines, 0, 0)
    if consumed != len(lines):  # a stray de-indented block the top level didn't absorb
        raise ConfigError(f"line {lines[consumed].lineno}: unexpected content")
    if not isinstance(value, dict):
        raise ConfigError("top-level config must be a mapping")
    return value


# --- Resolution (pure policy) ----------------------------------------------


@dataclass(frozen=True)
class ResolvedConfig:
    """The effective policy for one run, after layering arg > repo > machine > defaults.

    ``base_branch`` is the *preferred* base (arg or repo ``base_branch``); it is ``None``
    when neither names one, leaving the collector to auto-detect (and to ask on ambiguity).
    ``classifier`` folds the repo's ``exclude``/``exclude_reset``/``limits`` into the
    Change Classifier policy. The remaining fields surface the resolved styling, the
    authoring lenses (``focus``/``language_hints``), and the machine-scope settings the
    skill threads into open/loop steps.
    """

    base_branch: str | None
    styling: str
    focus: str | None
    language_hints: tuple[str, ...]
    classifier: ClassifierConfig
    pause: str | None
    lavish_version: str | None
    sessionstart_hook: bool = False
    # Whether Goal Evidence may reach the tracker via ``gh`` (ADR-0010). Defaults on;
    # either scope can switch it off wholesale — repo (committed policy) wins over
    # machine when both set it. Even when on, the collector only fetches when local
    # evidence (or --goal) names an issue, so the default review stays network-free.
    goal_remote_fetch: bool = True


def _reject_unknown(mapping: dict[str, object], allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {unknown}")


def _as_opt_str(value: object, key: str) -> str | None:
    """A string value (or ``None`` when absent); reject a mapping/list/bool for a scalar key."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ConfigError(f"{key}: expected a string, got {type(value).__name__}")
    return str(value)


def _as_str_list(value: object, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError(f"{key}: expected a list, got {type(value).__name__}")
    out: list[str] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (str, int, float)):
            raise ConfigError(f"{key}: list items must be strings, got {type(item).__name__}")
        out.append(str(item))
    return tuple(out)


def _as_bool(value: object, key: str, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ConfigError(f"{key}: expected a boolean, got {type(value).__name__}")
    return value


def _as_opt_bool(value: object, key: str) -> bool | None:
    """A boolean or ``None`` when absent — for keys merged across scopes, where
    "not set here" must stay distinguishable from an explicit ``false``."""
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ConfigError(f"{key}: expected a boolean, got {type(value).__name__}")
    return value


def _as_pos_int(value: object, key: str, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key}: expected an integer, got {type(value).__name__}")
    if value <= 0:
        raise ConfigError(f"{key}: must be a positive integer, got {value}")
    return value


def _styling(value: object, where: str) -> str | None:
    """Validate a ``styling`` value against the allowed set, or ``None`` when absent."""
    if value is None:
        return None
    if value not in VALID_STYLING:
        raise ConfigError(f"{where}: styling must be one of {list(VALID_STYLING)}, got {value!r}")
    return str(value)


def _classifier(repo: dict[str, object]) -> ClassifierConfig:
    """Fold the repo scope's excludes and limits into the Change Classifier policy."""
    limits_raw = repo.get("limits")
    if limits_raw is None:
        limits: dict[str, object] = {}
    elif isinstance(limits_raw, dict):
        limits = limits_raw
    else:
        raise ConfigError(f"limits: expected a mapping, got {type(limits_raw).__name__}")
    _reject_unknown(limits, _LIMITS_KEYS, "limits")

    return ClassifierConfig(
        max_file_diff_lines=_as_pos_int(
            limits.get("max_file_diff_lines"),
            "limits.max_file_diff_lines",
            default=DEFAULT_MAX_FILE_DIFF_LINES,
        ),
        max_total_diff_lines=_as_pos_int(
            limits.get("max_total_diff_lines"),
            "limits.max_total_diff_lines",
            default=DEFAULT_MAX_TOTAL_DIFF_LINES,
        ),
        extra_excludes=_as_str_list(repo.get("exclude"), "exclude"),
        exclude_reset=_as_bool(repo.get("exclude_reset"), "exclude_reset", default=False),
    )


def resolve(
    *,
    arg_base: str | None = None,
    repo: dict[str, object] | None = None,
    machine: dict[str, object] | None = None,
) -> ResolvedConfig:
    """Merge the parsed scopes into a :class:`ResolvedConfig` (arg > repo > machine > defaults).

    Each key is resolved by the scopes that define it: ``base_branch`` from the arg or the
    repo; ``styling`` from repo then machine then the built-in default; ``focus``,
    ``language_hints``, and the classifier policy from the repo; ``pause``,
    ``lavish_version``, and ``sessionstart_hook`` from the machine. Pure: no filesystem —
    :func:`resolve_config` is the shell that reads the files.
    """
    repo = repo or {}
    machine = machine or {}
    _reject_unknown(repo, _REPO_KEYS, f"repo {REPO_CONFIG_NAME}")
    _reject_unknown(machine, _MACHINE_KEYS, "machine config")

    base_branch = arg_base or _as_opt_str(repo.get("base_branch"), "base_branch")
    styling = (
        _styling(repo.get("styling"), f"repo {REPO_CONFIG_NAME}")
        or _styling(machine.get("styling"), "machine config")
        or DEFAULT_STYLING
    )
    repo_goal_fetch = _as_opt_bool(repo.get("goal_remote_fetch"), "goal_remote_fetch")
    machine_goal_fetch = _as_opt_bool(machine.get("goal_remote_fetch"), "goal_remote_fetch")
    goal_remote_fetch = (
        repo_goal_fetch
        if repo_goal_fetch is not None
        else machine_goal_fetch
        if machine_goal_fetch is not None
        else True
    )
    return ResolvedConfig(
        base_branch=base_branch,
        styling=styling,
        focus=_as_opt_str(repo.get("focus"), "focus"),
        language_hints=_as_str_list(repo.get("language_hints"), "language_hints"),
        classifier=_classifier(repo),
        pause=_as_opt_str(machine.get("pause"), "pause"),
        lavish_version=_as_opt_str(machine.get("lavish_version"), "lavish_version"),
        sessionstart_hook=_as_bool(
            machine.get("sessionstart_hook"), "sessionstart_hook", default=False
        ),
        goal_remote_fetch=goal_remote_fetch,
    )


# --- I/O shell --------------------------------------------------------------


def load_config_file(path: Path) -> dict[str, object] | None:
    """Parse a config file into a mapping, or ``None`` when the file is absent.

    An empty file resolves to ``{}`` (defaults). A top-level non-mapping is a
    :class:`ConfigError`, located to the offending file.
    """
    if not path.is_file():
        return None
    try:
        return parse_yaml(path.read_text(encoding="utf-8"))
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def load_repo_config(root: Path) -> dict[str, object] | None:
    """Load the committed repo policy at ``<root>/.review-agent.yaml`` (or ``None``)."""
    return load_config_file(root / REPO_CONFIG_NAME)


def load_machine_config(home: Path | None = None) -> dict[str, object] | None:
    """Load the per-machine policy at ``~/.review-agent/config.yaml`` (or ``None``)."""
    return load_config_file((home or Path.home()) / MACHINE_CONFIG_REL)


def resolve_config(
    root: Path, *, arg_base: str | None = None, home: Path | None = None
) -> ResolvedConfig:
    """Read both config files under ``root``/``home`` and resolve the effective policy.

    ``arg_base`` is the base the reviewer passed to ``/review-branch`` (``None`` =
    auto-detect). ``home`` overrides the machine-config location (for tests); it defaults
    to the real home directory.
    """
    return resolve(
        arg_base=arg_base,
        repo=load_repo_config(root),
        machine=load_machine_config(home),
    )


def resolved_config_dict(resolved: ResolvedConfig, *, base: str) -> dict[str, object]:
    """The ``resolved-config.json`` payload the collector writes for the skill to read.

    ``base`` is the *final* resolved base (after auto-detect), so the skill and later steps
    see one authoritative value; the rest are the styling/lens/machine settings that shape
    authoring, linting, and the open/loop steps.
    """
    return {
        "schema": _RESOLVED_CONFIG_SCHEMA,
        "base": base,
        "styling": resolved.styling,
        "focus": resolved.focus,
        "language_hints": list(resolved.language_hints),
        "pause": resolved.pause,
        "lavish_version": resolved.lavish_version,
        "sessionstart_hook": resolved.sessionstart_hook,
        "goal_remote_fetch": resolved.goal_remote_fetch,
    }
