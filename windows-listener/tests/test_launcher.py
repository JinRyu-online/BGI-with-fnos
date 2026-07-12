import pytest

from bgi_trigger.core.launcher import after_done_command


def test_sleep_returns_hibernate():
    assert after_done_command("sleep") == ["shutdown", "/h"]


def test_shutdown_command():
    assert after_done_command("shutdown") == ["shutdown", "/s", "/t", "0"]


def test_lock_command():
    assert after_done_command("lock") == ["rundll32", "user32.dll,LockWorkStation"]


def test_none_returns_none():
    assert after_done_command("none") is None


def test_unknown_raises():
    with pytest.raises(ValueError):
        after_done_command("reboot")
