"""Deterministically render ``analysis.json`` into the Review Cockpit document.

The isolated narrator owns the structured analysis. This module owns its HTML
representation: callers provide a collected ``.review-agent`` directory and receive
one escaped, structurally linted ``review.html``. No caller interpolates narrator
prose or reconstructs the L0-L3 document shape.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

from branch_review.analysis import step_ids, validate_analysis
from branch_review.escape import (
    INTERACTIVE_CSP,
    QA_SEAM_CLOSE,
    QA_SEAM_OPEN,
    escape_text,
    evidence_seam_markers,
    fragment,
    hunk_section_open,
)
from branch_review.lint import lint_cockpit
from branch_review.weight import (
    LINES_PER_MINUTE,
    StepWeight,
    dot_bucket,
    lines_label,
    minutes_label,
    rollup,
    step_weight,
)

DEFAULT_RUN_DIR = Path(".review-agent")
RENDER_CONTEXT_NAME = "render-context.json"

_IMPACT_LABELS = {
    "behavior-change": "behavior-change",
    "behavior-preserving": "preserving",
    "test-change": "test",
    "mechanical-change": "mechanical",
    "unknown-impact": "unknown",
}
_IMPACT_ORDER = tuple(_IMPACT_LABELS)

# The Behavior Impacts that earn a first-pass read — an observable behavior change, or
# an impact the narrator could not pin down. This one tuple is the single owner of that
# partition: it drives the abridged "core-first" route (issue #101), the per-step
# ``data-core`` flag the deck relays (never re-deriving the set in JS — the derived-over-
# authored posture, exactly like ``data-weight-bucket``), and :func:`_attention_impact`'s
# thread ranking. Ordered by attention priority (``unknown-impact`` outranks
# ``behavior-change``) so it doubles as that ranking. Every other impact
# (preserving/test/mechanical) waits in the full route, one toggle away — nothing is
# hidden, only sequenced.
CORE_IMPACTS = ("unknown-impact", "behavior-change")


class RenderError(ValueError):
    """The collected run cannot be rendered into a valid cockpit."""


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RenderError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RenderError(f"{path} is not valid JSON: {exc}") from exc


def _mapping(value: object, what: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RenderError(f"{what} must be an object")
    return value


def _items(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _alignment(analysis: Mapping[str, object]) -> Mapping[str, object] | None:
    value = analysis.get("alignment")
    return value if isinstance(value, Mapping) else None


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _isolation_note(run_dir: Path) -> str:
    path = run_dir / RENDER_CONTEXT_NAME
    if not path.exists():
        return ""
    context = _mapping(_load_json(path), RENDER_CONTEXT_NAME)
    isolated = context.get("analysis_isolated")
    if not isinstance(isolated, bool):
        raise RenderError(f"{RENDER_CONTEXT_NAME} analysis_isolated must be a boolean")
    if isolated:
        return ""
    return (
        '<p class="isolation-note">'
        + fragment(
            "Analysis was formed in the invoking session; independence was not enforced "
            "by construction on this platform."
        )
        + "</p>"
    )


def _run_meta(run_dir: Path) -> str:
    """A ``<meta name="brc-run">`` stamping this run's diff identity, or ``""``.

    The served cockpit's client store (``assets/app.js``, issue #112) keys its
    persisted deck state by the artifact path *and* this identity, so a regenerated
    run self-invalidates rather than restoring stale state across the clean break.

    The identity is the collector's ``head_sha``, then ``merge_base`` (a base that
    advanced under a fixed HEAD is a new run), then ``generated_at``. The timestamp is
    **load-bearing and required**: a review *regenerated on the same commit range* keeps
    the same head and merge-base, but the narrator re-mints Review Step ids positionally
    each run, so a stale ``t1.s2`` draft/position could otherwise restore onto a step
    that now means something else. The collector re-stamps ``generated_at`` every
    collection, making each regeneration a distinct identity; a seam-only live-evidence
    injection never re-collects, so the meta — and the identity — stays stable across the
    very reload the store exists to survive.

    Because ``generated_at`` is what carries that guarantee, the meta is emitted only
    when it (and ``head_sha``) are present: any identity we could form without it —
    ``head`` or ``head:merge_base`` — is reusable across a same-commit regeneration, so
    keying persistence to one would reopen exactly that hazard. When it is absent (or
    ``context.json`` is), the meta is omitted and the store stays inert (absence
    discards) rather than fall back to a weaker, reusable identity.
    """
    path = run_dir / "context.json"
    if not path.exists():
        return ""
    context = _load_json(path)
    if not isinstance(context, Mapping):
        return ""
    head = context.get("head_sha")
    generated_at = context.get("generated_at")
    if not isinstance(head, str) or not head:
        return ""
    if not isinstance(generated_at, str) or not generated_at:
        return ""  # the load-bearing part is missing → fail safe, keep the store inert
    parts = [head]
    merge_base = context.get("merge_base")
    if isinstance(merge_base, str) and merge_base:
        parts.append(merge_base)
    parts.append(generated_at)
    return f'<meta name="brc-run" content="{escape_text(":".join(parts))}">'


def _fragment_block(source: str, name: str) -> str:
    marker = f"<!-- fragment: {name} -->"
    start = source.find(marker)
    if start < 0:
        raise RenderError(f"fragments.html has no {name!r} block")
    body_start = start + len(marker)
    next_marker = source.find("<!-- fragment:", body_start)
    body_end = len(source) if next_marker < 0 else next_marker
    return source[body_start:body_end].strip()


def _impact_counts(steps: Sequence[Mapping[str, object]]) -> Counter[str]:
    return Counter(_text(step.get("impact")) for step in steps)


def _impact_summary(steps: Sequence[Mapping[str, object]]) -> str:
    counts = _impact_counts(steps)
    return " · ".join(
        f"{counts[impact]} {_IMPACT_LABELS[impact]}" for impact in _IMPACT_ORDER if counts[impact]
    )


def _attention_impact(steps: Sequence[Mapping[str, object]]) -> str | None:
    counts = _impact_counts(steps)
    # CORE_IMPACTS is in attention-priority order, so the first present one wins.
    for impact in CORE_IMPACTS:
        if counts[impact]:
            return impact
    return None


def _manifest_files(manifest: Mapping[str, object]) -> list[Mapping[str, object]]:
    files = manifest.get("files")
    if not isinstance(files, list) or any(not isinstance(item, Mapping) for item in files):
        raise RenderError("fragments.json files must be a list of objects")
    return [item for item in files if isinstance(item, Mapping)]


def _file_by_path(files: Sequence[Mapping[str, object]]) -> dict[str, Mapping[str, object]]:
    indexed: dict[str, Mapping[str, object]] = {}
    for entry in files:
        path = _text(entry.get("path"))
        if not path:
            raise RenderError("fragment entry has no path")
        indexed[path] = entry
    return indexed


def _path_html(entry: Mapping[str, object]) -> str:
    rendered = entry.get("path_html")
    if not isinstance(rendered, str) or not rendered:
        raise RenderError(f"fragment entry for {_text(entry.get('path'))!r} has no path_html")
    return rendered


def _file_anchor(entry: Mapping[str, object]) -> str:
    fid = _text(entry.get("id"))
    if not fid:
        raise RenderError(f"fragment entry for {_text(entry.get('path'))!r} has no id")
    return f"file-{fid}"


def _hunk_anchor(entry: Mapping[str, object], index: object) -> str:
    if isinstance(index, bool) or not isinstance(index, int):
        raise RenderError(f"hunk index must be an integer, got {index!r}")
    for hunk in _items(entry.get("hunks")):
        if hunk.get("index") == index:
            anchor = _text(hunk.get("anchor"))
            if anchor:
                return anchor
    raise RenderError(f"{_text(entry.get('path'))!r} has no hunk {index}")


def _step_evidence(step: Mapping[str, object]) -> list[Mapping[str, object]]:
    """All of a step's evidence refs — its main ``evidence`` plus each attention note's
    own ``evidence`` (schema 0.4 / ADR-0016), in DOM order (main first, then the notes,
    matching :func:`_render_step`).

    This is the *whole* of what the step cites: attention-note evidence renders as the
    same ``.evidence-list`` links, clones into the deck's inline evidence, and is folded
    into the reverse narration index — so it must also be sized. Feeding this one set to
    both :func:`_narration_index` and :func:`branch_review.weight.step_weight` keeps them
    consistent: a hunk a step cites only through an attention note is narrated *and* sized,
    never counted as narration while the weight reads ``unsized``. ``step_weight`` dedupes
    within a step, so a hunk cited in both the main list and a note is sized once.
    """
    refs = list(_items(step.get("evidence")))
    for note in _items(step.get("attention_notes")):
        refs.extend(_items(note.get("evidence")))
    return refs


def _narration_index(
    threads: Sequence[Mapping[str, object]],
    files_by_path: Mapping[str, Mapping[str, object]],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """The reverse hunk↔step join (issue #103): which Review Step(s) narrate each hunk/file.

    The forward direction (:func:`_render_evidence_ref`) points a step at its evidence;
    this inverts it so an L3 hunk can name the step(s) that narrate it — upgrading the
    nothing-hidden invariant from "reachable at L3" to "visibly accounted for by
    narration". Returns ``(by_hunk, by_file)``:

    * ``by_hunk`` maps a hunk element id (``hunk-<fid>-<n>``) to the step ids whose
      evidence anchors *that exact hunk* (a ``{path, hunk}`` ref), in first-appearance
      (Review Route) order, deduped.
    * ``by_file`` maps a changed file's path to the step ids that reference it at the
      *file* level (a ``{path}`` ref with no ``hunk``) — annotated on the file header,
      never on a hunk: a file-level ref narrates the file broadly, so it does not mark
      any one hunk as narrated (those hunks stay individually un-narrated).

    A step's evidence is *all* of it (:func:`_step_evidence`): its main ``evidence`` and
    each attention note's own ``evidence``, so a hunk a step cites only through a note is
    narrated by that step — never read "un-narrated" beside the very note that links it.

    A ref that does not resolve (unknown path, or a hunk index the file never emitted)
    is skipped here: the forward render (:func:`_render_thread`) validates every ref and
    raises before the files section renders, so a bad ref fails the build there — this
    index only needs the refs that resolve.
    """
    by_hunk: dict[str, list[str]] = {}
    by_file: dict[str, list[str]] = {}
    for thread in threads:
        for step in _items(thread.get("steps")):
            sid = _text(step.get("id"))
            if not sid:
                continue
            for ref in _step_evidence(step):
                path = ref.get("path")
                if not isinstance(path, str):
                    continue
                entry = files_by_path.get(path)
                if entry is None:
                    continue
                if "hunk" in ref:
                    try:
                        key = _hunk_anchor(entry, ref["hunk"])
                    except RenderError:
                        continue
                    bucket = by_hunk.setdefault(key, [])
                else:
                    bucket = by_file.setdefault(path, [])
                if sid not in bucket:
                    bucket.append(sid)
    return by_hunk, by_file


def _narrating_step_links(step_ids: Sequence[str]) -> str:
    """``narrated by <step> · <step>`` — the linked names of a hunk's/file's narrators.

    Each link carries ``class="narrating-step"``: the marker the served deck's click
    handler stages on and the Cockpit Linter checks resolves to a real step id (#103).
    The step id is both the jump target (``<details class="step" id=…>``) and its visible
    label, exactly like a ``relates_to`` link.
    """
    links = " ".join(
        f'<a class="narrating-step" href="#{escape_text(sid)}">{escape_text(sid)}</a>'
        for sid in step_ids
    )
    return f'<span class="narration-label">narrated by</span> {links}'


def _hunk_margin(step_ids: Sequence[str] | None) -> str:
    """The margin for one L3 hunk: its narrating step(s), or the neutral un-narrated marker.

    A hunk **no** step anchors carries a distinct ``un-narrated`` marker — visible
    bareness, a narration *state* and never a judgment, so it stays in the muted palette
    with a glyph+word (judgment-color discipline, ADR-0014/0016), never a warn/alarm tint.
    """
    if step_ids:
        return f'<div class="hunk-narration">{_narrating_step_links(step_ids)}</div>'
    return (
        '<div class="hunk-narration hunk-unnarrated">'
        '<span class="unnarrated-marker">un-narrated</span>'
        "</div>"
    )


def _annotate_hunks(
    fragment_html: str,
    entry: Mapping[str, object],
    by_hunk: Mapping[str, Sequence[str]],
) -> str:
    """Splice each hunk's narrating-step margin into a file's pre-escaped diff fragment.

    The fragment is opaque escaped HTML the collector wrote; its one structured handle is
    the per-hunk ``<section class="hunk" id=…>`` whose id the manifest also carries (the
    Hunk Anchorer, :func:`branch_review.escape.file_diff_fragment`). For every hunk in the
    manifest we insert a margin — the narrating step(s) or the neutral un-narrated marker —
    directly after that section's opening tag, matched via the shared
    :func:`branch_review.escape.hunk_section_open` so the writer and this splice can't
    desync. The match is the collision-free hunk id (not a parse) and lands outside the
    ``<pre>``'s untrusted region, so the Escape Boundary is untouched.
    """
    for hunk in _items(entry.get("hunks")):
        anchor = _text(hunk.get("anchor"))
        if not anchor:
            continue
        opening = hunk_section_open(anchor)
        margin = _hunk_margin(by_hunk.get(anchor))
        fragment_html = fragment_html.replace(opening, opening + margin, 1)
    return fragment_html


def _render_evidence_ref(
    ref: Mapping[str, object], files_by_path: Mapping[str, Mapping[str, object]]
) -> str:
    parts: list[str] = []
    path = ref.get("path")
    if isinstance(path, str):
        entry = files_by_path.get(path)
        if entry is None:
            raise RenderError(f"analysis evidence path {path!r} is not in fragments.json")
        anchor = _file_anchor(entry)
        if "hunk" in ref:
            anchor = _hunk_anchor(entry, ref["hunk"])
        parts.append(f'<a href="#{escape_text(anchor)}">{_path_html(entry)}</a>')
    note = ref.get("note")
    if isinstance(note, str):
        prefix = " — " if parts else ""
        parts.append(f'{prefix}<span class="note">{fragment(note)}</span>')
    return "".join(parts)


def _render_evidence_list(
    evidence: object, files_by_path: Mapping[str, Mapping[str, object]]
) -> str:
    refs = _items(evidence)
    return (
        '<ul class="evidence-list">'
        + "".join(f"<li>{_render_evidence_ref(ref, files_by_path)}</li>" for ref in refs)
        + "</ul>"
    )


def _render_attention_notes(
    notes: object, files_by_path: Mapping[str, Mapping[str, object]]
) -> str:
    rendered: list[str] = []
    for note in _items(notes):
        body = fragment(_text(note.get("text")))
        evidence = note.get("evidence")
        evidence_html = _render_evidence_list(evidence, files_by_path) if evidence else ""
        rendered.append(f'<aside class="attention-note">{body}{evidence_html}</aside>')
    return "".join(rendered)


def _weight_chip(weight: StepWeight) -> str:
    """The neutral reading-weight chip for a step summary (derived, never authored).

    Weight is emphasis, not verdict: the chip stays muted and carries its own glyph,
    so it never reads by colour and never competes with the impact/confidence chips.
    An approximate weight is a floor — its title says so.
    """
    approx = " weight-approx" if weight.approximate else ""
    title = (
        "Estimated reading weight — a floor; some cited evidence could not be sized precisely"
        if weight.approximate
        else "Estimated reading weight, derived from this step's evidence"
    )
    return (
        f'<span class="chip weight{approx}" title="{escape_text(title)}">'
        f"{escape_text(lines_label(weight))}</span>"
    )


def _render_step(
    step: Mapping[str, object], files_by_path: Mapping[str, Mapping[str, object]]
) -> str:
    sid = _text(step.get("id"))
    impact = _text(step.get("impact"))
    confidence = _text(step.get("confidence"))
    # Derived at render time from the step's evidence — the narrator never authors it.
    # `data-weight` (the number) and `data-weight-bucket` (its Map-dot size tier, a
    # Python-owned policy) ride on the panel so Deck Mode relays the tier onto its dot
    # verbatim — the same way it relays data-impact; the visible chip travels with the
    # relocated step onto the Stage.
    weight = step_weight(_step_evidence(step), files_by_path)
    # `data-core` stamps this step's core-route membership (CORE_IMPACTS) so the deck
    # relays it verbatim — the same Python-owned-policy/relay posture as data-weight-bucket;
    # the JS never re-derives which impacts are core (issue #101). Present only when core.
    core_flag = ' data-core="true"' if impact in CORE_IMPACTS else ""
    parts = [
        f'<details class="step" id="{escape_text(sid)}" data-impact="{escape_text(impact)}"'
        f'{core_flag} data-weight="{weight.lines}" data-weight-bucket="{dot_bucket(weight)}">',
        "<summary>",
        f'<span class="chip impact-{escape_text(impact)}">{escape_text(impact)}</span> ',
        fragment(_text(step.get("summary"))),
        " ",
        f'<span class="chip confidence-{escape_text(confidence)}">confidence: '
        f"{escape_text(confidence)}</span> ",
        _weight_chip(weight),
        "</summary>",
        '<div class="step-body">',
    ]
    detail = step.get("detail")
    if isinstance(detail, str):
        parts.append(f'<p class="detail">{fragment(detail)}</p>')
    parts.append(f'<p class="why-now">{fragment(_text(step.get("why_now")))}</p>')
    prompts = step.get("review_prompts")
    if isinstance(prompts, list) and prompts:
        parts.extend(
            [
                "<h4>Review prompts</h4>",
                '<ul class="review-prompts">',
                *[f"<li>{fragment(prompt)}</li>" for prompt in prompts if isinstance(prompt, str)],
                "</ul>",
            ]
        )
    parts.extend(
        [
            "<h4>Evidence</h4>",
            _render_evidence_list(step.get("evidence"), files_by_path),
            _render_attention_notes(step.get("attention_notes"), files_by_path),
        ]
    )
    related = step.get("relates_to")
    if isinstance(related, list) and related:
        links = " · ".join(
            f'<a href="#{escape_text(target)}">related: {escape_text(target)}</a>'
            for target in related
            if isinstance(target, str)
        )
        parts.append(f'<p class="step-relations">{links}</p>')
    seam_open, seam_close = evidence_seam_markers(sid)
    parts.extend([seam_open, seam_close, "</div>", "</details>"])
    return "\n".join(parts)


def _render_thread(
    thread: Mapping[str, object],
    files_by_path: Mapping[str, Mapping[str, object]],
    drive_by: set[str],
) -> str:
    tid = _text(thread.get("id"))
    steps = _items(thread.get("steps"))
    attention = _attention_impact(steps)
    impact_class = f" attention-{attention}" if attention else ""
    drive_by_chip = '<span class="chip flag-drive-by">drive-by</span>' if tid in drive_by else ""
    paths: list[str] = []
    for path in _strings(thread.get("paths")):
        if path not in files_by_path:
            raise RenderError(f"thread {tid!r} path {path!r} is not in fragments.json")
        paths.append(_path_html(files_by_path[path]))
    # Reading cost rolled up from the thread's steps — a per-thread total the Map reuses
    # (the derived-over-authored rule: no thread weight is ever in the analysis).
    weight = rollup(step_weight(_step_evidence(step), files_by_path) for step in steps)
    # The Map shows this thread rollup as a bare minute figure, so its tooltip states the
    # reading-pace heuristic too — a rollup is never a bare number the reviewer can't
    # recalibrate (weight.py's contract), matching the L0 route estimate.
    weight_title = (
        "reading time unknown — cited evidence carries no measurable lines"
        if weight.approximate and weight.lines == 0
        else f"{lines_label(weight)} to read (~{LINES_PER_MINUTE} lines/min)"
    )
    return "\n".join(
        [
            f'<section class="thread" id="{escape_text(tid)}" data-weight="{weight.lines}">',
            "<h2>",
            f'<span class="thread-id">{escape_text(tid)}</span> ',
            fragment(_text(thread.get("title"))),
            drive_by_chip,
            f'<span class="thread-impacts{impact_class}">'
            f"{escape_text(_impact_summary(steps))}</span>",
            f'<span class="thread-weight" data-weight="{weight.lines}"'
            f' title="{escape_text(weight_title)}">'
            f"{escape_text(minutes_label(weight))}</span>",
            "</h2>",
            f'<p class="thread-summary">{fragment(_text(thread.get("summary")))}</p>',
            f'<p class="thread-paths">{" · ".join(paths)}</p>',
            *[_render_step(step, files_by_path) for step in steps],
            "</section>",
        ]
    )


def _route_estimate(
    route_weight: StepWeight, core_weight: StepWeight, abridged: bool
) -> tuple[str, str]:
    """The L0 reading-weight line and any deck route-budget data attributes.

    Returns ``(text, attrs)``. ``text`` is the orientation line; ``attrs`` is the
    ``data-core-budget``/``data-full-budget`` attribute string stamped on ``section.l0``
    so the served deck's route selector can label each pass's budget without re-deriving
    the reading-pace policy (weight.py owns it — the derived-over-authored rule). When the
    whole route is unmeasurable, a subset of nothing is still nothing: one honest "not
    sized" line, no per-route split, no attributes. When it is measurable but **not**
    abridged (every step is core, or none is), there is a single budget — the original
    line, unchanged. Only an abridged, measured review states both budgets and stamps the
    attributes; a budget that itself sizes to "unknown" is not stamped, so the deck's
    selector degrades to no sub-label rather than showing "unknown".
    """
    if route_weight.approximate and route_weight.lines == 0:
        return (
            "Reading weight: not sized — the cited evidence carries no measurable lines",
            "",
        )
    full_detail = (
        f"{lines_label(route_weight)} · {minutes_label(route_weight)} "
        f"at reading pace (~{LINES_PER_MINUTE} lines/min)"
    )
    if not abridged:
        return f"Reading weight: {full_detail}", ""
    # Abridged: state the core-first budget beside the full one (issue #101). Core leads —
    # it is the pass a reviewer facing a large change is meant to take first — with the
    # full route's line count carried along so the abridgement never hides the true size.
    core_minutes = minutes_label(core_weight)
    full_minutes = minutes_label(route_weight)
    text = (
        f"Reading weight: {core_minutes} core · {full_minutes} full at reading pace "
        f"(~{LINES_PER_MINUTE} lines/min; full is {lines_label(route_weight)})"
    )
    attrs = ""
    if core_minutes != "unknown":
        attrs += f' data-core-budget="{escape_text(core_minutes)}"'
    if full_minutes != "unknown":
        attrs += f' data-full-budget="{escape_text(full_minutes)}"'
    return text, attrs


def _render_orientation(
    analysis: Mapping[str, object],
    goal_html: str,
    isolation_note: str,
    files: Sequence[Mapping[str, object]],
    files_by_path: Mapping[str, Mapping[str, object]],
) -> str:
    threads = _items(analysis.get("threads"))
    all_steps = [step for thread in threads for step in _items(thread.get("steps"))]
    # Size every step once, then split the rollups — core is a subset of all, so a second
    # step_weight pass over the core steps would just re-do work already done here.
    weights = [step_weight(_step_evidence(step), files_by_path) for step in all_steps]
    core_weights = [
        weight
        for step, weight in zip(all_steps, weights, strict=True)
        if _text(step.get("impact")) in CORE_IMPACTS
    ]
    route_weight = rollup(weights)
    core_weight = rollup(core_weights)
    # The abridged core-first route (issue #101) is offered only when it genuinely
    # abridges: some steps are behavior-affecting and some are not. When every step is
    # core (or none is), core == full — there is nothing to select and one budget suffices.
    abridged = 0 < len(core_weights) < len(all_steps)
    route_estimate, l0_attrs = _route_estimate(route_weight, core_weight, abridged)
    links = "".join(
        f'<li><a href="#{escape_text(_text(thread.get("id")))}">'
        f"{fragment(_text(thread.get('title')))}</a></li>"
        for thread in threads
    )
    alignment = _alignment(analysis)
    if alignment is None:
        alignment_text = "Goal alignment unavailable"
    else:
        serves = ", ".join(_strings(alignment.get("serves_goal")))
        drive_by = ", ".join(_strings(alignment.get("drive_by")))
        alignment_text = f"Serves goal: {serves or 'none'} · Drive-by: {drive_by or 'none'}"
    return "\n".join(
        [
            f'<section class="l0"{l0_attrs}>',
            "<h2>Orientation</h2>",
            goal_html,
            isolation_note,
            f'<h3 class="analysis-title">{fragment(_text(analysis.get("title")))}</h3>',
            f'<p class="intent-read">{fragment(_text(analysis.get("intent_summary")))}</p>',
            '<ul class="orientation">',
            *([links] if links else []),
            f"<li>{len(files)} changed file(s)</li>",
            f"<li>{escape_text(_impact_summary(all_steps))}</li>",
            f'<li class="route-weight">{escape_text(route_estimate)}</li>',
            f"<li>{escape_text(alignment_text)}</li>",
            "</ul>",
            "</section>",
        ]
    )


def _render_files(
    run_dir: Path,
    files: Sequence[Mapping[str, object]],
    manifest: Mapping[str, object],
    by_hunk: Mapping[str, Sequence[str]],
    by_file: Mapping[str, Sequence[str]],
) -> str:
    rendered = ['<section class="evidence-files">', "<h2>Evidence</h2>"]
    if manifest.get("too_large") is True:
        rendered.append(
            f'<p class="too-large">{fragment(_text(manifest.get("too_large_reason")))}</p>'
        )
    for entry in files:
        anchor = _file_anchor(entry)
        added = entry.get("added", 0)
        deleted = entry.get("deleted", 0)
        stats = (
            '<span class="file-stats">'
            f'<span class="added">+{int(added) if isinstance(added, int) else 0}</span> '
            f'<span class="deleted">−{int(deleted) if isinstance(deleted, int) else 0}</span>'
            "</span>"
        )
        if entry.get("omitted") is True:
            body = f'<p class="omitted">{fragment(_text(entry.get("reason")))}</p>'
        else:
            fragment_name = entry.get("fragment")
            if not isinstance(fragment_name, str):
                raise RenderError(f"included file {_text(entry.get('path'))!r} has no fragment")
            fragment_path = run_dir / fragment_name
            try:
                fragment_html = fragment_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise RenderError(f"cannot read {fragment_path}: {exc}") from exc
            # Splice each hunk's narrating-step margin into the pre-escaped diff (#103).
            body = _annotate_hunks(fragment_html, entry, by_hunk)
        # A file-level ``{path}`` ref annotates the file *header*, not any hunk — the
        # reviewer sees which step narrates the file as a whole beside its path/stats.
        file_steps = by_file.get(_text(entry.get("path")))
        file_narration = (
            f'<span class="file-narration">{_narrating_step_links(file_steps)}</span>'
            if file_steps
            else ""
        )
        rendered.extend(
            [
                f'<details class="file" id="{escape_text(anchor)}">',
                f"<summary>{_path_html(entry)} {stats}{file_narration}</summary>",
                f'<div class="file-body">{body}</div>',
                "</details>",
            ]
        )
    rendered.append("</section>")
    return "\n".join(rendered)


def _render_runner(analysis: Mapping[str, object]) -> str:
    runner = analysis.get("test_runner")
    if not isinstance(runner, Mapping):
        return '<section><h2>Test runner</h2><p class="runner-note">none detected</p></section>'
    command = runner.get("command") or runner.get("runner")
    evidence = runner.get("runner_evidence")
    if not isinstance(command, str) or not command:
        return '<section><h2>Test runner</h2><p class="runner-note">none detected</p></section>'
    suffix = f" — detected from {fragment(evidence)}" if isinstance(evidence, str) else ""
    return (
        '<section><h2>Test runner</h2><p class="runner-note">Suggested, not run: '
        f"<code>{fragment(command)}</code>{suffix}</p></section>"
    )


def _document(
    run_dir: Path,
    analysis: Mapping[str, object],
    manifest: Mapping[str, object],
    fragments_source: str,
) -> str:
    files = _manifest_files(manifest)
    files_by_path = _file_by_path(files)
    alignment = _alignment(analysis)
    drive_by = set(_strings(alignment.get("drive_by"))) if alignment is not None else set()
    title_html = _fragment_block(fragments_source, "title")
    meta_html = _fragment_block(fragments_source, "meta")
    goal_html = _fragment_block(fragments_source, "goal")
    isolation_note = _isolation_note(run_dir)
    run_meta = _run_meta(run_dir)
    threads = _items(analysis.get("threads"))
    # The reverse hunk↔step join, computed once here where both the analysis (steps) and
    # the manifest (hunk ids) are in hand, then handed to the L3 files section (#103).
    by_hunk, by_file = _narration_index(threads, files_by_path)
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            f'<meta http-equiv="Content-Security-Policy" content="{INTERACTIVE_CSP}">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            *([run_meta] if run_meta else []),
            "<title>Branch Review Cockpit</title>",
            '<link rel="stylesheet" href="assets/cockpit.css">',
            "</head>",
            "<body>",
            f'<header class="cockpit-head">{title_html}\n{meta_html}</header>',
            "<main>",
            _render_orientation(analysis, goal_html, isolation_note, files, files_by_path),
            *[_render_thread(thread, files_by_path, drive_by) for thread in threads],
            _render_files(run_dir, files, manifest, by_hunk, by_file),
            _render_runner(analysis),
            f"{QA_SEAM_OPEN}{QA_SEAM_CLOSE}",
            "</main>",
            '<script src="assets/app.js"></script>',
            "</body>",
            "</html>",
            "",
        ]
    )


def render_cockpit(run_dir: Path = DEFAULT_RUN_DIR) -> Path:
    """Render and atomically write ``run_dir/review.html``; return its path.

    The run directory is the module's interface. It must contain the collector's
    fragments plus a valid narrator ``analysis.json``. A failed validation, missing
    artifact, unresolved evidence reference, or final lint error raises
    :class:`RenderError` and leaves any existing cockpit untouched.
    """
    analysis = _mapping(_load_json(run_dir / "analysis.json"), "analysis.json")
    analysis_errors = validate_analysis(analysis)
    if analysis_errors:
        detail = "; ".join(str(error) for error in analysis_errors)
        raise RenderError(f"analysis.json is invalid: {detail}")
    config = _mapping(_load_json(run_dir / "resolved-config.json"), "resolved-config.json")
    styling = config.get("styling", "vendored")
    if styling not in {"vendored", "cdn"}:
        raise RenderError("resolved-config.json styling must be 'vendored' or 'cdn'")
    manifest = _mapping(_load_json(run_dir / "fragments.json"), "fragments.json")
    try:
        fragments_source = (run_dir / "fragments.html").read_text(encoding="utf-8")
    except OSError as exc:
        raise RenderError(f"cannot read {run_dir / 'fragments.html'}: {exc}") from exc

    html = _document(run_dir, analysis, manifest, fragments_source)
    lint_errors = lint_cockpit(
        html,
        styling=styling,
        csp_mode="interactive",
        step_ids=step_ids(analysis),
    )
    if lint_errors:
        raise RenderError("rendered cockpit failed lint: " + "; ".join(map(str, lint_errors)))

    output = run_dir / "review.html"
    temporary = run_dir / ".review.html.tmp"
    temporary.write_text(html, encoding="utf-8")
    temporary.replace(output)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "run_dir", nargs="?", type=Path, default=DEFAULT_RUN_DIR, help="Collected run directory."
    )
    parser.add_argument(
        "--analysis-context",
        choices=("isolated", "invoking"),
        default="isolated",
        help="Whether analysis independence was enforced (default: isolated).",
    )
    args = parser.parse_args(argv)
    try:
        context_path = args.run_dir / RENDER_CONTEXT_NAME
        if args.analysis_context == "invoking":
            context_path.write_text(
                json.dumps({"analysis_isolated": False}) + "\n", encoding="utf-8"
            )
        else:
            context_path.unlink(missing_ok=True)
        output = render_cockpit(args.run_dir)
    except (OSError, RenderError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Review Cockpit rendered: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
