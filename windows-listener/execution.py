"""BetterGI 启动与完成判定模块。

完成判定（三者任一触发，先到先得）：
  B - 游戏进程退出（is_game_running() 返回 False）
  C - BetterGI 日志出现完成关键字（log_done() 返回 True），仅在配置启用时生效
  D - 超时（在 timeout_sec 内 B/C 均未触发）

判定谓词（is_game_running / log_done）以可调用对象注入，使 CompletionMonitor
可在不依赖真实进程/日志的情况下单元测试。make_game_checker / make_log_checker
为面向真实环境的谓词工厂。
"""
from __future__ import annotations

import time
from typing import Callable


def build_command(bettergi_exe: str, groups: list[str]) -> list[str]:
    """构造 BetterGI 启动命令行参数列表。

    形如：["D:\\BGI\\BetterGI.exe", "--startGroups", "日常一条龙", "关闭游戏"]
    """
    exe = bettergi_exe.strip()
    return [exe, "--startGroups", *groups]


class CompletionMonitor:
    """完成判定监视器：轮询 B/C 谓词，返回首个命中原因或超时。

    每轮检查顺序：先 B（游戏进程退出），再 C（日志关键字），再判超时。
    因此当同一轮 B、C 同时命中时，B 优先（确定性选择）。
    """

    def __init__(
        self,
        is_game_running: Callable[[], bool],
        log_done: Callable[[], bool] | None = None,
        poll_interval: float = 2.0,
    ) -> None:
        self._is_game_running = is_game_running
        self._log_done = log_done  # None 表示 C 禁用
        self._poll_interval = poll_interval

    def wait(self, timeout_sec: float) -> str:
        """阻塞等待完成，返回原因字符串：game_exited / log_keyword / timeout。"""
        deadline = time.monotonic() + timeout_sec
        while True:
            # B：游戏进程退出
            if not self._is_game_running():
                return "game_exited"
            # C：日志关键字命中（仅启用时检查）
            if self._log_done is not None and self._log_done():
                return "log_keyword"
            # D：超时
            if time.monotonic() >= deadline:
                return "timeout"
            time.sleep(self._poll_interval)


def make_log_checker(log_path: str, keyword: str) -> Callable[[], bool] | None:
    """构造 C 谓词：当日志文件中出现 keyword 时返回 True。

    path 或 keyword 为空时返回 None（C 禁用）。
    每次调用读取整个日志文件（日志量不大，简单可靠），文件不可读时返回 False。
    """
    if not log_path or not keyword:
        return None
    path = log_path

    def _check() -> bool:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return keyword in f.read()
        except OSError:
            return False

    return _check


def make_game_checker(process_names: list[str]):
    """构造 B 谓词：当任一指定游戏进程在运行时返回 True。

    使用 psutil 枚举进程。psutil 延迟导入，使本模块在无 psutil 环境下
    仍可导入（便于纯逻辑单元测试）。
    """
    import psutil  # 延迟导入：非硬依赖，便于单测

    names = set(process_names)

    def _check() -> bool:
        try:
            for p in psutil.process_iter(["name"]):
                if p.info["name"] in names:
                    return True
        except psutil.Error:
            pass
        return False

    return _check
