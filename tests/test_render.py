"""End-to-end tests for the deterministic Review Cockpit renderer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from branch_review.analysis import SCHEMA, step_ids
from branch_review.escape import INTERACTIVE_CSP, file_fragment_id, fragment
from branch_review.lint import lint_cockpit
from branch_review.render import RenderError, main, render_cockpit


def _write_run(run_dir: Path) -> dict[str, object]:
    hostile = "Handles <style>body{display:none}</style> as text & nothing else."
    analysis: dict[str, object] = {
        "schema": SCHEMA,
        "title": "Safe HTML narration",
        "intent_summary": hostile,
        "widened_into": [],
        "alignment": {"serves_goal": ["t1"], "drive_by": []},
        "threads": [
            {
                "id": "t1",
                "title": "Render <tags> safely",
                "summary": "One behavior change and its test.",
                "paths": ["src/app.py"],
                "steps": [
                    {
                        "id": "t1.s1",
                        "impact": "behavior-change",
                        "summary": hostile,
                        "detail": "The renderer, not the agent, owns HTML.",
                        "confidence": "high",
                        "why_now": "Start with the observable behavior.",
                        "review_prompts": ["Confirm <style> stays visible text."],
                        "evidence": [{"path": "src/app.py", "hunk": 1, "note": "changed handler"}],
                        "attention_notes": [{"text": "No browser regression test covers <style>."}],
                    },
                    {
                        "id": "t1.s2",
                        "impact": "test-change",
                        "summary": "The test documents the escaped output.",
                        "confidence": "high",
                        "why_now": "Read after the behavior it documents.",
                        "review_prompts": [],
                        "relates_to": ["t1.s1"],
                        "evidence": [{"note": "test assertion in the same hunk"}],
                    },
                ],
            }
        ],
        "test_runner": {
            "runner": "pytest",
            "runner_evidence": "pyproject.toml",
            "command": "pytest",
        },
        "diagrams": [],
    }
    run_dir.mkdir()
    (run_dir / "analysis.json").write_text(json.dumps(analysis), encoding="utf-8")
    (run_dir / "resolved-config.json").write_text(
        json.dumps({"styling": "vendored"}), encoding="utf-8"
    )
    (run_dir / "fragments.html").write_text(
        "\n".join(
            [
                "<!-- fragment: title -->",
                f'<h1 class="cockpit-title">{fragment("feature/safe-html")}</h1>',
                "<!-- fragment: meta -->",
                f'<dl class="cockpit-meta"><dt>Base</dt><dd>{fragment("main")}</dd></dl>',
                "<!-- fragment: goal -->",
                '<blockquote class="goal-text">'
                f"{fragment('Render hostile HTML safely')}</blockquote>",
                "<!-- fragment: files -->",
                "<p>unused by renderer</p>",
                "<!-- fragment: commits -->",
                "<p>unused by renderer</p>",
                "",
            ]
        ),
        encoding="utf-8",
    )

    fid = file_fragment_id("src/app.py")
    anchor = f"hunk-{fid}-1"
    fragment_path = Path("fragments") / f"{fid}.html"
    (run_dir / fragment_path.parent).mkdir()
    diff_html = fragment("@@ -1 +1 @@\n-old\n+new\n")
    (run_dir / fragment_path).write_text(
        f'<div class="file-diff"><section class="hunk" id="{anchor}">'
        f'<pre class="diff">{diff_html}</pre>'
        "</section></div>\n",
        encoding="utf-8",
    )
    manifest = {
        "schema": "review-fragments/0.1",
        "files": [
            {
                "path": "src/app.py",
                "path_html": fragment("src/app.py"),
                "status": "M",
                "id": fid,
                "fragment": str(fragment_path),
                "omitted": False,
                "added": 1,
                "deleted": 1,
                "binary": False,
                "hunks": [{"index": 1, "anchor": anchor, "header_html": fragment("@@")}],
            }
        ],
    }
    (run_dir / "fragments.json").write_text(json.dumps(manifest), encoding="utf-8")
    return analysis


def test_render_cockpit_builds_a_safe_step_document(tmp_path: Path) -> None:
    run_dir = tmp_path / ".review-agent"
    analysis = _write_run(run_dir)

    cockpit = render_cockpit(run_dir)

    html = cockpit.read_text(encoding="utf-8")
    assert cockpit == run_dir / "review.html"
    assert INTERACTIVE_CSP in html
    assert '<h3 class="analysis-title">' in html
    assert "Safe HTML narration" in html
    assert "<style>body{display:none}</style>" not in html
    assert "&lt;style&gt;body{display:none}&lt;/style&gt;" in html
    assert '<span class="thread-impacts attention-behavior-change">' in html
    assert "1 behavior-change · 1 test" in html
    assert '<details class="step" id="t1.s1" data-impact="behavior-change">' in html
    assert '<aside class="attention-note">' in html
    assert 'href="#hunk-' in html
    assert '<details class="file"' in html
    assert (
        lint_cockpit(
            html,
            csp_mode="interactive",
            step_ids=step_ids(analysis),
        )
        == []
    )


def test_render_cockpit_supports_degraded_goal_alignment(tmp_path: Path) -> None:
    run_dir = tmp_path / ".review-agent"
    _write_run(run_dir)
    analysis_path = run_dir / "analysis.json"
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    analysis["alignment"] = None
    analysis_path.write_text(json.dumps(analysis), encoding="utf-8")

    html = render_cockpit(run_dir).read_text(encoding="utf-8")

    assert "Goal alignment unavailable" in html
    assert html.count("</h2>") == html.count("<h2>")


def test_render_cli_persists_non_isolated_analysis_disclosure(tmp_path: Path) -> None:
    run_dir = tmp_path / ".review-agent"
    _write_run(run_dir)

    assert main([str(run_dir), "--analysis-context", "invoking"]) == 0
    html = (run_dir / "review.html").read_text(encoding="utf-8")

    assert '<p class="isolation-note">' in html
    assert "independence was not enforced" in html
    assert json.loads((run_dir / "render-context.json").read_text(encoding="utf-8")) == {
        "analysis_isolated": False
    }


def test_render_cockpit_does_not_replace_output_when_config_is_invalid(tmp_path: Path) -> None:
    run_dir = tmp_path / ".review-agent"
    _write_run(run_dir)
    output = run_dir / "review.html"
    output.write_text("previous cockpit", encoding="utf-8")
    (run_dir / "resolved-config.json").write_text("{broken", encoding="utf-8")

    with pytest.raises(RenderError, match="resolved-config.json is not valid JSON"):
        render_cockpit(run_dir)

    assert output.read_text(encoding="utf-8") == "previous cockpit"


def test_render_cockpit_keeps_omitted_files_visible(tmp_path: Path) -> None:
    run_dir = tmp_path / ".review-agent"
    _write_run(run_dir)
    manifest_path = run_dir / "fragments.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append(
        {
            "path": "uv.lock",
            "path_html": fragment("uv.lock"),
            "status": "M",
            "id": file_fragment_id("uv.lock"),
            "omitted": True,
            "reason": "lockfile <body> omitted",
            "added": 20,
            "deleted": 10,
            "binary": False,
            "hunks": [],
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    html = render_cockpit(run_dir).read_text(encoding="utf-8")

    assert "uv.lock" in html
    assert "lockfile &lt;body&gt; omitted" in html
    assert "2 changed file(s)" in html


def test_render_cockpit_explains_total_diff_fallback(tmp_path: Path) -> None:
    run_dir = tmp_path / ".review-agent"
    _write_run(run_dir)
    manifest_path = run_dir / "fragments.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["too_large"] = True
    manifest["too_large_reason"] = "Diff exceeds <limit>; bodies omitted."
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    html = render_cockpit(run_dir).read_text(encoding="utf-8")

    assert '<p class="too-large">' in html
    assert "Diff exceeds &lt;limit&gt;; bodies omitted." in html
