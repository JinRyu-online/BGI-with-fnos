"""真实任务启动器：拉起 BetterGI、后台监控完成、执行收尾动作。

启动回调（launch）由 HTTP 层在 /trigger 时调用，需非阻塞——因此实际
工作放在守护线程中执行，/trigger 立即返回 job_id。

执行流程（守护线程内）：
  1. 构造命令行并 Popen BetterGI；
  2. CompletionMonitor 轮询 B/C，超时上限 = task.timeout_min * 60；
  3. 命中后 mark_completing（进入 grace 反悔窗口），等待 grace_seconds；
     期间若被 /abort 置为 aborted，则跳过收尾；
  4. finalize（done/timeout），清理残留 BetterGI 进程；
  5. 按 task.after_done 执行收尾（sleep/shutdown/lock/none）。

异常时标记 failed，不执行收尾。
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from typing import Callable

from execution import CompletionMonitor, build_command, make_game_checker, make_log_checker
from state import Job, JobStore, JobState

log = logging.getLogger("bgi_trigger.launcher")

# 收尾动作 → 系统命令映射。
# sleep   休眠（可被 WOL 唤醒，默认）
# shutdown 关机
# lock    锁屏（不关机，保留登录态）
_AFTER_DONE_COMMANDS = {
    "sleep": ["shutdown", "/h"],
    "shutdown": ["shutdown", "/s", "/t", "0"],
    "lock": ["rundll32", "user32.dll,LockWorkStation"],
}


def after_done_command(action: str) -> list[str] | None:
    """将收尾动作名转为系统命令列表。none 返回 None，未知动作抛 ValueError。"""
    if action == "none":
        return None
    try:
        return _AFTER_DONE_COMMANDS[action]
    except KeyError:
        raise ValueError(f"unknown after_done action: {action!r}")


class Launcher:
    """任务启动器。构造时注入配置与任务存储；以 `launcher(job, task)` 形式调用启动任务。

    subprocess_runner 与 sleep 可注入，便于测试替换。
    """

    def __init__(
        self,
        bettergi_exe: str,
        game_processes: list[str],
        log_path: str,
        log_done_keyword: str,
        grace_seconds: int,
        jobs: JobStore,
        subprocess_runner: Callable = subprocess.Popen,
        sleep: Callable = time.sleep,
    ) -> None:
        self._exe = bettergi_exe
        self._game_processes = game_processes
        self._log_path = log_path
        self._log_keyword = log_done_keyword
        self._grace = grace_seconds
        self._jobs = jobs
        self._popen = subprocess_runner
        self._sleep = sleep

    def __call__(self, job: Job, task) -> None:
        """非阻塞启动：开守护线程执行实际工作。"""
        t = threading.Thread(target=self._run, args=(job, task), daemon=True)
        t.start()

    def _run(self, job: Job, task) -> None:
        """线程入口：捕获异常并标记 failed。"""
        try:
            self._execute(job, task)
        except Exception:
            log.exception("job %s failed", job.id)
            self._jobs.mark_completing("error")
            self._jobs.finalize(JobState.FAILED)

    def _execute(self, job: Job, task) -> None:
        """实际执行流程（见模块文档）。"""
        cmd = build_command(self._exe, task.groups)
        log.info("launching BetterGI for job %s: %s", job.id, cmd)
        proc = self._popen(cmd)

        monitor = CompletionMonitor(
            is_game_running=make_game_checker(self._game_processes),
            log_done=make_log_checker(self._log_path, self._log_keyword),
            poll_interval=2.0,
        )
        reason = monitor.wait(timeout_sec=task.timeout_min * 60)
        log.info("job %s completion reason: %s", job.id, reason)

        self._jobs.mark_completing(reason)
        # 反悔窗口：期间用户可通过 /abort 中止，跳过收尾。
        self._sleep(self._grace)
        if job.state == JobState.ABORTED:
            log.info("job %s aborted during grace window; skipping after_done", job.id)
            return

        final = JobState.DONE if reason != "timeout" else JobState.TIMEOUT
        self._jobs.finalize(final)

        # 清理可能仍残留的 BetterGI 进程。
        self._terminate(proc)

        # 执行收尾动作。
        cmd_after = after_done_command(task.after_done)
        if cmd_after:
            log.info("running after_done: %s", cmd_after)
            try:
                subprocess.run(cmd_after, check=False)
            except Exception:
                log.exception("after_done command failed")

    @staticmethod
    def _terminate(proc) -> None:
        """若 BetterGI 进程仍在运行，尝试 terminate。"""
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            log.exception("failed to terminate BetterGI process")
