"""Tests for the deterministic Review context collector.

Each test builds a throwaway git repo in a temp dir so the collector runs against
real ``git`` plumbing — the layer it exists to wrap.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from branch_review.classify import ClassifierConfig
from branch_review.collect import (
    BaseResolutionError,
    collect,
    copy_assets,
    detect_base,
)

_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": os.environ["PATH"], "HOME": str(repo), **_ENV},
    )
    return proc.stdout.strip()


def _commit(repo: Path, name: str, content: str, message: str) -> None:
    (repo / name).write_text(content)
    _git(repo, "add", name)
    _git(repo, "commit", "-m", message)


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Path]:
    """A repo with a `main` base commit and a `feature` branch with one change."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _commit(root, "app.py", "x = 1\n", "base: initial")
    _git(root, "checkout", "-b", "feature")
    _commit(root, "app.py", "x = 2\n", "feat: bump x")
    yield root


def test_detect_base_falls_back_to_main(repo: Path) -> None:
    assert detect_base(repo) == "main"


def test_detect_base_raises_when_no_candidate(tmp_path: Path) -> None:
    root = tmp_path / "orphan"
    root.mkdir()
    _git(root, "init", "-b", "trunk")
    _commit(root, "f.txt", "hi\n", "only commit")
    with pytest.raises(BaseResolutionError):
        detect_base(root)


def test_detect_base_prefers_remote_default_over_local(repo: Path) -> None:
    # origin/HEAD -> origin/main AND a local `main` both exist. Documented
    # precedence is origin/HEAD first: returning local `main` would risk diffing
    # against a stale base and leaking already-merged commits into the cockpit.
    main_sha = _git(repo, "rev-parse", "main")
    _git(repo, "update-ref", "refs/remotes/origin/main", main_sha)
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    # local `main` is intentionally left in place.

    assert detect_base(repo) == "origin/main"

    context = collect(repo)
    assert context.base == "origin/main"
    assert context.diff_range == "origin/main...HEAD"


def test_detect_base_uses_remote_default_when_no_local(repo: Path) -> None:
    # Simulate a feature-only checkout: origin/HEAD -> origin/main, but no local main.
    main_sha = _git(repo, "rev-parse", "main")
    _git(repo, "update-ref", "refs/remotes/origin/main", main_sha)
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    _git(repo, "checkout", "feature")
    _git(repo, "branch", "-D", "main")

    assert detect_base(repo) == "origin/main"

    # ...and the remote-tracking ref must drive a working collection.
    context = collect(repo)
    assert context.base == "origin/main"
    assert context.diff_range == "origin/main...HEAD"
    assert context.changed_file_count == 1


def test_collect_writes_context_and_files(repo: Path) -> None:
    context = collect(repo)
    out = repo / ".review-agent"

    assert context.base == "main"
    assert context.branch == "feature"
    assert context.changed_file_count == 1
    assert not context.is_empty

    for name in (
        "context.json",
        "changed-files.json",
        "diff.patch",
        "diff-stat.txt",
        "commits.txt",
        "diff.fragment.html",
    ):
        assert (out / name).is_file(), name

    written = json.loads((out / "context.json").read_text())
    assert written["base"] == "main"
    assert written["branch"] == "feature"
    assert written["diff_range"] == "main...HEAD"
    assert written["schema"].startswith("review-context/")

    files = json.loads((out / "changed-files.json").read_text())
    assert files == [{"status": "M", "path": "app.py"}]

    assert "x = 2" in (out / "diff.patch").read_text()
    assert "feat: bump x" in (out / "commits.txt").read_text()


def _fragments_index(out: Path) -> dict[str, dict[str, object]]:
    """Load fragments.json keyed by path for assertions (order checked separately)."""
    data = json.loads((out / "fragments.json").read_text())
    return {rec["path"]: rec for rec in data["files"]}


def test_per_file_fragments_written_in_changed_files_order(repo: Path) -> None:
    _git(repo, "checkout", "feature")
    _commit(repo, "b.py", "b = 1\n", "feat: add b")
    _commit(repo, "a.py", "a = 1\n", "feat: add a")
    collect(repo)
    out = repo / ".review-agent"

    changed = [rec["path"] for rec in json.loads((out / "changed-files.json").read_text())]
    indexed = [rec["path"] for rec in json.loads((out / "fragments.json").read_text())["files"]]
    assert indexed == changed  # ordered index preserves changed-files order

    # Every non-omitted entry has a real, escaped fragment file on disk.
    for rec in _fragments_index(out).values():
        frag = out / str(rec["fragment"])
        assert frag.is_file()
        assert frag.read_text().startswith('<pre class="diff">')


def test_per_file_fragment_isolates_html_in_one_file(repo: Path) -> None:
    _git(repo, "checkout", "feature")
    _commit(repo, "safe.py", "ok = 1\n", "feat: safe file")
    _commit(repo, "evil.py", "# <script>alert(1)</script>\n", "feat: xss bait")
    collect(repo)
    out = repo / ".review-agent"
    index = _fragments_index(out)

    evil = (out / str(index["evil.py"]["fragment"])).read_text()
    assert "<script>alert(1)</script>" not in evil
    assert "&lt;script&gt;" in evil
    # The payload stays contained in its own file's fragment, not the neighbour's.
    safe = (out / str(index["safe.py"]["fragment"])).read_text()
    assert "script" not in safe


def test_per_file_fragment_records_rename(repo: Path) -> None:
    _git(repo, "checkout", "feature")
    _git(repo, "mv", "app.py", "renamed.py")
    # Match the base content so git scores it a pure rename (R100), not add+delete.
    (repo / "renamed.py").write_text("x = 1\n")
    _git(repo, "add", "renamed.py")
    _git(repo, "commit", "-m", "refactor: rename app.py")
    collect(repo)
    index = _fragments_index(repo / ".review-agent")

    rename = index["renamed.py"]
    assert str(rename["status"]).startswith("R")
    assert rename["old_path"] == "app.py"
    assert rename["fragment"] is not None


def test_per_file_fragment_handles_unusual_path(repo: Path) -> None:
    _git(repo, "checkout", "feature")
    odd = "weird dir/na me \U0001f600.py"
    (repo / "weird dir").mkdir()
    (repo / odd).write_text("x = 1\n")
    _git(repo, "add", "--", odd)
    _git(repo, "commit", "-m", "feat: odd path")
    collect(repo)
    out = repo / ".review-agent"
    index = _fragments_index(out)

    # The odd path is keyed verbatim, but its fragment file is a safe hex stem.
    rec = index[odd]
    frag_name = Path(str(rec["fragment"])).name
    assert all(c in "0123456789abcdef" for c in frag_name.removesuffix(".html"))
    assert (out / str(rec["fragment"])).is_file()


def test_per_file_fragment_handles_tab_in_path(repo: Path) -> None:
    # A literal tab in a filename is C-quoted by git even with core.quotePath=false
    # (the tab would corrupt the line/tab format), which would record a quoted path
    # and make the path-scoped per-file diff empty — silently hiding the body.
    # NUL-delimited (-z) name-status keeps the path raw so the body survives.
    _git(repo, "checkout", "feature")
    odd = "weird\ttab.py"
    (repo / odd).write_text("tabbed = 1\n")
    _git(repo, "add", "--", odd)
    _git(repo, "commit", "-m", "feat: tab in path")
    collect(repo)
    out = repo / ".review-agent"
    index = _fragments_index(out)

    assert odd in index, list(index)
    frag = (out / str(index[odd]["fragment"])).read_text()
    assert "(no changes in this range)" not in frag  # the body must not vanish
    assert "tabbed = 1" in frag


def test_per_file_fragment_handles_pathspec_magic_name(repo: Path) -> None:
    # A file literally named with a pathspec-magic prefix must be treated as an exact
    # filename (--literal-pathspecs), not reinterpreted as a glob that sweeps in other
    # files. Without the literal flag, `:(glob)*.py` would match every .py file and
    # the fragment would wrongly include the decoy's content.
    _git(repo, "checkout", "feature")
    (repo / "decoy.py").write_text("decoy = 1\n")
    magic = ":(glob)*.py"
    (repo / magic).write_text("globby = 1\n")
    _git(repo, "add", "-A")  # -A avoids a pathspec arg of its own
    _git(repo, "commit", "-m", "feat: pathspec-magic filename")
    collect(repo)
    out = repo / ".review-agent"
    index = _fragments_index(out)

    assert magic in index, list(index)
    frag = (out / str(index[magic]["fragment"])).read_text()
    assert "globby = 1" in frag  # this file's own body
    assert "decoy = 1" not in frag  # not a glob match that swept in the decoy
    # The on-disk fragment filename is still a safe hex stem.
    stem = Path(str(index[magic]["fragment"])).name.removesuffix(".html")
    assert all(c in "0123456789abcdef" for c in stem)


def test_per_file_fragments_rebuilt_without_orphans(repo: Path) -> None:
    # First run with two files, second run with one — the dropped file's fragment
    # must not linger in fragments/ or the index.
    _git(repo, "checkout", "feature")
    _commit(repo, "gone.py", "g = 1\n", "feat: temporary file")
    collect(repo)
    out = repo / ".review-agent"
    assert "gone.py" in _fragments_index(out)
    stale = out / str(_fragments_index(out)["gone.py"]["fragment"])
    assert stale.is_file()

    _git(repo, "rm", "gone.py")
    _git(repo, "commit", "-m", "chore: drop temporary file")
    collect(repo)
    assert "gone.py" not in _fragments_index(out)
    assert not stale.exists()  # fragments/ rebuilt from scratch


def test_authored_cockpit_from_fragments_passes_lint(repo: Path) -> None:
    """End-to-end: a cockpit assembled the way the SKILL instructs is lint-clean.

    Proves the Escape Boundary holds through the new #6 sections — a hostile path
    and an HTML-in-hunk are injected only via ``path_html`` and the per-file
    fragment, and the Cockpit Linter (issue #4) finds nothing to complain about.
    """
    from branch_review.escape import STRICT_CSP
    from branch_review.lint import lint_cockpit

    _git(repo, "checkout", "feature")
    odd = "src/inj<x>&'.py"  # hostile but filesystem-legal (no path separator)
    (repo / "src").mkdir(exist_ok=True)
    (repo / odd).write_text('html = "<img src=x onerror=alert(1)>"\n')
    _git(repo, "add", "--", odd)
    _git(repo, "commit", "-m", "feat: hostile path and hunk")
    collect(repo)
    out = repo / ".review-agent"

    header = (out / "fragments.html").read_text(encoding="utf-8")
    index = _fragments_index(out)
    entry = index[odd]
    diff_frag = (out / str(entry["fragment"])).read_text(encoding="utf-8")

    # Build the File Walkthrough exactly as the SKILL prescribes: path from
    # `path_html` (escaped), body from the per-file fragment (escaped), prose ours.
    cockpit = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<meta http-equiv='Content-Security-Policy' content=\"{STRICT_CSP}\">"
        "<link rel='stylesheet' href='assets/cockpit.css'></head><body><main>"
        f"<header class='cockpit-head'>{header}</header>"
        "<section><h2>Executive Summary</h2><p class='intent'>A test branch.</p></section>"
        "<section><h2>File Walkthrough</h2>"
        "<div class='walkthrough-file'>"
        f"<h3>{entry['path_html']}</h3>"
        "<p class='explanation'>This file is hostile by design.</p>"
        f"{diff_frag}</div></section>"
        "<script src='assets/app.js'></script></body></html>"
    )

    errors = lint_cockpit(cockpit, styling="vendored")
    assert errors == [], errors
    # And the payloads really are neutralised, not merely tolerated: every hostile
    # angle bracket became an entity, so neither the path nor the hunk can form a tag.
    assert "inj<x>" not in cockpit  # the angle brackets in the path are escaped
    assert "<img" not in cockpit  # the hunk's tag is escaped to &lt;img (inert text)
    assert "&lt;img" in cockpit


def test_explicit_base_overrides_autodetect(repo: Path) -> None:
    # A second base whose merge-base with HEAD is the same initial commit.
    _git(repo, "branch", "develop", "main")
    context = collect(repo, base="develop")
    assert context.base == "develop"
    assert context.diff_range == "develop...HEAD"


def test_diff_fragment_escapes_untrusted_content(repo: Path) -> None:
    _git(repo, "checkout", "feature")
    _commit(repo, "evil.py", "# <script>alert(1)</script>\n", "feat: add xss bait")
    collect(repo)
    fragment = (repo / ".review-agent" / "diff.fragment.html").read_text()

    assert "<script>alert(1)</script>" not in fragment
    assert "&lt;script&gt;" in fragment
    assert fragment.startswith('<pre class="diff">')


def test_artifacts_are_utf8(repo: Path) -> None:
    _git(repo, "checkout", "feature")
    _commit(repo, "app.py", "x = 'é你好\U0001f600'\n", "feat: café 你好 😀")
    collect(repo)
    out = repo / ".review-agent"

    # Decoding strictly as UTF-8 must succeed and preserve the non-ASCII bytes.
    assert "café 你好 \U0001f600" in (out / "commits.txt").read_text(encoding="utf-8")
    assert "你好\U0001f600" in (out / "diff.patch").read_text(encoding="utf-8")
    assert "你好\U0001f600" in (out / "diff.fragment.html").read_text(encoding="utf-8")


def test_empty_range_is_marked(repo: Path) -> None:
    _git(repo, "checkout", "main")
    _git(repo, "branch", "-D", "feature")
    _git(repo, "checkout", "-b", "noop")  # no new commits → empty range vs main
    context = collect(repo)
    assert context.is_empty
    assert context.changed_file_count == 0
    assert "no changes" in (repo / ".review-agent" / "diff.fragment.html").read_text()


def test_collect_copies_assets(repo: Path, tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "cockpit.css").write_text("/* css */")
    (assets / "app.js").write_text("// js")

    collect(repo, assets_dir=assets)
    dest = repo / ".review-agent" / "assets"
    assert (dest / "cockpit.css").read_text() == "/* css */"
    assert (dest / "app.js").read_text() == "// js"


def test_copy_assets_missing_source_raises(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        copy_assets(empty, tmp_path / "dest")


def test_skill_shim_runs_without_editable_install(repo: Path, tmp_path: Path) -> None:
    """The documented `python <shim>` works in a fresh checkout (no editable install).

    Run with `-S` so site-packages (where the editable install lives) is not on
    the path — the script only succeeds if its own `sys.path` insertion finds
    `branch_review` under the repo's `src/`.
    """
    project_root = Path(__file__).resolve().parents[1]
    shim = project_root / ".claude/skills/branch-review-cockpit/scripts/collect_review_context.py"
    out = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, "-S", str(shim), "--repo", str(repo), "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": os.environ["PATH"], "HOME": str(repo), **_ENV},
    )

    assert proc.returncode == 0, proc.stderr
    assert (out / "context.json").is_file()
    assert (out / "diff.fragment.html").is_file()
    # Default --assets-dir resolves to the skill's vendored assets and is copied.
    assert (out / "assets" / "cockpit.css").is_file()
    assert (out / "assets" / "app.js").is_file()


def test_skill_shim_honors_assets_dir_equals_form(repo: Path, tmp_path: Path) -> None:
    """`--assets-dir=VALUE` must override the vendored default, not be ignored."""
    project_root = Path(__file__).resolve().parents[1]
    shim = project_root / ".claude/skills/branch-review-cockpit/scripts/collect_review_context.py"
    custom = tmp_path / "custom-assets"
    custom.mkdir()
    (custom / "cockpit.css").write_text("/* custom */")
    (custom / "app.js").write_text("// custom")
    out = tmp_path / "out"

    proc = subprocess.run(
        [
            sys.executable,
            str(shim),
            f"--assets-dir={custom}",
            "--repo",
            str(repo),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": os.environ["PATH"], "HOME": str(repo), **_ENV},
    )

    assert proc.returncode == 0, proc.stderr
    assert (out / "assets" / "cockpit.css").read_text() == "/* custom */"
    assert (out / "assets" / "app.js").read_text() == "// custom"


# --- Change Classifier wiring (issue #7) -----------------------------------
#
# These exercise the classifier *through the collector* against real git plumbing:
# the per-disposition policy itself is unit-tested in test_classify.py.


def _add_lines(repo: Path, name: str, n: int, message: str) -> None:
    """Commit a file of ``n`` lines on the current branch (parents created)."""
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(f"line {i}\n" for i in range(n)))
    _git(repo, "add", name)
    _git(repo, "commit", "-m", message)


def test_lockfile_body_omitted_but_listed_with_stats(repo: Path) -> None:
    _git(repo, "checkout", "feature")
    _add_lines(repo, "package-lock.json", 200, "chore: lockfile")
    collect(repo)
    out = repo / ".review-agent"
    index = _fragments_index(out)

    lock = index["package-lock.json"]
    assert lock["disposition"] == "omit:lockfile"
    assert lock["omitted"] is True
    assert lock["fragment"] is None  # body dropped
    assert str(lock["reason"]).strip()
    # Existence + stats survive: the file is listed and its line counts are kept.
    assert lock["added"] == 200
    assert "package-lock.json" in {rec["path"] for rec in json.loads(
        (out / "changed-files.json").read_text())}
    # No orphan body fragment was written for the omitted file.
    assert not (out / "fragments" / f"{lock['id']}.html").exists()


def test_vendored_and_generated_are_excluded(repo: Path) -> None:
    _git(repo, "checkout", "feature")
    _add_lines(repo, "node_modules/dep/index.js", 30, "chore: vendored dep")
    _add_lines(repo, "api/types.generated.ts", 30, "chore: generated types")
    collect(repo)
    index = _fragments_index(repo / ".review-agent")

    for path in ("node_modules/dep/index.js", "api/types.generated.ts"):
        rec = index[path]
        assert rec["disposition"] == "omit:excluded", path
        assert rec["fragment"] is None
        assert rec["omitted"] is True


def test_linguist_generated_attribute_is_honored(repo: Path) -> None:
    _git(repo, "checkout", "feature")
    # A normal-looking path becomes excluded purely via .gitattributes.
    (repo / ".gitattributes").write_text("generated.py linguist-generated\n")
    _add_lines(repo, "generated.py", 20, "feat: add generated.py")
    _git(repo, "add", ".gitattributes")
    _git(repo, "commit", "-m", "chore: mark generated")
    collect(repo)
    index = _fragments_index(repo / ".review-agent")

    rec = index["generated.py"]
    assert rec["disposition"] == "omit:excluded"
    assert rec["fragment"] is None
    assert "linguist-generated" in str(rec["reason"])


def test_oversized_file_body_omitted_but_listed(repo: Path) -> None:
    _git(repo, "checkout", "feature")
    _add_lines(repo, "huge.py", 50, "feat: huge file")
    collect(repo, config=ClassifierConfig(max_file_diff_lines=10))
    index = _fragments_index(repo / ".review-agent")

    huge = index["huge.py"]
    assert huge["disposition"] == "omit:too-large"
    assert huge["fragment"] is None
    assert huge["added"] == 50  # stats kept even though the body is gone
    assert "50" in str(huge["reason"])


def test_normal_file_keeps_its_body(repo: Path) -> None:
    # The default repo fixture changes app.py by one line — a plain include-body.
    collect(repo)
    index = _fragments_index(repo / ".review-agent")
    app = index["app.py"]
    assert app["disposition"] == "include-body"
    assert app["omitted"] is False
    assert app["fragment"] is not None
    assert (repo / ".review-agent" / str(app["fragment"])).is_file()


def test_total_diff_guard_falls_back_to_listing(repo: Path) -> None:
    _git(repo, "checkout", "feature")
    _add_lines(repo, "a.py", 40, "feat: a")
    _add_lines(repo, "b.py", 40, "feat: b")
    # Total changed lines (~80+) exceeds the tiny total cap → file-list + stats only.
    collect(repo, config=ClassifierConfig(max_total_diff_lines=20))
    out = repo / ".review-agent"
    index = _fragments_index(out)

    # Every file is omitted under the fallback — but every file is still listed,
    # with stats, and carries the same total-diff reason. Nothing is silently cut.
    for rec in index.values():
        assert rec["omitted"] is True
        assert rec["fragment"] is None
        assert "diff too large" in str(rec["reason"])
    # No body fragments were written at all.
    assert not any((out / "fragments").glob("*.html"))
    # changed-files.json still names every file (existence never dropped).
    changed = {rec["path"] for rec in json.loads((out / "changed-files.json").read_text())}
    assert {"a.py", "b.py"}.issubset(changed)
    # The fallback is an explicit top-level signal the cockpit can banner on.
    data = json.loads((out / "fragments.json").read_text())
    assert data["too_large"] is True
    assert "diff too large" in str(data["too_large_reason"])
    assert data["included_changed_lines"] > 20


def test_normal_branch_is_not_flagged_too_large(repo: Path) -> None:
    collect(repo)
    data = json.loads((repo / ".review-agent" / "fragments.json").read_text())
    assert data["too_large"] is False
    assert data["too_large_reason"] is None


def test_existence_and_stats_survive_every_omission(repo: Path) -> None:
    # The load-bearing invariant: across mixed dispositions, every changed file
    # appears in the index with stats; only bodies (fragments) ever disappear.
    _git(repo, "checkout", "feature")
    _add_lines(repo, "uv.lock", 100, "chore: lock")
    _add_lines(repo, "vendor/x.go", 30, "chore: vendor")
    _add_lines(repo, "keep.py", 5, "feat: keep")
    collect(repo)
    out = repo / ".review-agent"

    changed = [rec["path"] for rec in json.loads((out / "changed-files.json").read_text())]
    index = _fragments_index(out)
    # Index covers exactly the changed files, in order.
    assert list(index.keys()) == changed
    for rec in index.values():
        assert "added" in rec and "deleted" in rec  # stats always present
        if rec["omitted"]:
            assert rec["fragment"] is None
        else:
            assert (out / str(rec["fragment"])).is_file()
