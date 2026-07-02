"""Tests for the interactive feedback loop (ADR-0003, issue #5).

The loop's job is threefold: pass the agent's answer to ``lavish-axi`` *as data,
never as a shell string* (the hardening guarantee), append every Q&A exchange to
``qa.jsonl`` live, and pass the poll's TOON through verbatim without parsing it. A
fake :class:`Runner` stands in for the real CLI so every branch is driven without
spawning a process.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from branch_review import feedback
from branch_review.feedback import (
    AGENT_REPLY_NAME,
    LAST_POLL_NAME,
    QA_NAME,
    LavishResult,
    append_exchange,
    end,
    poll,
    reply,
)

_NOW = datetime(2026, 6, 27, 12, 0, 0, tzinfo=UTC)


class FakeRunner:
    """Records each argv and returns scripted results in order."""

    def __init__(self, *results: LavishResult) -> None:
        self._results = list(results)
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> LavishResult:
        self.calls.append(argv)
        return self._results.pop(0) if self._results else LavishResult(0, "")


def _cockpit(tmp_path: Path) -> Path:
    out = tmp_path / ".review-agent"
    out.mkdir()
    return out / "review.html"


def _recorder(seen: dict[str, Path], name: str) -> Callable[..., int]:
    """A stand-in poll/reply/end that records the cockpit path it was called with."""

    def _run(f: Path, **_kwargs: object) -> int:
        seen[name] = f
        return 0

    return _run


# --- append_exchange -------------------------------------------------------------


def test_append_exchange_writes_one_jsonl_record(tmp_path: Path) -> None:
    qa = tmp_path / QA_NAME
    append_exchange(qa, feedback_raw="status: feedback", answer="because X", now=_NOW)

    (line,) = qa.read_text(encoding="utf-8").splitlines()
    record = json.loads(line)
    assert record == {
        "seq": 1,
        "ts": _NOW.isoformat(),
        "feedback_raw": "status: feedback",
        "answer": "because X",
    }


def test_append_exchange_increments_seq_and_appends(tmp_path: Path) -> None:
    qa = tmp_path / QA_NAME
    append_exchange(qa, feedback_raw="q1", answer="a1", now=_NOW)
    append_exchange(qa, feedback_raw="q2", answer="a2", now=_NOW)

    seqs = [json.loads(line)["seq"] for line in qa.read_text(encoding="utf-8").splitlines()]
    assert seqs == [1, 2]


def test_append_exchange_preserves_non_ascii_and_untrusted_text(tmp_path: Path) -> None:
    qa = tmp_path / QA_NAME
    # Untrusted feedback laden with shell metacharacters is stored as JSON data.
    hostile = '"; rm -rf ~ #  $(whoami)  `id`  café'
    append_exchange(qa, feedback_raw=hostile, answer="naïve café ☕", now=_NOW)

    record = json.loads(qa.read_text(encoding="utf-8"))
    assert record["feedback_raw"] == hostile
    assert record["answer"] == "naïve café ☕"


# --- poll ------------------------------------------------------------------------


def test_poll_saves_and_echoes_toon_on_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cockpit = _cockpit(tmp_path)
    runner = FakeRunner(LavishResult(0, "session:\n  status: feedback\n"))

    rc = poll(cockpit, runner=runner)

    assert rc == 0
    assert runner.calls == [["npx", "-y", feedback.LAVISH_PKG, "poll", str(cockpit)]]
    assert (cockpit.parent / LAST_POLL_NAME).read_text() == "session:\n  status: feedback\n"
    assert "status: feedback" in capsys.readouterr().out


def test_poll_does_not_save_on_nonzero(tmp_path: Path) -> None:
    cockpit = _cockpit(tmp_path)
    runner = FakeRunner(LavishResult(1, ""))  # e.g. NOT_FOUND / missing session

    rc = poll(cockpit, runner=runner, echo=False)

    assert rc == 1
    assert not (cockpit.parent / LAST_POLL_NAME).exists()


# --- reply -----------------------------------------------------------------------


def _write_answer(cockpit: Path, text: str) -> None:
    (cockpit.parent / AGENT_REPLY_NAME).write_text(text, encoding="utf-8")


def test_reply_passes_answer_as_argv_element_not_shell(tmp_path: Path) -> None:
    cockpit = _cockpit(tmp_path)
    # An answer that would be catastrophic if interpolated into a shell string.
    answer = 'Done.\nSee `git log`; rm -rf / "$HOME" #'
    _write_answer(cockpit, answer)
    runner = FakeRunner(LavishResult(0, "session:\n  status: ended\n"))

    rc = reply(cockpit, runner=runner, echo=False, now=_NOW)

    assert rc == 0
    # The answer is one discrete argv element, byte-for-byte — never a shell fragment.
    assert runner.calls == [
        ["npx", "-y", feedback.LAVISH_PKG, "poll", str(cockpit), "--agent-reply", answer]
    ]


def test_reply_logs_exchange_with_prior_feedback(tmp_path: Path) -> None:
    cockpit = _cockpit(tmp_path)
    (cockpit.parent / LAST_POLL_NAME).write_text("prompts[1]:\n  - prompt: why?\n")
    _write_answer(cockpit, "because the retry is bounded")
    runner = FakeRunner(LavishResult(0, "session:\n  status: waiting\n"))

    reply(cockpit, runner=runner, echo=False, now=_NOW)

    record = json.loads((cockpit.parent / QA_NAME).read_text(encoding="utf-8"))
    assert record["feedback_raw"] == "prompts[1]:\n  - prompt: why?\n"
    assert record["answer"] == "because the retry is bounded"


def test_reply_rotates_last_poll_to_next_question(tmp_path: Path) -> None:
    cockpit = _cockpit(tmp_path)
    (cockpit.parent / LAST_POLL_NAME).write_text("first question\n")
    _write_answer(cockpit, "first answer")
    runner = FakeRunner(LavishResult(0, "second question\n"))

    reply(cockpit, runner=runner, echo=False, now=_NOW)

    # The exchange logged the *prior* question; last-poll now holds the *next* one.
    record = json.loads((cockpit.parent / QA_NAME).read_text())
    assert record["feedback_raw"] == "first question\n"
    assert (cockpit.parent / LAST_POLL_NAME).read_text() == "second question\n"


def test_reply_logs_on_interrupt_but_keeps_prior_last_poll(tmp_path: Path) -> None:
    cockpit = _cockpit(tmp_path)
    (cockpit.parent / LAST_POLL_NAME).write_text("the question\n")
    _write_answer(cockpit, "the answer")
    # Esc during the post-reply wait: answer was delivered, but no next TOON arrived.
    runner = FakeRunner(LavishResult(130, "", interrupted=True))

    rc = reply(cockpit, runner=runner, echo=False, now=_NOW)

    assert rc == 130
    record = json.loads((cockpit.parent / QA_NAME).read_text())
    assert record["answer"] == "the answer"
    # last-poll is untouched, so /review-resume still answers the same question.
    assert (cockpit.parent / LAST_POLL_NAME).read_text() == "the question\n"


def test_reply_does_not_log_on_fast_error(tmp_path: Path) -> None:
    cockpit = _cockpit(tmp_path)
    _write_answer(cockpit, "an answer")
    runner = FakeRunner(LavishResult(1, ""))  # server unreachable: answer never shown

    rc = reply(cockpit, runner=runner, echo=False, now=_NOW)

    assert rc == 1
    assert not (cockpit.parent / QA_NAME).exists()


def test_reply_errors_when_answer_missing(tmp_path: Path) -> None:
    cockpit = _cockpit(tmp_path)
    runner = FakeRunner(LavishResult(0, ""))

    rc = reply(cockpit, runner=runner, echo=False, now=_NOW)

    assert rc == 2
    assert runner.calls == []  # never invoked lavish-axi
    assert not (cockpit.parent / QA_NAME).exists()


def test_reply_errors_when_answer_empty(tmp_path: Path) -> None:
    cockpit = _cockpit(tmp_path)
    _write_answer(cockpit, "   \n")
    runner = FakeRunner(LavishResult(0, ""))

    rc = reply(cockpit, runner=runner, echo=False, now=_NOW)

    assert rc == 2
    assert runner.calls == []


# --- end -------------------------------------------------------------------------


def test_end_invokes_lavish_end(tmp_path: Path) -> None:
    cockpit = _cockpit(tmp_path)
    runner = FakeRunner(LavishResult(0, ""))

    rc = end(cockpit, runner=runner)

    assert rc == 0
    assert runner.calls == [["npx", "-y", feedback.LAVISH_PKG, "end", str(cockpit)]]


# --- lavish_version threading (issue #10 machine config) --------------------------


def _pin_version(cockpit: Path, version: object) -> None:
    (cockpit.parent / feedback.RESOLVED_CONFIG_NAME).write_text(
        json.dumps({"lavish_version": version}), encoding="utf-8"
    )


def test_poll_uses_configured_lavish_version(tmp_path: Path) -> None:
    cockpit = _cockpit(tmp_path)
    _pin_version(cockpit, "0.2.0")
    runner = FakeRunner(LavishResult(0, "session:\n  status: waiting\n"))

    poll(cockpit, runner=runner, echo=False)

    assert runner.calls == [["npx", "-y", "lavish-axi@0.2.0", "poll", str(cockpit)]]


def test_reply_and_end_use_configured_lavish_version(tmp_path: Path) -> None:
    cockpit = _cockpit(tmp_path)
    _pin_version(cockpit, "0.2.0")
    answer = cockpit.parent / AGENT_REPLY_NAME
    answer.write_text("because X\n", encoding="utf-8")
    runner = FakeRunner(LavishResult(0, "session:\n  status: waiting\n"))

    reply(cockpit, runner=runner, echo=False, now=_NOW)
    end(cockpit, runner=runner)

    assert runner.calls[0][:3] == ["npx", "-y", "lavish-axi@0.2.0"]
    assert runner.calls[1] == ["npx", "-y", "lavish-axi@0.2.0", "end", str(cockpit)]


def test_resolve_lavish_pkg_defaults_when_unset(tmp_path: Path) -> None:
    cockpit = _cockpit(tmp_path)
    # No resolved-config.json at all -> bundled default.
    assert feedback.resolve_lavish_pkg(cockpit) == feedback.LAVISH_PKG
    # Present but the machine config left the version null -> bundled default.
    _pin_version(cockpit, None)
    assert feedback.resolve_lavish_pkg(cockpit) == feedback.LAVISH_PKG


def test_resolve_lavish_pkg_degrades_on_malformed_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A corrupt resolved-config.json must never block the answer loop: fall back to
    # the bundled default and say so on stderr.
    cockpit = _cockpit(tmp_path)
    (cockpit.parent / feedback.RESOLVED_CONFIG_NAME).write_text("{not json", encoding="utf-8")

    assert feedback.resolve_lavish_pkg(cockpit) == feedback.LAVISH_PKG
    assert "warning" in capsys.readouterr().err


# --- CLI dispatch ----------------------------------------------------------------


def test_main_dispatches_to_each_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Path] = {}
    monkeypatch.setattr(feedback, "poll", _recorder(seen, "poll"))
    monkeypatch.setattr(feedback, "reply", _recorder(seen, "reply"))
    monkeypatch.setattr(feedback, "end", _recorder(seen, "end"))

    assert feedback.main(["poll", "a.html"]) == 0
    assert feedback.main(["reply", "b.html"]) == 0
    assert feedback.main(["end", "c.html"]) == 0
    assert seen == {"poll": Path("a.html"), "reply": Path("b.html"), "end": Path("c.html")}


def test_main_defaults_to_canonical_cockpit(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Path] = {}
    monkeypatch.setattr(feedback, "poll", _recorder(seen, "poll"))

    feedback.main(["poll"])

    assert seen["poll"] == feedback.DEFAULT_COCKPIT
