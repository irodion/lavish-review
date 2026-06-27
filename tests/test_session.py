"""Tests for the Session Evaluator (issue #8).

The evaluator is pure policy — no git, no filesystem — so its core is table-driven:
every disposition (and the regenerate-by-default invariant on ``stale``) is a row in
``_EVAL_CASES``. The rest cover the status property, the disposition policy
properties, the JSON round-trip, and the thin I/O (load/save, build-from-context),
including the malformed-file failure modes that must raise :class:`SessionError`
rather than crash.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from branch_review.session import (
    Session,
    SessionDisposition,
    SessionError,
    SessionStatus,
    end_session,
    evaluate,
    load_session,
    save_session,
    session_from_context,
)

_HEAD = "1111111111111111111111111111111111111111"
_OTHER_HEAD = "2222222222222222222222222222222222222222"
_NOW = datetime(2026, 6, 27, 12, 0, 0, tzinfo=UTC)


def _session(
    *,
    status: SessionStatus = SessionStatus.OPEN,
    branch: str = "feat/x",
    head_sha: str = _HEAD,
) -> Session:
    return Session(
        schema="review-session/0.1",
        status=status,
        base="origin/main",
        branch=branch,
        head_sha=head_sha,
        started_at=_NOW.isoformat(),
    )


# (label, session, current_head, current_branch, expected disposition).
_EVAL_CASES: list[tuple[str, Session | None, str, str, SessionDisposition]] = [
    # none — nothing to resume.
    ("no session at all", None, _HEAD, "feat/x", SessionDisposition.NONE),
    (
        "finished session is not resumable",
        _session(status=SessionStatus.ENDED),
        _HEAD,
        "feat/x",
        SessionDisposition.NONE,
    ),
    (
        "finished outranks even a branch mismatch",
        _session(status=SessionStatus.ENDED, branch="feat/old"),
        _OTHER_HEAD,
        "feat/x",
        SessionDisposition.NONE,
    ),
    # fresh — same branch, same HEAD: re-attach.
    ("same branch and head", _session(), _HEAD, "feat/x", SessionDisposition.FRESH),
    # stale — same branch, HEAD advanced: regenerate by default.
    ("branch advanced", _session(), _OTHER_HEAD, "feat/x", SessionDisposition.STALE),
    # different-branch — saved review belongs to another branch.
    (
        "different branch, different head",
        _session(branch="feat/old"),
        _OTHER_HEAD,
        "feat/x",
        SessionDisposition.DIFFERENT_BRANCH,
    ),
    (
        "different branch wins even when head matches",
        _session(branch="feat/old"),
        _HEAD,
        "feat/x",
        SessionDisposition.DIFFERENT_BRANCH,
    ),
]


@pytest.mark.parametrize(
    ("session", "head", "branch", "expected"),
    [(s, h, b, e) for _label, s, h, b, e in _EVAL_CASES],
    ids=[label for label, _s, _h, _b, _e in _EVAL_CASES],
)
def test_evaluate_dispositions(
    session: Session | None, head: str, branch: str, expected: SessionDisposition
) -> None:
    assert evaluate(session, head, branch) is expected


def test_every_disposition_is_covered() -> None:
    # The acceptance criterion: the table exercises *every* disposition value.
    covered = {expected for _label, _s, _h, _b, expected in _EVAL_CASES}
    assert covered == set(SessionDisposition)


def test_stale_regenerates_by_default() -> None:
    # The headline invariant: a stale review offers restore but does NOT default to it
    # — regenerate is the default, resume-anyway available.
    disposition = evaluate(_session(), _OTHER_HEAD, "feat/x")
    assert disposition is SessionDisposition.STALE
    assert disposition.offers_restore is True
    assert disposition.restore_is_default is False


# (disposition, offers_restore, restore_is_default) — the policy each verdict carries.
_POLICY_CASES: list[tuple[SessionDisposition, bool, bool]] = [
    (SessionDisposition.NONE, False, False),
    (SessionDisposition.FRESH, True, True),
    (SessionDisposition.STALE, True, False),
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
        "schema": "review-session/0.1",
        "status": "open",
        "base": "main",
        "branch": "feat/x",
        "head_sha": 12345,  # not a string
        "started_at": _NOW.isoformat(),
    }
    (tmp_path / "session.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(SessionError, match="head_sha"):
        load_session(tmp_path)


def test_load_session_unknown_status_raises(tmp_path: Path) -> None:
    bad = {
        "schema": "review-session/0.1",
        "status": "paused",  # not a known SessionStatus
        "base": "main",
        "branch": "feat/x",
        "head_sha": _HEAD,
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
    # started_at is the context's generated_at — the review began when the diff was collected.
    assert session.started_at == _NOW.isoformat()


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
