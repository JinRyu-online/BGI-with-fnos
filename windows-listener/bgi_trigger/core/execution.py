"""BetterGI 启动与完成判定模块。

完成判定（三者优先级递减，先到先得）：
  B - 游戏进程退出（is_game_running() 返回 False）
  C - BetterGI 最新日志命中 log_done_keyword（通过注入的 LogHarvester 缓冲查询）
  D - 24 小时硬编码兜底（任务挂过久无人管）

此外，每轮检查 abort 信号，命中即返回 "aborted"。

注入资源：
  - is_game_running: B 谓词
  - harvester: LogHarvester 实例（None 表示 C 禁用）
  - log_done_keyword: str（空表示 C 禁用）
  - jobs: JobStore 实例（用于响应 /abort）
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:  # 避免运行时循环导入
    from bgi_trigger.core.log_harvester import LogHarvester
    from bgi_trigger.core.state import JobStore

log = logging.getLogger("bgi_trigger.execution")

# 任务运行上限（安全兜底）。B/C 之一命中即提前结束；超出此时长强制终结。
# 不可配置：作为"有人忘了关"的最后安全网。
MAX_TASK_DURATION_SEC = 24 * 3600  # 24 小时


def build_command(bettergi_exe: str, groups: list[str]) -> list[str]:
    """构造 BetterGI 启动命令行参数列表。

    形如：["D:\\BGI\\BetterGI.exe", "--startGroups", "日常一条龙", "关闭游戏"]
    """
    exe = bettergi_exe.strip()
    return [exe, "--startGroups", *groups]


class CompletionMonitor:
    """完成判定监视器：轮询 B/C 谓词，返回首个命中原因。

    每轮检查顺序：先 abort，再 B（游戏进程退出），再 C（日志关键字），再 D（超时）。
    因此当同一轮多个条件同时命中时，abort > B > C > D（确定性选择）。
    """

    def __init__(
        self,
        *,
        is_game_running: Callable[[], bool],
        harvester: LogHarvester | None,
        log_done_keyword: str,
        jobs: JobStore,
        poll_interval: float = 2.0,
    ) -> None:
        self._is_game_running = is_game_running
        self._harvester = harvester
        self._log_done_keyword = log_done_keyword
        self._jobs = jobs
        self._poll_interval = poll_interval
        # C 启用条件：harvester 与 keyword 都非空
        c_enabled = harvester is not None and bool(log_done_keyword)
        # ★ 诊断日志：记录判定器启动时的能力状态
        log.info("CompletionMonitor start: poll_interval=%.1fs, "
                 "checker_B(game_process)=yes, checker_C(log_keyword)=%s, "
                 "checker_D(24h safety net)=yes, checker_abort=yes",
                 poll_interval, "yes" if c_enabled else "disabled")

    def wait(self, timeout_sec: float = MAX_TASK_DURATION_SEC) -> str:
        """阻塞等待完成，返回原因："log_keyword" / "game_exited" / "aborted" / "timeout"。

        timeout_sec 默认 24h 硬编码兜底；调用方可传更小值用于单测模拟。
        """
        deadline = time.monotonic() + timeout_sec
        poll_count = 0
        while True:
            poll_count += 1
            remaining = deadline - time.monotonic()

            # 0. 响应外部 abort（★ 新增）
            if self._jobs.abort_requested():
                log.info("completion interrupted [A:aborted] at poll #%d "
                         "(%.1fs remaining): abort signal detected, "
                         "stopping monitor", poll_count, remaining)
                return "aborted"

            # B：游戏进程退出
            if not self._is_game_running():
                log.info("completion detected [B:game_exited] at poll #%d "
                         "(%.1fs remaining): game_process not found, "
                         "stopping monitor", poll_count, remaining)
                return "game_exited"

            # C：日志关键字命中（仅 harvester 与 keyword 都非空时启用）
            if self._harvester is not None and self._log_done_keyword:
                ok, matched_line = self._harvester.check_keyword(self._log_done_keyword)
                if ok:
                    # ★ 命中时记录匹配行原文，方便日后排查误命中
                    log.info("completion detected [C:log_keyword] at poll #%d "
                             "(%.1fs remaining): keyword=%r matched, line=%r, "
                             "stopping monitor",
                             poll_count, remaining, self._log_done_keyword,
                             matched_line[:200])
                    return "log_keyword"

            # D：24h 硬编码超时兜底
            if time.monotonic() >= deadline:
                log.info("completion detected [D:timeout] at poll #%d "
                         "(%.1fs remaining): timeout_sec=%.0f expired, "
                         "stopping monitor", poll_count, remaining, timeout_sec)
                return "timeout"

            # ★ 诊断日志：只在每 30 次轮询（约 60 秒）打一次节拍，
            #   让"判定还在跑"可观察，又不会刷爆日志
            if poll_count % 30 == 0:
                log.debug("completion poll #%d: still waiting (%.0fs remaining), "
                          "game_running=True", poll_count, remaining)

            time.sleep(self._poll_interval)


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
