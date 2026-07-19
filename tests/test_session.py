"""Tests for the Session Evaluator (issue #8; schema-mismatch clean break, issue #87).

The evaluator is pure policy — no git, no filesystem — so its core is table-driven:
every disposition (and the regenerate-by-default invariant on ``stale``, the
resume-suppressing ``stale-schema``) is a row in ``_EVAL_CASES``. The rest cover the
status property, the disposition policy properties, the JSON round-trip, and the thin
I/O (load/save, build-from-context), including the malformed-file failure modes that
must raise :class:`SessionError` rather than crash.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from branch_review.analysis import SCHEMA as ANALYSIS_SCHEMA
from branch_review.session import (
    Session,
    SessionDisposition,
    SessionError,
    SessionStatus,
    bump_resume,
    end_session,
    evaluate,
    load_session,
    main,
    save_session,
    session_from_context,
)

_HEAD = "1111111111111111111111111111111111111111"
_OTHER_HEAD = "2222222222222222222222222222222222222222"
_MERGE_BASE = "3333333333333333333333333333333333333333"
_OTHER_MERGE_BASE = "4444444444444444444444444444444444444444"
_BASE = "origin/main"
_OTHER_BASE = "origin/release"
_OLD_SCHEMA = "review-analysis/0.3"  # a schema this code no longer speaks
_NOW = datetime(2026, 6, 27, 12, 0, 0, tzinfo=UTC)


def _session(
    *,
    status: SessionStatus = SessionStatus.OPEN,
    branch: str = "feat/x",
    head_sha: str = _HEAD,
    base: str = _BASE,
    merge_base: str = _MERGE_BASE,
    analysis_schema: str = ANALYSIS_SCHEMA,
    resume_seq: int = 0,
) -> Session:
    return Session(
        schema="review-session/0.4",
        status=status,
        base=base,
        branch=branch,
        head_sha=head_sha,
        merge_base=merge_base,
        analysis_schema=analysis_schema,
        started_at=_NOW.isoformat(),
        resume_seq=resume_seq,
    )


# (label, session, current_head, current_branch, current_base, current_merge_base, expected).
_EvalCase = tuple[str, Session | None, str, str, str, str, SessionDisposition]
_EVAL_CASES: list[_EvalCase] = [
    # none — nothing to resume (base/merge-base are irrelevant; passed as-is).
    ("no session at all", None, _HEAD, "feat/x", _BASE, _MERGE_BASE, SessionDisposition.NONE),
    (
        "finished session is not resumable",
        _session(status=SessionStatus.ENDED),
        _HEAD,
        "feat/x",
        _BASE,
        _MERGE_BASE,
        SessionDisposition.NONE,
    ),
    (
        "finished outranks even a branch mismatch",
        _session(status=SessionStatus.ENDED, branch="feat/old"),
        _OTHER_HEAD,
        "feat/x",
        _BASE,
        _MERGE_BASE,
        SessionDisposition.NONE,
    ),
    # fresh — same branch, HEAD, base, AND merge-base: re-attach.
    ("all match", _session(), _HEAD, "feat/x", _BASE, _MERGE_BASE, SessionDisposition.FRESH),
    # stale — same branch, but the diff identity moved (HEAD / base / merge-base).
    (
        "head advanced",
        _session(),
        _OTHER_HEAD,
        "feat/x",
        _BASE,
        _MERGE_BASE,
        SessionDisposition.STALE,
    ),
    (
        "different base requested (same HEAD & merge-base)",
        _session(),
        _HEAD,
        "feat/x",
        _OTHER_BASE,
        _MERGE_BASE,
        SessionDisposition.STALE,
    ),
    (
        "base advanced — merge-base moved under a fixed HEAD",
        _session(),
        _HEAD,
        "feat/x",
        _BASE,
        _OTHER_MERGE_BASE,
        SessionDisposition.STALE,
    ),
    # different-branch — saved review belongs to another branch (outranks the diff check).
    (
        "different branch, different head",
        _session(branch="feat/old"),
        _OTHER_HEAD,
        "feat/x",
        _BASE,
        _MERGE_BASE,
        SessionDisposition.DIFFERENT_BRANCH,
    ),
    (
        "different branch wins even when head matches",
        _session(branch="feat/old"),
        _HEAD,
        "feat/x",
        _BASE,
        _MERGE_BASE,
        SessionDisposition.DIFFERENT_BRANCH,
    ),
    (
        "different branch outranks a schema mismatch too",
        _session(branch="feat/old", analysis_schema=_OLD_SCHEMA),
        _HEAD,
        "feat/x",
        _BASE,
        _MERGE_BASE,
        SessionDisposition.DIFFERENT_BRANCH,
    ),
    # stale-schema — same branch, but the saved analysis is a schema this code retired
    # (ADR-0016). Outranks the diff check and suppresses resume, even at the same HEAD.
    (
        "schema predates current, same HEAD/base",
        _session(analysis_schema=_OLD_SCHEMA),
        _HEAD,
        "feat/x",
        _BASE,
        _MERGE_BASE,
        SessionDisposition.STALE_SCHEMA,
    ),
    (
        "schema mismatch outranks a fresh diff and a head advance",
        _session(analysis_schema=_OLD_SCHEMA),
        _OTHER_HEAD,
        "feat/x",
        _BASE,
        _MERGE_BASE,
        SessionDisposition.STALE_SCHEMA,
    ),
    (
        "an empty recorded schema (a pre-0.3 session) is a mismatch",
        _session(analysis_schema=""),
        _HEAD,
        "feat/x",
        _BASE,
        _MERGE_BASE,
        SessionDisposition.STALE_SCHEMA,
    ),
]


@pytest.mark.parametrize(
    ("session", "head", "branch", "base", "merge_base", "expected"),
    [(s, h, b, bs, mb, e) for _label, s, h, b, bs, mb, e in _EVAL_CASES],
    ids=[c[0] for c in _EVAL_CASES],
)
def test_evaluate_dispositions(
    session: Session | None,
    head: str,
    branch: str,
    base: str,
    merge_base: str,
    expected: SessionDisposition,
) -> None:
    assert (
        evaluate(
            session,
            current_head=head,
            current_branch=branch,
            current_base=base,
            current_merge_base=merge_base,
        )
        is expected
    )


def test_every_disposition_is_covered() -> None:
    # The acceptance criterion: the table exercises *every* disposition value.
    covered = {c[-1] for c in _EVAL_CASES}
    assert covered == set(SessionDisposition)


def test_stale_regenerates_by_default() -> None:
    # The headline invariant: a stale review offers restore but does NOT default to it
    # — regenerate is the default, resume-anyway available.
    disposition = evaluate(
        _session(),
        current_head=_OTHER_HEAD,
        current_branch="feat/x",
        current_base=_BASE,
        current_merge_base=_MERGE_BASE,
    )
    assert disposition is SessionDisposition.STALE
    assert disposition.offers_restore is True
    assert disposition.restore_is_default is False


def test_schema_mismatch_regenerates_with_no_resume() -> None:
    # The clean break (ADR-0016, #87): a session whose recorded analysis schema
    # predates the current one resolves to stale-schema — regenerate, and resume-anyway
    # is NOT offered, because the loop and bake can no longer read that analysis. The
    # default current schema is this code's own, so a matching session is never
    # spuriously schema-stale.
    disposition = evaluate(
        _session(analysis_schema=_OLD_SCHEMA),
        current_head=_HEAD,
        current_branch="feat/x",
        current_base=_BASE,
        current_merge_base=_MERGE_BASE,
    )
    assert disposition is SessionDisposition.STALE_SCHEMA
    assert disposition.offers_restore is False
    assert disposition.restore_is_default is False


def test_matching_schema_is_not_schema_stale() -> None:
    # A session recorded against the code's current schema falls through the
    # schema gate to the ordinary diff check (fresh here).
    assert (
        evaluate(
            _session(analysis_schema=ANALYSIS_SCHEMA),
            current_head=_HEAD,
            current_branch="feat/x",
            current_base=_BASE,
            current_merge_base=_MERGE_BASE,
        )
        is SessionDisposition.FRESH
    )


# (disposition, offers_restore, restore_is_default) — the policy each verdict carries.
_POLICY_CASES: list[tuple[SessionDisposition, bool, bool]] = [
    (SessionDisposition.NONE, False, False),
    (SessionDisposition.FRESH, True, True),
    (SessionDisposition.STALE, True, False),
    (SessionDisposition.STALE_SCHEMA, False, False),
    (SessionDisposition.DIFFERENT_BRANCH, False, False),
]


@pytest.mark.parametrize(("disposition", "offers", "default"), _POLICY_CASES)
def test_disposition_policy_properties(
    disposition: SessionDisposition, offers: bool, default: bool
) -> None:
    assert disposition.offers_restore is offers
    assert disposition.restore_is_default is default


def test_policy_table_covers_every_disposition() -> None:
    assert {d for d, _o, _df in _POLICY_CASES} == set(SessionDisposition)


def test_restore_is_default_implies_offers_restore() -> None:
    # A verdict can never default to a restore it does not even offer.
    for disposition in SessionDisposition:
        if disposition.restore_is_default:
            assert disposition.offers_restore


@pytest.mark.parametrize(
    ("status", "resumable"),
    [(SessionStatus.OPEN, True), (SessionStatus.ENDED, False)],
)
def test_status_is_resumable(status: SessionStatus, resumable: bool) -> None:
    assert status.is_resumable is resumable


def test_end_session_transitions_open_to_ended() -> None:
    original = _session()
    ended = end_session(original)
    assert ended.status is SessionStatus.ENDED
    # ONLY the status changes — every other field (schema, base, branch, head_sha,
    # started_at) is carried through verbatim.
    assert ended == replace(original, status=SessionStatus.ENDED)


def test_end_session_is_idempotent() -> None:
    once = end_session(_session())
    assert end_session(once) == once


def test_bump_resume_advances_only_the_counter() -> None:
    original = _session(resume_seq=2)
    bumped = bump_resume(original)
    assert bumped.resume_seq == 3
    # ONLY resume_seq changes — every other field is carried through verbatim.
    assert bumped == replace(original, resume_seq=3)


def test_resume_seq_round_trips() -> None:
    original = _session(resume_seq=5)
    assert Session.from_mapping(json.loads(original.to_json())) == original


def test_json_round_trip() -> None:
    original = _session()
    restored = Session.from_mapping(json.loads(original.to_json()))
    assert restored == original
    # An ended session round-trips too (status serialised as its string value).
    ended = end_session(original)
    assert Session.from_mapping(json.loads(ended.to_json())) == ended


def test_to_json_writes_status_as_string_value() -> None:
    data = json.loads(_session().to_json())
    assert data["status"] == "open"


def test_load_session_absent_returns_none(tmp_path: Path) -> None:
    assert load_session(tmp_path) is None


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    session = _session()
    path = save_session(tmp_path, session)
    assert path == tmp_path / "session.json"
    assert load_session(tmp_path) == session


def test_save_session_creates_missing_parent(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / ".review-agent"
    save_session(nested, _session())
    assert (nested / "session.json").is_file()


def test_save_session_wraps_filesystem_error(tmp_path: Path) -> None:
    # A state dir whose parent is a regular file can't be created — the OSError must
    # surface as SessionError (the one type the CLI's main() handles), not escape raw.
    blocker = tmp_path / "afile"
    blocker.write_text("not a dir", encoding="utf-8")
    with pytest.raises(SessionError, match="cannot write"):
        save_session(blocker / "sub", _session())


def test_load_session_malformed_json_raises(tmp_path: Path) -> None:
    (tmp_path / "session.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(SessionError):
        load_session(tmp_path)


def test_load_session_not_an_object_raises(tmp_path: Path) -> None:
    (tmp_path / "session.json").write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(SessionError):
        load_session(tmp_path)


def test_load_session_missing_field_raises(tmp_path: Path) -> None:
    incomplete = {"schema": "review-session/0.1", "status": "open", "base": "main"}
    (tmp_path / "session.json").write_text(json.dumps(incomplete), encoding="utf-8")
    with pytest.raises(SessionError, match="missing field"):
        load_session(tmp_path)


def test_load_session_non_string_field_raises(tmp_path: Path) -> None:
    bad = {
        "schema": "review-session/0.2",
        "status": "open",
        "base": "main",
        "branch": "feat/x",
        "head_sha": 12345,  # not a string
        "merge_base": _MERGE_BASE,
        "started_at": _NOW.isoformat(),
    }
    (tmp_path / "session.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(SessionError, match="head_sha"):
        load_session(tmp_path)


def test_load_session_legacy_without_analysis_schema(tmp_path: Path) -> None:
    # A session.json written before the 0.3 clean break has no ``analysis_schema``.
    # It loads leniently (absence → ""), and the evaluator reads that empty schema as
    # a mismatch — so an old session regenerates rather than crashing the review.
    legacy = {
        "schema": "review-session/0.2",
        "status": "open",
        "base": _BASE,
        "branch": "feat/x",
        "head_sha": _HEAD,
        "merge_base": _MERGE_BASE,
        "started_at": _NOW.isoformat(),
    }
    (tmp_path / "session.json").write_text(json.dumps(legacy), encoding="utf-8")
    session = load_session(tmp_path)
    assert session is not None and session.analysis_schema == ""
    assert (
        evaluate(
            session,
            current_head=_HEAD,
            current_branch="feat/x",
            current_base=_BASE,
            current_merge_base=_MERGE_BASE,
        )
        is SessionDisposition.STALE_SCHEMA
    )


def test_load_session_legacy_without_resume_seq(tmp_path: Path) -> None:
    # A session.json written before 0.4 has no ``resume_seq``. It loads leniently
    # (absence → 0, "never resumed"), so an old session never crashes a resume.
    legacy = {
        "schema": "review-session/0.3",
        "status": "open",
        "base": _BASE,
        "branch": "feat/x",
        "head_sha": _HEAD,
        "merge_base": _MERGE_BASE,
        "analysis_schema": ANALYSIS_SCHEMA,
        "started_at": _NOW.isoformat(),
    }
    (tmp_path / "session.json").write_text(json.dumps(legacy), encoding="utf-8")
    session = load_session(tmp_path)
    assert session is not None and session.resume_seq == 0


@pytest.mark.parametrize("bad_seq", [-1, "3", 1.5, True])
def test_load_session_invalid_resume_seq_raises(tmp_path: Path, bad_seq: object) -> None:
    # A present resume_seq must be a non-negative int — a negative, a string, a float, or
    # a bool (an int subclass, excluded explicitly) is rejected, not silently coerced.
    bad = {
        "schema": "review-session/0.4",
        "status": "open",
        "base": _BASE,
        "branch": "feat/x",
        "head_sha": _HEAD,
        "merge_base": _MERGE_BASE,
        "analysis_schema": ANALYSIS_SCHEMA,
        "started_at": _NOW.isoformat(),
        "resume_seq": bad_seq,
    }
    (tmp_path / "session.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(SessionError, match="resume_seq"):
        load_session(tmp_path)


def test_load_session_non_string_analysis_schema_raises(tmp_path: Path) -> None:
    bad = {
        "schema": "review-session/0.3",
        "status": "open",
        "base": _BASE,
        "branch": "feat/x",
        "head_sha": _HEAD,
        "merge_base": _MERGE_BASE,
        "analysis_schema": 4,  # not a string
        "started_at": _NOW.isoformat(),
    }
    (tmp_path / "session.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(SessionError, match="analysis_schema"):
        load_session(tmp_path)


def test_load_session_unknown_status_raises(tmp_path: Path) -> None:
    bad = {
        "schema": "review-session/0.2",
        "status": "paused",  # not a known SessionStatus
        "base": "main",
        "branch": "feat/x",
        "head_sha": _HEAD,
        "merge_base": _MERGE_BASE,
        "started_at": _NOW.isoformat(),
    }
    (tmp_path / "session.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(SessionError, match="status"):
        load_session(tmp_path)


def _write_context(tmp_path: Path, **overrides: str) -> Path:
    context = {
        "schema": "review-context/0.1-skeleton",
        "base": "origin/main",
        "base_sha": "abc",
        "branch": "feat/x",
        "head_sha": _HEAD,
        "merge_base": "def",
        "diff_range": "origin/main...HEAD",
        "generated_at": _NOW.isoformat(),
        "changed_file_count": 3,
        "is_empty": False,
    }
    context.update(overrides)
    path = tmp_path / "context.json"
    path.write_text(json.dumps(context), encoding="utf-8")
    return path


def test_session_from_context_mirrors_the_collected_revision(tmp_path: Path) -> None:
    session = session_from_context(_write_context(tmp_path))
    assert session.status is SessionStatus.OPEN
    assert session.base == "origin/main"
    assert session.branch == "feat/x"
    assert session.head_sha == _HEAD
    assert session.merge_base == "def"  # the diff-identity anchor is carried through
    # No analysis.json beside the context here, so analysis_schema degrades to this
    # code's schema — the honest fallback (a run that authored a cockpit did so with
    # this code). The read-from-disk path is pinned in its own test below.
    assert session.analysis_schema == ANALYSIS_SCHEMA
    # started_at is the context's generated_at — the review began when the diff was collected.
    assert session.started_at == _NOW.isoformat()


def test_session_from_context_records_the_analysis_own_schema(tmp_path: Path) -> None:
    # The session records the schema the analysis.json beside the context was authored
    # with — the artifact's fact, not merely the code's belief — so an analysis from an
    # older code version is caught as stale-schema on a later resume.
    context = _write_context(tmp_path)
    (tmp_path / "analysis.json").write_text(
        json.dumps({"schema": "review-analysis/0.3", "threads": []}), encoding="utf-8"
    )
    session = session_from_context(context)
    assert session.analysis_schema == "review-analysis/0.3"


def test_session_from_context_analysis_schema_degrades_when_unreadable(tmp_path: Path) -> None:
    # A malformed analysis.json must not break session recording — it falls back to the
    # code's own schema rather than raising.
    context = _write_context(tmp_path)
    (tmp_path / "analysis.json").write_text("{not json", encoding="utf-8")
    session = session_from_context(context)
    assert session.analysis_schema == ANALYSIS_SCHEMA


def test_session_from_context_missing_field_raises(tmp_path: Path) -> None:
    path = tmp_path / "context.json"
    path.write_text(json.dumps({"base": "main"}), encoding="utf-8")
    with pytest.raises(SessionError, match="missing or not a string"):
        session_from_context(path)


def test_session_from_context_non_string_field_raises(tmp_path: Path) -> None:
    # The validation gap closed by the cleanup: a non-string field is rejected, not
    # silently built into a nonsense session.
    with pytest.raises(SessionError, match="head_sha"):
        session_from_context(_write_context(tmp_path, head_sha=12345))  # type: ignore[arg-type]


def test_session_from_context_absent_file_raises(tmp_path: Path) -> None:
    with pytest.raises(SessionError):
        session_from_context(tmp_path / "context.json")


def test_cli_resume_bumps_the_open_session(tmp_path: Path) -> None:
    save_session(tmp_path, _session(resume_seq=0))
    assert main(["--out", str(tmp_path), "resume"]) == 0
    first = load_session(tmp_path)
    assert first is not None and first.resume_seq == 1
    # Monotonic across repeated resumes.
    assert main(["--out", str(tmp_path), "resume"]) == 0
    second = load_session(tmp_path)
    assert second is not None and second.resume_seq == 2


def test_cli_resume_is_a_noop_without_an_open_session(tmp_path: Path) -> None:
    # No session, and an ended one, both no-op — a resume signal never blocks the resume,
    # and an ended review has nothing to re-attach to.
    assert main(["--out", str(tmp_path), "resume"]) == 0
    assert load_session(tmp_path) is None
    save_session(tmp_path, _session(status=SessionStatus.ENDED, resume_seq=4))
    assert main(["--out", str(tmp_path), "resume"]) == 0
    ended = load_session(tmp_path)
    assert ended is not None and ended.resume_seq == 4  # untouched
