import pytest

from bgi_trigger.core.execution import build_command, CompletionMonitor
from bgi_trigger.core.state import JobState, JobStore


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


class _FakeHarvester:
    """测试用 fake：脚本化返回 (命中, 匹配行)。"""

    def __init__(self, results):
        # results: list of (bool, str)
        self._results = list(results)

    def check_keyword(self, keyword: str):
        if not self._results:
            return False, ""
        return self._results.pop(0)


def test_completion_on_game_exit():
    # game running True for 2 polls, then False -> game_exited
    store = JobStore()
    store.start("t", ["g"])
    game = _Scripted([True, True, False])
    mon = CompletionMonitor(is_game_running=game, harvester=None, log_done_keyword="",
                            jobs=store, poll_interval=0)

    reason = mon.wait(timeout_sec=5)

    assert reason == "game_exited"


def test_completion_on_log_keyword_when_C_enabled():
    # game still running, but harvester hits keyword -> log_keyword wins
    store = JobStore()
    store.start("t", ["g"])
    game = _Scripted([True, True, True])
    harvester = _FakeHarvester([(False, ""), (True, "调度组执行完成")])
    mon = CompletionMonitor(is_game_running=game, harvester=harvester,
                            log_done_keyword="调度组执行完成",
                            jobs=store, poll_interval=0)

    reason = mon.wait(timeout_sec=5)

    assert reason == "log_keyword"


def test_C_disabled_only_game_exit_matters():
    # harvester=None and keyword="" -> C disabled; game exits -> game_exited
    store = JobStore()
    store.start("t", ["g"])
    game = _Scripted([True, False])
    mon = CompletionMonitor(is_game_running=game, harvester=None, log_done_keyword="",
                            jobs=store, poll_interval=0)

    reason = mon.wait(timeout_sec=5)

    assert reason == "game_exited"


def test_race_C_fires_before_B():
    # both eventually fire, but C first (C checked earlier in same poll)
    store = JobStore()
    store.start("t", ["g"])
    game = _Scripted([True, True, False])      # B fires on 3rd poll
    harvester = _FakeHarvester([(False, ""), (True, "done")])  # C fires on 2nd poll
    mon = CompletionMonitor(is_game_running=game, harvester=harvester,
                            log_done_keyword="done",
                            jobs=store, poll_interval=0)

    reason = mon.wait(timeout_sec=5)

    assert reason == "log_keyword"


def test_timeout_when_neither_fires_with_small_timeout():
    """24h 超时模拟：用 timeout_sec=0.05 短值模拟 B/C 都不命中的场景。"""
    store = JobStore()
    store.start("t", ["g"])
    game = _Scripted([True])                   # always running
    harvester = _FakeHarvester([(False, "")])   # never done
    mon = CompletionMonitor(is_game_running=game, harvester=harvester,
                            log_done_keyword="完成",
                            jobs=store, poll_interval=0)

    reason = mon.wait(timeout_sec=0.05)

    assert reason == "timeout"


def test_C_disabled_and_game_never_exits_times_out():
    store = JobStore()
    store.start("t", ["g"])
    game = _Scripted([True])
    mon = CompletionMonitor(is_game_running=game, harvester=None, log_done_keyword="",
                            jobs=store, poll_interval=0)

    reason = mon.wait(timeout_sec=0.05)

    assert reason == "timeout"


def test_abort_interrupts_early():
    """外部 /abort 信号应打断 wait，返回 aborted。"""
    store = JobStore()
    store.start("t", ["g"])
    game = _Scripted([True, True, True])
    harvester = _FakeHarvester([(False, ""), (False, "")])
    mon = CompletionMonitor(is_game_running=game, harvester=harvester,
                            log_done_keyword="done",
                            jobs=store, poll_interval=0)

    # 第 2 轮时触发 abort
    def trigger_abort_later():
        import time
        time.sleep(0.02)
        store.abort()

    import threading
    threading.Thread(target=trigger_abort_later, daemon=True).start()

    reason = mon.wait(timeout_sec=5)

    assert reason == "aborted"
