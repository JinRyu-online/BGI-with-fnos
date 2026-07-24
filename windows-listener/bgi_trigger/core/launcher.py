"""真实任务启动器：拉起 BetterGI、后台监控完成、执行收尾动作。

启动回调（launch）由 HTTP 层在 /trigger 时调用，需非阻塞——因此实际
工作放在守护线程中执行，/trigger 立即返回 job_id。

执行流程（守护线程内）：
  1. 构造命令行并 Popen BetterGI；
  2. CompletionMonitor 轮询 B/C/D(24h 硬编码兜底),命中即返回 reason；
  3. 命中后 mark_completing(进入 grace 反悔窗口),等待 grace_seconds；
     期间若被 /abort 置为 aborted,则跳过收尾；
  4. finalize(根据 reason 映射到 DONE/ABNORMAL_EXIT/TIMED_OUT 终态),
     清理残留 BetterGI 进程；
  5. 仅 DONE 时按 task.after_done 执行收尾(sleep/shutdown/lock/none)。

异常时标记 failed，不执行收尾。
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from typing import Callable

from bgi_trigger.core.execution import CompletionMonitor, build_command, make_game_checker
from bgi_trigger.core.log_harvester import LogHarvester
from bgi_trigger.core.state import Job, JobStore, JobState

# ★ Windows:子进程创建标志。
# CREATE_NEW_PROCESS_GROUP 让子进程成为新的进程组长,接收 Ctrl+C。
# CREATE_UNIVERSAL:WINDOWS  + 不继承父进程句柄 → 允许子进程访问
# 交互式窗口站(Window Station)和桌面,避免计划任务 /RL HIGHEST
# 下 BetterGI 看不到游戏窗口。
# 在非 Windows 平台回退到 0。
if sys.platform == "win32":
    # CREATE_NEW_PROCESS_GROUP:子进程为新进程组长 + 自带控制台窗口,
    # 避免继承 pythonw.exe 的无控制台句柄导致 BetterGI 无法看见游戏窗口。
    _SUBPROCESS_CREATION_FLAGS = subprocess.CREATE_NEW_PROCESS_GROUP
else:
    _SUBPROCESS_CREATION_FLAGS = 0


def _default_popen(cmd: list[str], **kwargs) -> subprocess.Popen:
    """Popen 默认实现:在 Windows 上用 creationflags 让子进程前台可见。"""
    if sys.platform == "win32":
        kwargs.setdefault("creationflags", _SUBPROCESS_CREATION_FLAGS)
    return subprocess.Popen(cmd, **kwargs)

log = logging.getLogger("bgi_trigger.launcher")

# ★ job_id → LogHarvester registry(模块级)。
# WS 端点通过它找到 job 对应的 harvester,读取日志流。
_harvesters: dict[str, LogHarvester] = {}
_harvesters_lock = threading.Lock()


def get_harvester(job_id: str) -> LogHarvester | None:
    """WS 端点使用:按 job_id 查找日志收割器(进行中/刚完成均有)。"""
    with _harvesters_lock:
        return _harvesters.get(job_id)

# 收尾动作 → 系统命令映射。
# sleep   休眠（可被 WOL 唤醒，默认）
# shutdown 关机
# lock    锁屏（不关机，保留登录态）
_AFTER_DONE_COMMANDS = {
    "sleep": ["shutdown", "/h"],
    "shutdown": ["shutdown", "/s", "/t", "0"],
    "lock": ["rundll32", "user32.dll,LockWorkStation"],
}


def _job_log_save_path(job_id: str) -> Path | None:
    """计算 job 对应的全量日志落盘路径。

    ★ 落地位置：windows-listener/log/{job_id}.log
    与 listener 自己的运行时日志同目录，便于统一归档与清理。
    （不放在 BetterGI 日志目录，避免污染 BetterGI 自带日志）
    """
    try:
        # 延迟导入：listener.py 定义了 BASE_DIR；launcher 启动时必然已有
        from listener import BASE_DIR
        return BASE_DIR / "log" / f"{job_id}.log"
    except Exception:
        return None


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
        log_done_mode: str = "count",
        subprocess_runner: Callable | None = None,
        sleep: Callable = time.sleep,
    ) -> None:
        self._exe = bettergi_exe
        self._game_processes = game_processes
        self._log_path = log_path
        self._log_keyword = log_done_keyword
        # ★ "count" → required_matches=组数; "first" → required_matches=1(旧行为)
        self._log_done_mode = log_done_mode
        self._grace = grace_seconds
        self._jobs = jobs
        # ★ 默认用_windows 友好的 Popen(前台可见);测试可注入 fake
        self._popen = subprocess_runner or _default_popen
        self._sleep = sleep

    def __call__(self, job: Job, task) -> None:
        """非阻塞启动：开守护线程执行实际工作。"""
        # ★ job 已携带 log_path(由 app.py 注入);有则启动 harvester
        if job.log_path:
            # ★ 完成判定 C 的触发阈值:
            #   count 模式 → required_matches = 组数(每个组结束都打 keyword,
            #           只有累计命中组数那次=最后一组才触发,避免中间组误触发)。
            #   first 模式 → required_matches = 1(旧行为,首次命中即触发)。
            if self._log_done_mode == "first":
                required = 1
            else:
                required = max(1, len(job.groups))
            h = LogHarvester(job_id=job.id, log_path=job.log_path,
                             save_path=_job_log_save_path(job.id),
                             required_matches=required)
            h.set_keyword(self._log_keyword)   # ★ 启用计数模式
            with _harvesters_lock:
                _harvesters[job.id] = h
            h.start()
        t = threading.Thread(target=self._run, args=(job, task), daemon=True)
        t.start()

    def _run(self, job: Job, task) -> None:
        """线程入口：捕获异常并标记 failed。"""
        try:
            _BetterGIExecutor(self).execute(job, task)
        except Exception:
            log.exception("job %s failed", job.id)
            self._jobs.mark_completing("error")
            self._jobs.finalize(JobState.FAILED)
        finally:
            # ★ 任务结束(任意终态)后停止收割并标记 last=True
            with _harvesters_lock:
                h = _harvesters.get(job.id)
            if h is not None:
                h.mark_finished()
                # ★ 保留 harvester 5 分钟给 WS 读完最后日志,然后清理
                t = threading.Timer(300.0, lambda: _cleanup_harvester(job.id))
                t.daemon = True
                t.start()


def _cleanup_harvester(job_id: str) -> None:
    """延迟清理 harvester(给 WS 留出读完最后日志的时间)。"""
    with _harvesters_lock:
        h = _harvesters.pop(job_id, None)
    if h is not None:
        h.stop()


class _BetterGIExecutor:
    """BetterGI 单任务执行体(从 Launcher 拆出,避免 _execute 缩进错误)。

    由 Launcher._run 调用,封装从拉起进程到收尾的全流程。
    """

    def __init__(self, launcher: "Launcher") -> None:
        self._exe = launcher._exe
        self._game_processes = launcher._game_processes
        self._log_path = launcher._log_path
        self._log_keyword = launcher._log_keyword
        self._grace = launcher._grace
        self._jobs = launcher._jobs
        self._popen = launcher._popen
        self._sleep = launcher._sleep
        # ★ 启动前检测：BetterGI / 游戏是否已在运行（手动启动时也适用）。
        # 用进程文件名匹配，与 make_game_checker 同一逻辑（psutil 软依赖已延迟导入）。
        bettergi_name = os.path.basename(self._exe)
        self._is_bettergi_running = make_game_checker([bettergi_name])
        self._is_game_running = make_game_checker(self._game_processes)

    def execute(self, job: Job, task) -> None:
        """实际执行流程（见模块文档）。"""
        # ★ 启动前检测：BetterGI 或 游戏 任一已在运行 → 跳过拉起，直接接管监控。
        # 覆盖场景：用户手动启动的 BetterGI+原神（槽位是空的，单槽保护拦不到），
        # 此时再 /trigger 不会起第二个实例，而是监听已有实例直到完成。
        bettergi_running = self._is_bettergi_running()
        game_running = self._is_game_running()
        proc: subprocess.Popen | None = None
        if bettergi_running or game_running:
            log.info("job %s: already running (BetterGI=%s, game=%s); skip launching, "
                     "hand off to completion monitor", job.id, bettergi_running, game_running)
        else:
            cmd = build_command(self._exe, task.groups)
            log.info("launching BetterGI for job %s: %s", job.id, cmd)
            proc = self._popen(cmd)

        # ★ 取本机已启动的 harvester(由 __call__ 提前创建),注入 monitor
        with _harvesters_lock:
            harvester = _harvesters.get(job.id)
        monitor = CompletionMonitor(
            is_game_running=self._is_game_running,
            harvester=harvester,                    # None = C 禁用
            log_done_keyword=self._log_keyword,     # 空 = C 禁用
            jobs=self._jobs,                        # 用于响应 abort
            poll_interval=2.0,
        )
        reason = monitor.wait()   # timeout_sec 默认 24h 硬编码
        log.info("job %s completion reason: %s", job.id, reason)

        self._jobs.mark_completing(reason)

        # 反悔窗口：期间用户可通过 /abort 中止，跳过收尾。
        # ★ 改用循环 + 检查 abort 信号,确保 abort 后立即退出,不阻塞 _grace 秒。
        deadline = self._grace
        while deadline > 0:
            if self._jobs.abort_requested():
                log.info("job %s abort requested during grace window; exiting immediately", job.id)
                # ★ grace 窗口内 abort：尚未 finalize(仍 COMPLETING)，需在此转为 ABORTED
                if self._jobs.current is job and job.state == JobState.COMPLETING:
                    self._jobs.finalize(JobState.ABORTED)
                    log.info("job %s finalized as ABORTED from grace window", job.id)
                return
            step = min(0.5, deadline)
            self._sleep(step)
            deadline -= step
        log.info("job %s grace window (%.0fs) expired, proceeding to finalize with reason=%s",
                 job.id, self._grace, reason)

        # 再次取出 current:如果 abort 已清槽位,job 对象已被替换;跳过所有操作。
        if self._jobs.current is not job:
            log.info("job %s slot cleared (abort/restart); skipping finalize and after_done", job.id)
            return
        if job.state == JobState.ABORTED:
            log.info("job %s aborted; skipping after_done", job.id)
            return

        # ★ 新映射：reason → JobState
        if reason == "log_keyword":
            final = JobState.DONE
        elif reason == "game_exited":
            final = JobState.ABNORMAL_EXIT
        elif reason == "timeout":
            final = JobState.TIMED_OUT
        elif reason == "aborted":
            final = JobState.ABORTED
        else:   # "error" 等内部异常
            final = JobState.FAILED
        self._jobs.finalize(final)
        log.info("job %s finalized: reason=%s → state=%s", job.id, reason, final.value)

        # ★ 仅正常完成(DONE)执行 after_done；异常/超时/失败/中止 都不动手
        if final != JobState.DONE:
            log.info("job %s: final state is %s (not DONE), skipping after_done",
                     job.id, final.value)
            return

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
        """若 BetterGI 进程仍在运行，尝试 terminate。
        proc 为 None 表示本次 job 未拉起新进程（已运行，跳过），无需清理。"""
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            log.exception("failed to terminate BetterGI process")
