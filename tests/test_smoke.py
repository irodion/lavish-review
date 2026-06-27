"""Smoke test — proves the CI harness (import path + pytest) works end to end."""

from branch_review import __version__


def test_version_is_a_nonempty_string() -> None:
    assert isinstance(__version__, str)
    assert __version__
