import pytest

from execution import build_command, CompletionMonitor


def test_build_command_with_groups():
    cmd = build_command("D:\\BGI\\BetterGI.exe", ["日常一条龙", "关闭游戏"])

    assert cmd == ["D:\\BGI\\BetterGI.exe", "--startGroups", "日常一条龙", "关闭游戏"]


def test_build_command_strips_exe_whitespace():
    cmd = build_command("  D:\\BGI\\BetterGI.exe  ", ["g1"])

    assert cmd[0] == "D:\\BGI\\BetterGI.exe"


class _Scripted:
    """Returns scripted values from a list, then repeats the last one."""

    def __init__(self, values):
        self._values = list(values)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if not self._values:
            return False
        if len(self._values) == 1:
            return self._values[0]
        return self._values.pop(0)


def test_completion_on_game_exit():
    # game running True for 2 polls, then False -> game_exited
    game = _Scripted([True, True, False])
    log = _Scripted([False])
    mon = CompletionMonitor(is_game_running=game, log_done=log, poll_interval=0)

    reason = mon.wait(timeout_sec=5)

    assert reason == "game_exited"


def test_completion_on_log_keyword_when_C_enabled():
    # game still running, but log_done fires -> log_keyword wins
    game = _Scripted([True, True, True])
    log = _Scripted([False, True])
    mon = CompletionMonitor(is_game_running=game, log_done=log, poll_interval=0)

    reason = mon.wait(timeout_sec=5)

    assert reason == "log_keyword"


def test_C_disabled_only_game_exit_matters():
    # log_done=None -> C disabled; game exits -> game_exited
    game = _Scripted([True, False])
    mon = CompletionMonitor(is_game_running=game, log_done=None, poll_interval=0)

    reason = mon.wait(timeout_sec=5)

    assert reason == "game_exited"


def test_race_C_fires_before_B():
    # both eventually fire, but C first
    game = _Scripted([True, True, False])
    log = _Scripted([False, True, True])
    mon = CompletionMonitor(is_game_running=game, log_done=log, poll_interval=0)

    reason = mon.wait(timeout_sec=5)

    assert reason == "log_keyword"


def test_timeout_when_neither_fires():
    game = _Scripted([True])  # always running
    log = _Scripted([False])  # never done
    mon = CompletionMonitor(is_game_running=game, log_done=log, poll_interval=0)

    reason = mon.wait(timeout_sec=0.05)

    assert reason == "timeout"


def test_C_disabled_and_game_never_exits_times_out():
    game = _Scripted([True])
    mon = CompletionMonitor(is_game_running=game, log_done=None, poll_interval=0)

    reason = mon.wait(timeout_sec=0.05)

    assert reason == "timeout"
