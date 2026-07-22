"""End-to-end tests for the deterministic Review Cockpit renderer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from branch_review.analysis import SCHEMA, step_ids
from branch_review.coverage import COVERAGE_RULE
from branch_review.escape import INTERACTIVE_CSP, file_fragment_id, fragment
from branch_review.lint import lint_cockpit
from branch_review.render import RenderError, _route_estimate, main, render_cockpit
from branch_review.weight import StepWeight


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
    # Every step carries a derived reading weight on its panel (a number + its Map-dot
    # size tier) and a chip in its summary (document + Stage). This fixture's hunk header
    # is degenerate ("@@") and t1.s2 is note-only, so nothing is measurable: the weight is
    # an approximate floor of 0, shown as "unsized" — never a "~0 lines · <1 min" budget
    # that would read as negligible, and its dot is bucketed "unsized", not the w1 smallest.
    assert (
        '<details class="step" id="t1.s1" data-impact="behavior-change"'
        ' data-core="true" data-weight="0" data-weight-bucket="unsized">' in html
    )
    # A non-core step (test-change) carries no data-core — the deck reads its absence.
    assert (
        '<details class="step" id="t1.s2" data-impact="test-change"'
        ' data-weight="0" data-weight-bucket="unsized">' in html
    )
    assert '<span class="chip weight weight-approx"' in html
    assert ">unsized</span>" in html
    assert "~0 lines" not in html and "<1 min" not in html
    # Thread + route rollups: nothing measurable → an honest "not sized", no faked time.
    assert '<section class="thread" id="t1" data-weight="0">' in html
    assert '<span class="thread-weight" data-weight="0"' in html
    assert ">unknown</span>" in html
    assert (
        '<li class="route-weight">Reading weight: not sized — '
        "the cited evidence carries no measurable lines</li>" in html
    )
    # An unmeasurable route stamps no route-budget attributes — a subset of nothing is
    # still nothing, so the deck's selector degrades rather than showing a faked budget.
    assert "data-core-budget" not in html and "data-full-budget" not in html
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


def test_render_cockpit_derives_reading_weight_from_real_hunks(tmp_path: Path) -> None:
    run_dir = tmp_path / ".review-agent"
    analysis = _write_run(run_dir)
    # Give the cited hunk a real header AND the collector's exact line count. The exact
    # count (24) intentionally differs from the header's max(18, 21) = 21, proving the
    # renderer uses the exact count, not the undercounting header (issue #100).
    manifest_path = run_dir / "fragments.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["hunks"][0]["header_html"] = fragment("@@ -1,18 +1,21 @@")
    manifest["files"][0]["hunks"][0]["lines"] = 24
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    html = render_cockpit(run_dir).read_text(encoding="utf-8")

    # t1.s1 is sized exactly from its hunk — a plain (non-floor) chip showing 24, not 21 —
    # and 24 lines lands in the w2 size tier (the Python-owned bucket policy).
    s1_tag = (
        '<details class="step" id="t1.s1" data-impact="behavior-change"'
        ' data-core="true" data-weight="24" data-weight-bucket="w2">'
    )
    assert s1_tag in html
    assert '<span class="chip weight" title=' in html
    assert "24 lines" in html
    # t1.s2 is note-only, so the line count becomes an explicit floor (≥ the 24 measured,
    # with the unmeasured note on top) while the time stays a rough "~" estimate — never a
    # rounded-up "≥ min" bound.
    assert '<section class="thread" id="t1" data-weight="24">' in html
    assert '<span class="thread-weight" data-weight="24"' in html
    # The thread tooltip states the reading-pace heuristic too, like the L0 route estimate.
    assert 'title="≥24 lines to read (~25 lines/min)">~1 min</span>' in html
    # This fixture abridges (t1.s1 is behavior-change/core, t1.s2 is test-change/non-core),
    # so L0 states both budgets (core-first) with the full line count carried along, and the
    # section stamps the per-route budgets the deck's route selector relays (issue #101).
    assert (
        "Reading weight: ~1 min core · ~1 min full at reading pace "
        "(~25 lines/min; full is ≥24 lines)" in html
    )
    # The coverage headline rides on L0 too (issue #104): this fixture's one hunk is
    # anchored by t1.s1, so narration accounts for all of it.
    assert (
        '<section class="l0" data-core-budget="~1 min" data-full-budget="~1 min"'
        ' data-coverage-label="1 of 1 hunk narrated">' in html
    )
    assert lint_cockpit(html, csp_mode="interactive", step_ids=step_ids(analysis)) == []


def test_render_cockpit_stamps_run_identity_meta(tmp_path: Path) -> None:
    run_dir = tmp_path / ".review-agent"
    analysis = _write_run(run_dir)
    (run_dir / "context.json").write_text(
        json.dumps(
            {"head_sha": "abc123", "merge_base": "def456", "generated_at": "2026-07-18T06:00:00Z"}
        ),
        encoding="utf-8",
    )

    html = render_cockpit(run_dir).read_text(encoding="utf-8")

    # The run identity pins the store to this exact run — head, base, and the collection
    # timestamp — so a regenerated run's persisted deck state self-invalidates instead of
    # leaking across the clean break, even when the commit range is unchanged.
    assert '<meta name="brc-run" content="abc123:def456:2026-07-18T06:00:00Z">' in html
    assert lint_cockpit(html, csp_mode="interactive", step_ids=step_ids(analysis)) == []


def test_render_cockpit_run_identity_distinguishes_same_commit_regenerations(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / ".review-agent"
    _write_run(run_dir)
    ctx = run_dir / "context.json"
    same_range = {"head_sha": "abc123", "merge_base": "def456"}

    ctx.write_text(json.dumps({**same_range, "generated_at": "first"}), encoding="utf-8")
    first = render_cockpit(run_dir).read_text(encoding="utf-8")
    ctx.write_text(json.dumps({**same_range, "generated_at": "second"}), encoding="utf-8")
    second = render_cockpit(run_dir).read_text(encoding="utf-8")

    # Same head and merge-base, different collection → different identity, so a stale
    # positional step id from the first run cannot restore onto the second (finding #2).
    assert 'content="abc123:def456:first"' in first
    assert 'content="abc123:def456:second"' in second


def test_render_cockpit_omits_run_meta_without_context(tmp_path: Path) -> None:
    run_dir = tmp_path / ".review-agent"
    analysis = _write_run(run_dir)  # no context.json — a degraded run

    html = render_cockpit(run_dir).read_text(encoding="utf-8")

    # Absence of the run identity is honest: the store simply stays inert rather than
    # keying persistence to a fabricated identity.
    assert 'name="brc-run"' not in html
    assert lint_cockpit(html, csp_mode="interactive", step_ids=step_ids(analysis)) == []


def test_render_cockpit_omits_run_meta_without_generated_at(tmp_path: Path) -> None:
    run_dir = tmp_path / ".review-agent"
    _write_run(run_dir)
    # head_sha + merge_base but no generated_at: any identity we could form here is
    # reusable across a same-commit regeneration, so the meta must be omitted (fail
    # safe) rather than fall back to a weaker identity that reopens finding #2.
    (run_dir / "context.json").write_text(
        json.dumps({"head_sha": "abc123", "merge_base": "def456"}), encoding="utf-8"
    )

    html = render_cockpit(run_dir).read_text(encoding="utf-8")

    assert 'name="brc-run"' not in html


def test_render_cockpit_run_meta_without_merge_base(tmp_path: Path) -> None:
    run_dir = tmp_path / ".review-agent"
    _write_run(run_dir)
    # head_sha + generated_at (no merge_base) is a complete identity — the load-bearing
    # timestamp is present, so the meta is emitted without the optional base segment.
    (run_dir / "context.json").write_text(
        json.dumps({"head_sha": "abc123", "generated_at": "2026-07-18T06:00:00Z"}),
        encoding="utf-8",
    )

    html = render_cockpit(run_dir).read_text(encoding="utf-8")

    assert '<meta name="brc-run" content="abc123:2026-07-18T06:00:00Z">' in html


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


_S1_MARGIN = (
    '<div class="hunk-narration"><span class="narration-label">narrated by</span> '
    '<a class="narrating-step" href="#t1.s1">t1.s1</a></div>'
)
_UNNARRATED_MARGIN = (
    '<div class="hunk-narration hunk-unnarrated">'
    '<span class="unnarrated-marker">un-narrated</span></div>'
)


def _rewrite_app_fragment(run_dir: Path, sections: str) -> str:
    """Rewrite the src/app.py diff fragment with the given hunk ``<section>``s; return fid."""
    fid = file_fragment_id("src/app.py")
    (run_dir / "fragments" / f"{fid}.html").write_text(
        f'<div class="file-diff">{sections}</div>\n', encoding="utf-8"
    )
    return fid


def test_render_cockpit_narrates_hunks_in_the_margin(tmp_path: Path) -> None:
    run_dir = tmp_path / ".review-agent"
    analysis = _write_run(run_dir)  # t1.s1 anchors src/app.py hunk 1

    html = render_cockpit(run_dir).read_text(encoding="utf-8")

    # The hunk the step anchors names its narrating step in the margin, linked to the
    # step panel — the reverse of the forward evidence link (issue #103).
    fid = file_fragment_id("src/app.py")
    assert f'<section class="hunk" id="hunk-{fid}-1">{_S1_MARGIN}' in html
    # The lone narrated hunk carries no un-narrated marker.
    assert "hunk-unnarrated" not in html
    assert lint_cockpit(html, csp_mode="interactive", step_ids=step_ids(analysis)) == []


def test_render_cockpit_marks_un_narrated_hunks_neutrally(tmp_path: Path) -> None:
    run_dir = tmp_path / ".review-agent"
    analysis = _write_run(run_dir)
    # Add a second hunk to src/app.py that no step anchors — it stays visibly bare.
    fid = file_fragment_id("src/app.py")
    diff_html = fragment("@@ -1 +1 @@\n-old\n+new\n")
    _rewrite_app_fragment(
        run_dir,
        f'<section class="hunk" id="hunk-{fid}-1"><pre class="diff">{diff_html}</pre></section>'
        f'<section class="hunk" id="hunk-{fid}-2"><pre class="diff">{diff_html}</pre></section>',
    )
    manifest_path = run_dir / "fragments.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["hunks"].append(
        {"index": 2, "anchor": f"hunk-{fid}-2", "header_html": fragment("@@")}
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    html = render_cockpit(run_dir).read_text(encoding="utf-8")

    assert f'<section class="hunk" id="hunk-{fid}-1">{_S1_MARGIN}' in html
    # Hunk 2 has no narrating step → the neutral un-narrated marker (not a warn/diff colour).
    assert f'<section class="hunk" id="hunk-{fid}-2">{_UNNARRATED_MARGIN}' in html
    assert lint_cockpit(html, csp_mode="interactive", step_ids=step_ids(analysis)) == []


def test_render_cockpit_lists_every_step_that_narrates_one_hunk(tmp_path: Path) -> None:
    run_dir = tmp_path / ".review-agent"
    _write_run(run_dir)
    # Make t1.s2 also anchor src/app.py hunk 1, so two steps narrate the one hunk.
    analysis_path = run_dir / "analysis.json"
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    analysis["threads"][0]["steps"][1]["evidence"] = [
        {"note": "test assertion in the same hunk"},
        {"path": "src/app.py", "hunk": 1},
    ]
    analysis_path.write_text(json.dumps(analysis), encoding="utf-8")

    html = render_cockpit(run_dir).read_text(encoding="utf-8")

    # Both narrating steps are listed, in Review Route (first-appearance) order.
    fid = file_fragment_id("src/app.py")
    assert (
        f'<section class="hunk" id="hunk-{fid}-1"><div class="hunk-narration">'
        '<span class="narration-label">narrated by</span> '
        '<a class="narrating-step" href="#t1.s1">t1.s1</a> '
        '<a class="narrating-step" href="#t1.s2">t1.s2</a></div>' in html
    )
    assert lint_cockpit(html, csp_mode="interactive", step_ids=step_ids(analysis)) == []


def test_render_cockpit_counts_attention_note_evidence_as_narration(tmp_path: Path) -> None:
    run_dir = tmp_path / ".review-agent"
    _write_run(run_dir)
    # A step whose ONLY hunk citation lives in an attention note. That evidence renders as
    # a working `.evidence-list` link and clones into the deck, so the hunk is narrated —
    # it must not read "un-narrated" beside the note that links it (ADR-0016 evidence).
    analysis_path = run_dir / "analysis.json"
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    step = analysis["threads"][0]["steps"][0]
    step["evidence"] = [{"note": "no hunk cited directly"}]
    step["attention_notes"] = [
        {"text": "flagged here", "evidence": [{"path": "src/app.py", "hunk": 1}]}
    ]
    analysis_path.write_text(json.dumps(analysis), encoding="utf-8")

    html = render_cockpit(run_dir).read_text(encoding="utf-8")

    fid = file_fragment_id("src/app.py")
    assert f'<section class="hunk" id="hunk-{fid}-1">{_S1_MARGIN}' in html
    assert "hunk-unnarrated" not in html
    assert lint_cockpit(html, csp_mode="interactive", step_ids=step_ids(analysis)) == []


def test_render_cockpit_sizes_attention_note_evidence_in_reading_weight(tmp_path: Path) -> None:
    run_dir = tmp_path / ".review-agent"
    _write_run(run_dir)
    # Give the cited hunk a real line count, then move t1.s1's hunk citation into an
    # attention note (main evidence left note-only). The reading weight must still size the
    # attention-note hunk — the narration index counts it, so the weight must too (#103).
    manifest_path = run_dir / "fragments.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["hunks"][0]["lines"] = 24
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    analysis_path = run_dir / "analysis.json"
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    step = analysis["threads"][0]["steps"][0]
    step["evidence"] = [{"note": "prose only"}]
    step["attention_notes"] = [
        {"text": "the real hunk", "evidence": [{"path": "src/app.py", "hunk": 1}]}
    ]
    analysis_path.write_text(json.dumps(analysis), encoding="utf-8")

    html = render_cockpit(run_dir).read_text(encoding="utf-8")

    # The attention-note hunk contributes its 24 lines — no longer data-weight="0"/unsized;
    # the note keeps it an approximate floor, so the chip reads "≥24 lines".
    assert (
        '<details class="step" id="t1.s1" data-impact="behavior-change"'
        ' data-core="true" data-weight="24" data-weight-bucket="w2">' in html
    )
    assert "≥24 lines" in html
    assert lint_cockpit(html, csp_mode="interactive", step_ids=step_ids(analysis)) == []


def test_render_cockpit_annotates_file_level_refs_on_the_header(tmp_path: Path) -> None:
    run_dir = tmp_path / ".review-agent"
    _write_run(run_dir)
    # Repoint t1.s2 at a file-level ref (no hunk) — it narrates the file, not a hunk.
    analysis_path = run_dir / "analysis.json"
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    analysis["threads"][0]["steps"][1]["evidence"] = [{"path": "src/app.py"}]
    analysis_path.write_text(json.dumps(analysis), encoding="utf-8")

    html = render_cockpit(run_dir).read_text(encoding="utf-8")

    # The file-level ref annotates the file-fragment header, not any hunk…
    assert (
        '<span class="file-narration"><span class="narration-label">narrated by</span> '
        '<a class="narrating-step" href="#t1.s2">t1.s2</a></span>' in html
    )
    # …and hunk 1's own margin still lists only its hunk-level narrator (t1.s1), never t1.s2.
    fid = file_fragment_id("src/app.py")
    assert f'<section class="hunk" id="hunk-{fid}-1">{_S1_MARGIN}' in html
    assert lint_cockpit(html, csp_mode="interactive", step_ids=step_ids(analysis)) == []


def test_render_cockpit_annotates_file_level_ref_on_an_omitted_file(tmp_path: Path) -> None:
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
            "reason": "lockfile body omitted",
            "added": 20,
            "deleted": 10,
            "binary": False,
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    # A step file-level-refs the omitted file — an omitted body has no hunks, so it can
    # only be annotated at the file-fragment level (never a per-hunk marker).
    analysis_path = run_dir / "analysis.json"
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    analysis["threads"][0]["steps"][1]["evidence"] = [{"path": "uv.lock"}]
    analysis_path.write_text(json.dumps(analysis), encoding="utf-8")

    html = render_cockpit(run_dir).read_text(encoding="utf-8")

    assert (
        '<span class="file-narration"><span class="narration-label">narrated by</span> '
        '<a class="narrating-step" href="#t1.s2">t1.s2</a></span>' in html
    )
    # The omitted file has no hunk, so it never carries an un-narrated hunk marker.
    omitted_fid = file_fragment_id("uv.lock")
    assert f'id="hunk-{omitted_fid}-' not in html
    assert lint_cockpit(html, csp_mode="interactive", step_ids=step_ids(analysis)) == []


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


def _add_second_bare_hunk(run_dir: Path, header: str = "@@ -5,2 +5,3 @@") -> str:
    """Give src/app.py a second hunk (index 2) that no step anchors; return its anchor."""
    fid = file_fragment_id("src/app.py")
    diff_html = fragment("@@ -1 +1 @@\n-old\n+new\n")
    _rewrite_app_fragment(
        run_dir,
        f'<section class="hunk" id="hunk-{fid}-1"><pre class="diff">{diff_html}</pre></section>'
        f'<section class="hunk" id="hunk-{fid}-2"><pre class="diff">{diff_html}</pre></section>',
    )
    manifest_path = run_dir / "fragments.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["hunks"].append(
        {"index": 2, "anchor": f"hunk-{fid}-2", "header_html": fragment(header)}
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return f"hunk-{fid}-2"


def test_render_cockpit_shows_a_fully_narrated_coverage_meter(tmp_path: Path) -> None:
    run_dir = tmp_path / ".review-agent"
    analysis = _write_run(run_dir)  # the one hunk is anchored by t1.s1 → nothing bare

    html = render_cockpit(run_dir).read_text(encoding="utf-8")

    # The headline is stamped on L0 for the Map to relay, and stated in the meter with the
    # every-hunk-narrated close. Nothing is bare → no queue, and no link that would dangle.
    assert 'data-coverage-label="1 of 1 hunk narrated"' in html
    assert (
        '<li class="coverage-meter" title=' in html
        and "Narrated-hunk coverage: 1 of 1 hunk narrated (100%) — "
        "every changed hunk is narrated." in html
    )
    assert 'id="unnarrated-changes"' not in html
    assert 'href="#unnarrated-changes"' not in html
    assert lint_cockpit(html, csp_mode="interactive", step_ids=step_ids(analysis)) == []


def test_render_cockpit_generates_the_unnarrated_queue(tmp_path: Path) -> None:
    run_dir = tmp_path / ".review-agent"
    analysis = _write_run(run_dir)
    _add_second_bare_hunk(run_dir)  # hunk 1 narrated by t1.s1, hunk 2 bare

    html = render_cockpit(run_dir).read_text(encoding="utf-8")

    fid = file_fragment_id("src/app.py")
    # The L0 meter reports the honest fraction and links into the queue (no file-blanket).
    assert 'data-coverage-label="1 of 2 hunks narrated"' in html
    assert (
        "Narrated-hunk coverage: 1 of 2 hunks narrated (50%) · 1 un-narrated — "
        '<a href="#unnarrated-changes">review</a>' in html
    )
    # The queue exists, states the rule, and lists the bare hunk grouped under its file with
    # a working L3 link + its header; the narrated hunk 1 is not listed.
    assert '<section class="unnarrated-changes" id="unnarrated-changes">' in html
    assert COVERAGE_RULE in html  # the counting rule is stated in the UI
    assert f'<li><a href="#hunk-{fid}-2">hunk 2</a> <span class="hunk-header">' in html
    assert ">hunk 1</a>" not in html  # hunk 1 is narrated, never queued
    assert lint_cockpit(html, csp_mode="interactive", step_ids=step_ids(analysis)) == []


def test_render_cockpit_notes_file_blanket_narration_in_the_queue(tmp_path: Path) -> None:
    run_dir = tmp_path / ".review-agent"
    _write_run(run_dir)
    _add_second_bare_hunk(run_dir)  # hunk 2 bare
    # Repoint t1.s2 at a file-level ref: it blankets src/app.py, so hunk 2 is un-narrated
    # but under a file-level citation — counted distinctly, and noted beside the bare hunk.
    analysis_path = run_dir / "analysis.json"
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    analysis["threads"][0]["steps"][1]["evidence"] = [{"path": "src/app.py"}]
    analysis_path.write_text(json.dumps(analysis), encoding="utf-8")

    html = render_cockpit(run_dir).read_text(encoding="utf-8")

    # The un-narrated count carries the distinct file-blanket refinement.
    assert (
        "1 of 2 hunks narrated (50%) · 1 un-narrated (1 under a file-level citation) — " in html
    )
    # The queue's file head notes the blanket narrator (a staging narrating-step link).
    assert (
        '<span class="file-blanket-note">file-level narration: '
        '<a class="narrating-step" href="#t1.s2">t1.s2</a></span>' in html
    )
    assert lint_cockpit(html, csp_mode="interactive", step_ids=step_ids(analysis)) == []


def test_render_cockpit_never_counts_or_queues_an_omitted_file(tmp_path: Path) -> None:
    run_dir = tmp_path / ".review-agent"
    _write_run(run_dir)
    _add_second_bare_hunk(run_dir)  # src/app.py: hunk 1 narrated, hunk 2 bare
    manifest_path = run_dir / "fragments.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append(
        {
            "path": "uv.lock",
            "path_html": fragment("uv.lock"),
            "status": "M",
            "id": file_fragment_id("uv.lock"),
            "omitted": True,
            "reason": "lockfile body omitted",
            "added": 400,
            "deleted": 10,
            "binary": False,
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    # Even a file-level ref at the omitted file cannot count it — it has no hunks.
    analysis_path = run_dir / "analysis.json"
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    analysis["threads"][0]["steps"][1]["evidence"] = [{"path": "uv.lock"}]
    analysis_path.write_text(json.dumps(analysis), encoding="utf-8")

    html = render_cockpit(run_dir).read_text(encoding="utf-8")

    # Total counts only src/app.py's two real hunks; the lockfile is shown (existence +
    # stats, nothing-hidden) but never counted and never queued.
    assert 'data-coverage-label="1 of 2 hunks narrated"' in html
    assert "uv.lock" in html and "lockfile body omitted" in html
    unnarrated = html[html.index('id="unnarrated-changes"') :]
    assert "uv.lock" not in unnarrated
    assert lint_cockpit(html, csp_mode="interactive", step_ids=step_ids(analysis)) == []


@pytest.mark.parametrize(
    ("route_weight", "core_weight", "abridged", "expected_text", "expected_attrs"),
    [
        # Unmeasurable route → one honest "not sized" line, no per-route split, no attrs.
        (
            StepWeight(0, True),
            StepWeight(0, True),
            True,
            "Reading weight: not sized — the cited evidence carries no measurable lines",
            "",
        ),
        # Measured but not abridged (core == full) → the single original budget line.
        (
            StepWeight(100, False),
            StepWeight(100, False),
            False,
            "Reading weight: 100 lines · ~4 min at reading pace (~25 lines/min)",
            "",
        ),
        # Abridged + measured → both budgets (core-first) with the full line count carried
        # along, plus the per-route data attributes the deck's route selector relays.
        (
            StepWeight(600, False),
            StepWeight(200, False),
            True,
            "Reading weight: ~8 min core · ~24 min full at reading pace "
            "(~25 lines/min; full is 600 lines)",
            ' data-core-budget="~8 min" data-full-budget="~24 min"',
        ),
        # Abridged, route measurable, but the core subset alone is unsized (note-only core
        # evidence beside measurable non-core steps): core reads honestly as "unknown" and
        # no data-core-budget is stamped — the Core button degrades to no sub-label while
        # Full keeps its budget, never a fabricated "unknown" attribute.
        (
            StepWeight(100, True),
            StepWeight(0, True),
            True,
            "Reading weight: unknown core · ~4 min full at reading pace "
            "(~25 lines/min; full is ≥100 lines)",
            ' data-full-budget="~4 min"',
        ),
    ],
    ids=["unsized-route", "not-abridged", "abridged-measured", "abridged-core-unsized"],
)
def test_route_estimate_states_budgets_honestly(
    route_weight: StepWeight,
    core_weight: StepWeight,
    abridged: bool,
    expected_text: str,
    expected_attrs: str,
) -> None:
    """_route_estimate splits the L0 budget only for an abridged, measured review."""
    text, attrs = _route_estimate(route_weight, core_weight, abridged)
    assert text == expected_text
    assert attrs == expected_attrs
