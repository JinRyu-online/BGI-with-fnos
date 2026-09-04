"""CompletionMonitor 实况对账（seen_game 机制）补充测试。

场景：拉起后 BetterGI 崩了、游戏从未起来 → 不能挂到超时兜底，应返回 "failed"；
游戏出现过再消失 → 维持原 B 行为（game_exited），不误报。
"""
import time

from bgi_trigger.core.execution import CompletionMonitor
from bgi_trigger.core.state import JobStore


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


def _store() -> JobStore:
    s = JobStore()
    s.start("t", ["g"])
    return s


def test_bettergi_gone_game_never_seen_returns_failed():
    """游戏从未出现 + BetterGI 消失 → "failed"（执行器已死，不干等超时）。

    场景：预热期内 BetterGI 还活着（游戏尚未拉起），预热刚过 BetterGI 就崩了
    （游戏始终没出现）→ 对账命中 failed。
    """
    store = _store()
    game = _Scripted([False])   # 游戏从未出现
    grace_end = time.monotonic() + 0.3   # 与 monitor 的 grace 时钟对齐（时钟不会倒退）
    bettergi = lambda: time.monotonic() < grace_end   # 预热期内活着，之后崩
    mon = CompletionMonitor(is_game_running=game, harvester=None,
                            log_done_keyword="", jobs=store,
                            poll_interval=0.02, startup_grace=0.3,
                            is_bettergi_running=bettergi)
    assert mon.wait(timeout_sec=5) == "failed"


def test_bettergi_gone_game_seen_returns_game_exited():
    """游戏出现过再退出 → 维持 B 行为 game_exited，不误报 failed。"""
    store = _store()
    # 前 2 轮游戏在跑（seen_game 置位），第 3 轮游戏退出
    game = _Scripted([True, True, False])
    bettergi = _Scripted([False])   # BetterGI 检查从未命中（如 handoff 模式）
    mon = CompletionMonitor(is_game_running=game, harvester=None,
                            log_done_keyword="", jobs=store,
                            poll_interval=0, startup_grace=0,
                            is_bettergi_running=bettergi)
    assert mon.wait(timeout_sec=5) == "game_exited"


def test_reconcile_disabled_when_injection_is_none():
    """is_bettergi_running=None（默认）→ 对账禁用，旧行为 game_exited。"""
    store = _store()
    game = _Scripted([True, False])
    mon = CompletionMonitor(is_game_running=game, harvester=None,
                            log_done_keyword="", jobs=store,
                            poll_interval=0, startup_grace=0)
    assert mon.wait(timeout_sec=5) == "game_exited"


def test_reconcile_not_triggered_while_bettergi_still_running():
    """预热期后游戏仍没起来、但 BetterGI 还在跑 → 对账不命中，
    维持原 B 行为返回 game_exited（BetterGI 活着说明执行器未死）。"""
    store = _store()
    game = _Scripted([False])
    bettergi = _Scripted([True])   # BetterGI 一直在
    mon = CompletionMonitor(is_game_running=game, harvester=None,
                            log_done_keyword="", jobs=store,
                            poll_interval=0.01, startup_grace=0.05,
                            is_bettergi_running=bettergi)
    # 对账要求 BetterGI 消失 → 不命中 → B（游戏退出）在预热期后正常触发
    assert mon.wait(timeout_sec=5) == "game_exited"


def test_reconcile_during_startup_grace_not_triggered():
    """预热期内游戏/BetterGI 都不在 → 不触发（B 与对账均被预热期抑制）。"""
    store = _store()
    game = _Scripted([False])
    bettergi = _Scripted([False])
    mon = CompletionMonitor(is_game_running=game, harvester=None,
                            log_done_keyword="", jobs=store,
                            poll_interval=0.01, startup_grace=5.0,
                            is_bettergi_running=bettergi)
    assert mon.wait(timeout_sec=0.08) == "timeout"
