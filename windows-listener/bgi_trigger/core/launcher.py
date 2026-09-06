"""真实任务启动器：拉起 BetterGI、后台监控完成、执行收尾动作。

启动回调（launch）由 HTTP 层在 /trigger 时调用，需非阻塞——因此实际
工作放在守护线程中执行，/trigger 立即返回 job_id。

执行流程（守护线程内）：
  1. 启动预检：BetterGI/游戏是否已在运行（见 _BetterGIExecutor.execute 三分支）；
  2. 构造命令行并 Popen BetterGI；
  3. CompletionMonitor 轮询 B/C/E 与 D（min(task.timeout_min*60, 24h) 兜底），
     命中即返回 reason；
  4. 命中后 mark_completing(进入 grace 反悔窗口),等待 grace_seconds；
     期间若被 /abort 置为 aborted,则跳过收尾；
  5. finalize(根据 reason 映射到 DONE/ABNORMAL_EXIT/TIMED_OUT 终态),
     清理残留 BetterGI 进程；
  6. 仅 DONE 时按 task.after_done 执行收尾(sleep/shutdown/lock/none)。

异常时标记 failed，不执行收尾。

/abort 主动杀进程：Launcher 记住本 job 拉起的 proc（self._current_proc），
HTTP 层 abort 时通过 terminate_current_proc() 对其 terminate → wait(5) → kill。
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from bgi_trigger.core.execution import (
    MAX_TASK_DURATION_SEC,
    CompletionMonitor,
    build_command,
    kill_processes,
    make_game_checker,
)
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
    # pre_launch_script 用:pythonw 下跑 shell 命令不弹控制台闪窗
    _PRE_LAUNCH_CREATION_FLAGS = subprocess.CREATE_NO_WINDOW
else:
    _SUBPROCESS_CREATION_FLAGS = 0
    _PRE_LAUNCH_CREATION_FLAGS = 0


def _default_pre_launch_runner(script: str) -> None:
    """pre_launch_script 默认执行器:cmd.exe(shell=True)+ 60s 超时 + 不弹窗。

    非零退出码/超时由调用方按 WARNING 容错(不阻断 BetterGI 拉起)。
    注意:超时只 kill cmd 直接子进程,PowerShell 等孙进程可能存活——
    脚本须自行保证短命(文档已注明)。
    """
    subprocess.run(script, shell=True, timeout=60,
                   creationflags=_PRE_LAUNCH_CREATION_FLAGS)


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

    log_save_dir: job 全量日志落盘目录（None=不落盘）。listener 传入 BASE_DIR / "log"。
    line_sink: 日志收割桥回调，每收割一行调用一次（listener 注入 logging 回显）。
    abort_kills_game: /abort 时是否同时终止游戏进程（默认 True，可被配置覆盖）。
    pre_launch_runner: 拉起前脚本执行器（注入便于测试替换，默认 cmd.exe + timeout 60
        + CREATE_NO_WINDOW）。handoff 分支不执行；失败仅 WARNING 不阻断。
    pre_launch_script: 拉起前脚本内容（来自 [execution] pre_launch_script，空=禁用）。
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
        log_save_dir: str | Path | None = None,
        line_sink: Callable[[str], None] | None = None,
        abort_kills_game: bool = True,
        pre_launch_script: str = "",
        pre_launch_runner: Callable[[str], None] | None = None,
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
        # ★ job 全量日志落盘目录(None=不落盘);取代旧版对 listener.BASE_DIR 的反向依赖
        self._log_save_dir = Path(log_save_dir) if log_save_dir else None
        # ★ 控制台日志桥:harvester 每收割一行回调一次
        self._line_sink = line_sink
        # ★ /abort 是否同时终止游戏进程
        self._abort_kills_game = abort_kills_game
        # ★ 拉起前脚本内容（"" = 禁用）与执行器（None=默认 cmd 实现；测试注入 fake）
        self._pre_launch_script = pre_launch_script
        self._pre_launch_runner = pre_launch_runner or _default_pre_launch_runner
        # ★ 本 job 拉起的 BetterGI 进程(由 launch 守护线程赋值,供 /abort 主动终止)
        self._current_proc: subprocess.Popen | None = None
        self._proc_lock = threading.Lock()

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
                             save_path=self._job_log_save_path(job.id),
                             required_matches=required,
                             line_sink=self._line_sink)
            h.set_keyword(self._log_keyword)   # ★ 启用计数模式
            with _harvesters_lock:
                _harvesters[job.id] = h
            h.start()
        t = threading.Thread(target=self._run, args=(job, task), daemon=True)
        t.start()

    def _job_log_save_path(self, job_id: str) -> Path | None:
        """计算 job 对应的全量日志落盘路径。

        ★ 落地位置：{log_save_dir}/{job_id}.log（log_save_dir 由构造注入，
        listener 传 BASE_DIR / "log"）。None=不落盘。
        与 listener 自己的运行时日志同目录，便于统一归档与清理。
        （不放在 BetterGI 日志目录，避免污染 BetterGI 自带日志）
        """
        if self._log_save_dir is None:
            return None
        return self._log_save_dir / f"{job_id}.log"

    def current_bettergi_proc(self) -> subprocess.Popen | None:
        """返回当前 job 拉起的 BetterGI 进程（无则 None）。HTTP 层 abort 用。"""
        with self._proc_lock:
            return self._current_proc

    def terminate_current_proc(self) -> None:
        """主动终止本 job 拉起的 BetterGI 进程（/abort 路径调用）。

        terminate → wait(5)，超时 kill → wait(3)。进程不存在或已退出则忽略。
        """
        with self._proc_lock:
            proc = self._current_proc
        if proc is None:
            return
        _terminate_proc(proc)

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


def _terminate_proc(proc) -> None:
    """终止子进程：terminate → wait(5)，超时 kill → wait(3)。

    ★ terminate() 后必须 wait()：否则 Windows 上进程句柄不回收（僵尸），
    且无法确认是否真的退出了。所有 subprocess 异常均捕获，尽力而为。
    """
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
    except Exception:
        log.exception("failed to terminate BetterGI process")
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        log.warning("BetterGI process did not exit in 5s after terminate; killing")
        try:
            proc.kill()
        except Exception:
            log.exception("failed to kill BetterGI process")
            return
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            log.warning("BetterGI process still alive 3s after kill; giving up")
        except Exception:
            log.exception("error waiting BetterGI process after kill")
    except Exception:
        log.exception("error waiting BetterGI process after terminate")


class _BetterGIExecutor:
    """BetterGI 单任务执行体(从 Launcher 拆出,避免 _execute 缩进错误)。

    由 Launcher._run 调用,封装从拉起进程到收尾的全流程。
    """

    def __init__(self, launcher: "Launcher") -> None:
        self._launcher = launcher
        self._exe = launcher._exe
        self._game_processes = launcher._game_processes
        self._log_path = launcher._log_path
        self._log_keyword = launcher._log_keyword
        self._grace = launcher._grace
        self._jobs = launcher._jobs
        self._popen = launcher._popen
        self._sleep = launcher._sleep
        # ★ pre_launch_script 内容与执行器（"" = 禁用）
        self._pre_launch_script = launcher._pre_launch_script
        self._pre_launch_runner = launcher._pre_launch_runner
        # ★ 启动前检测：BetterGI / 游戏是否已在运行（手动启动时也适用）。
        # 用进程文件名匹配，与 make_game_checker 同一逻辑（psutil 软依赖已延迟导入）。
        bettergi_name = os.path.basename(self._exe)
        self._is_bettergi_running = make_game_checker([bettergi_name])
        self._is_game_running = make_game_checker(self._game_processes)
        # ★ 残留 BetterGI 清理用：按 basename 匹配枚举进程
        self._bettergi_name = bettergi_name

    def _kill_residue_bettergi(self) -> list[str]:
        """枚举并终止残留的 BetterGI 进程（按 exe basename 匹配）。"""
        killed = kill_processes([self._bettergi_name])
        if killed:
            # 短暂等待进程完全退出（句柄释放、日志刷盘），随后正常 Popen
            self._sleep(1.0)
        return killed

    def _run_pre_launch_script(self, job: Job) -> bool:
        """执行 pre_launch_script（真正 Popen 分支前；handoff 不执行）。

        返回 False 表示脚本执行期间收到 /abort → 调用方直接归档 aborted、
        不再拉起 BetterGI（消除"abort 空杀后脚本结束仍拉起"的时序洞）。
        脚本非零退出/超时/异常仅 WARNING，不阻断拉起（返回 True）。
        """
        if not self._pre_launch_script:
            return True
        log.info("job %s: running pre_launch_script", job.id)
        try:
            self._pre_launch_runner(self._pre_launch_script)
        except Exception:
            # 失败容错（照 after_done 模式）：非零退出/超时/异常 → WARNING，不阻断
            log.warning("job %s: pre_launch_script failed (non-blocking); "
                        "continuing to launch BetterGI", job.id, exc_info=True)
        # 脚本执行可能耗时（timeout 60s），期间 /abort 可能已置位 → 复查
        if self._jobs.abort_requested():
            log.info("job %s: abort requested during pre_launch_script; "
                     "skipping launch", job.id)
            self._jobs.finalize(JobState.ABORTED)
            return False
        return True

    def execute(self, job: Job, task) -> None:
        """实际执行流程（见模块文档）。"""
        # ★ 启动预检三分支：
        #   1) BetterGI 与游戏都在跑 → handoff 接管（用户手动启动的场景）；
        #   2) 仅 BetterGI 在跑（游戏没跑）→ 残留实例：先 terminate 该 BetterGI，
        #      短暂等待后正常 Popen 拉起（残留实例多半是上次任务卡死的遗留，
        #      直接接管会失去对启动流程的控制，故清掉重拉）；
        #   3) 仅游戏在跑 / 都没跑 → 仅游戏时 handoff 接管，都没跑时正常 Popen。
        bettergi_running = self._is_bettergi_running()
        game_running = self._is_game_running()
        proc: subprocess.Popen | None = None
        # ★ pre_launch_script：仅真正 Popen 的分支前执行（handoff 接管不执行）。
        #   脚本期间收到 /abort → 直接归档 aborted 返回，不再拉起。
        if not bettergi_running and not game_running:
            if not self._run_pre_launch_script(job):
                self._cleanup_current_proc()
                return
        if bettergi_running and game_running:
            log.info("job %s: already running (BetterGI=%s, game=%s); skip launching, "
                     "hand off to completion monitor", job.id, bettergi_running, game_running)
        elif bettergi_running and not game_running:
            killed = self._kill_residue_bettergi()
            log.info("job %s: residue BetterGI found (game not running), killed=%s; "
                     "relaunching", job.id, killed)
            proc = self._popen(build_command(self._exe, task.groups))
        else:
            # 仅游戏在跑 → handoff；都没跑 → 正常拉起
            if game_running:
                log.info("job %s: game already running without BetterGI; "
                         "hand off to completion monitor", job.id)
            else:
                cmd = build_command(self._exe, task.groups)
                log.info("launching BetterGI for job %s: %s", job.id, cmd)
                proc = self._popen(cmd)

        # ★ 记住本 job 拉起的 proc（供 /abort 主动终止）；job 结束时清理。
        #   handoff 模式 proc=None，/abort 走 kill_processes 兜底。
        with self._launcher._proc_lock:
            self._launcher._current_proc = proc

        # ★ 取本机已启动的 harvester(由 __call__ 提前创建),注入 monitor
        with _harvesters_lock:
            harvester = _harvesters.get(job.id)
        monitor = CompletionMonitor(
            is_game_running=self._is_game_running,
            harvester=harvester,                    # None = C 禁用
            log_done_keyword=self._log_keyword,     # 空 = C 禁用
            jobs=self._jobs,                        # 用于响应 abort
            poll_interval=2.0,
            # ★ E 实况对账：BetterGI 消失 + 游戏从未出现 → failed（不干等超时）
            is_bettergi_running=self._is_bettergi_running,
        )
        # ★ 完成判定 D：接入任务级 timeout_min（分钟）。
        #   deadline = min(task.timeout_min * 60, MAX_TASK_DURATION_SEC)；
        #   timeout_min <= 0 时回退 MAX_TASK_DURATION_SEC（防呆）。
        #   handoff 模式（未拉起进程直接接管）同样生效。
        try:
            task_timeout_min = int(getattr(task, "timeout_min", 0) or 0)
        except (TypeError, ValueError):
            task_timeout_min = 0
        timeout_sec = min(task_timeout_min * 60, MAX_TASK_DURATION_SEC) \
            if task_timeout_min > 0 else MAX_TASK_DURATION_SEC
        reason = monitor.wait(timeout_sec=timeout_sec)
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
                self._cleanup_current_proc()
                return
            step = min(0.5, deadline)
            self._sleep(step)
            deadline -= step
        log.info("job %s grace window (%.0fs) expired, proceeding to finalize with reason=%s",
                 job.id, self._grace, reason)

        # 再次取出 current:如果 abort 已清槽位,job 对象已被替换;跳过所有操作。
        if self._jobs.current is not job:
            log.info("job %s slot cleared (abort/restart); skipping finalize and after_done", job.id)
            self._cleanup_current_proc()
            return
        if job.state == JobState.ABORTED:
            log.info("job %s aborted; skipping after_done", job.id)
            self._cleanup_current_proc()
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
        else:   # "failed"/"error" 等内部异常
            final = JobState.FAILED
        self._jobs.finalize(final)
        log.info("job %s finalized: reason=%s → state=%s", job.id, reason, final.value)

        # ★ 仅正常完成(DONE)执行 after_done；异常/超时/失败/中止 都不动手
        if final != JobState.DONE:
            log.info("job %s: final state is %s (not DONE), skipping after_done",
                     job.id, final.value)
            self._cleanup_current_proc()
            return

        # 清理可能仍残留的 BetterGI 进程。
        _terminate_proc(proc)
        self._cleanup_current_proc()

        # 执行收尾动作。
        cmd_after = after_done_command(task.after_done)
        if cmd_after:
            log.info("running after_done: %s", cmd_after)
            try:
                subprocess.run(cmd_after, check=False)
            except Exception:
                log.exception("after_done command failed")

    def _cleanup_current_proc(self) -> None:
        """job 结束后清空 Launcher 记住的 proc 引用。"""
        with self._launcher._proc_lock:
            self._launcher._current_proc = None
