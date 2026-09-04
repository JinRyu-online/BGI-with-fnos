"""BetterGI 启动与完成判定模块。

完成判定（优先级递减，先到先得）：
  A - /abort 信号（每轮最先检查）
  B - 游戏进程退出（is_game_running() 返回 False）
  E - 实况对账：BetterGI 进程消失 且 游戏从未出现过（seen_game 标志）→ "failed"
      （仅在注入 is_bettergi_running 时启用；E 与 B 条件重叠，折叠在 B 分支内，
       用于区分"BetterGI 崩了、游戏没起来"与"游戏正常退出"）
  C - BetterGI 最新日志命中 log_done_keyword（通过注入的 LogHarvester 缓冲查询）
  D - 超时兜底：调用方传入 min(task.timeout_min * 60, MAX_TASK_DURATION_SEC)，
      默认 24 小时硬编码（"有人忘了关"的最后安全网）

注入资源：
  - is_game_running: B 谓词
  - is_bettergi_running: 实况对账谓词（None 表示 E 禁用，向后兼容）
  - harvester: LogHarvester 实例（None 表示 C 禁用）
  - log_done_keyword: str（空表示 C 禁用）
  - jobs: JobStore 实例（用于响应 /abort）

此外提供 kill_processes：按进程名枚举并终止进程（terminate → 等待 → kill），
供 /stop、/abort、残留 BetterGI 清理复用。psutil 一律延迟导入（软依赖）。
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

# 启动预热期：monitor 启动后，此段时间内不触发 B（游戏进程退出）判定。
# 目的：给 BetterGI 启动游戏留出拉起启动器→登录→进场景的时间，
# 避免"游戏还没起来就被误判为 game_exited"。
# 30s：覆盖从休眠唤醒后冷启动 + BitLocker 加密盘慢速读取的边界场景。
STARTUP_GRACE_SECONDS = 30


def build_command(bettergi_exe: str, groups: list[str]) -> list[str]:
    """构造 BetterGI 启动命令行参数列表。

    形如：["D:\\BGI\\BetterGI.exe", "--startGroups", "日常一条龙", "关闭游戏"]
    """
    exe = bettergi_exe.strip()
    return [exe, "--startGroups", *groups]


class CompletionMonitor:
    """完成判定监视器：轮询 B/C 谓词，返回首个命中原因。

    每轮检查顺序：先 abort，再 B（游戏进程退出；E 实况对账折叠在 B 分支内），
    再 C（日志关键字），再 D（超时）。
    因此当同一轮多个条件同时命中时，abort > B/E > C > D（确定性选择）。

    E 实况对账（seen_game 机制，仅当注入 is_bettergi_running 时启用）：
      monitor 维护 seen_game 标志，每轮 poll 若 is_game_running() 为 True 就置位。
      当已过 startup_grace 且 is_bettergi_running() 为 False 且 is_game_running()
      为 False 时：
        - 若游戏从未出现过（seen_game=False）：说明"BetterGI 曾被拉起但已崩、
          游戏从未起来"，返回 "failed"（执行器已死，不能干等超时兜底）；
        - 若游戏出现过（seen_game=True）：维持原 B 行为，返回 "game_exited"
          （游戏正常退出路径保留，不误报）。
      is_bettergi_running=None 表示禁用（向后兼容旧行为）。
    """

    def __init__(
        self,
        *,
        is_game_running: Callable[[], bool],
        harvester: LogHarvester | None,
        log_done_keyword: str,
        jobs: JobStore,
        poll_interval: float = 2.0,
        startup_grace: float = STARTUP_GRACE_SECONDS,
        is_bettergi_running: Callable[[], bool] | None = None,
    ) -> None:
        self._is_game_running = is_game_running
        self._is_bettergi_running = is_bettergi_running
        self._harvester = harvester
        self._log_done_keyword = log_done_keyword
        self._jobs = jobs
        self._poll_interval = poll_interval
        self._startup_grace = startup_grace
        # ★ E 实况对账：seen_game 标志——游戏进程是否曾出现过。
        #   每轮 poll 若 is_game_running() 为 True 就置位（只增不减）。
        self._seen_game = False
        # C 启用条件：harvester 与 keyword 都非空
        c_enabled = harvester is not None and bool(log_done_keyword)
        # ★ 诊断日志：记录判定器启动时的能力状态
        log.info("CompletionMonitor start: poll_interval=%.1fs, "
                 "startup_grace=%ds, "
                 "checker_B(game_process)=yes, checker_C(log_keyword)=%s, "
                 "checker_E(bettergi_reconcile)=%s, "
                 "checker_D(duration safety net)=yes, checker_abort=yes",
                 poll_interval, self._startup_grace,
                 "yes" if c_enabled else "disabled",
                 "yes" if is_bettergi_running is not None else "disabled")

    def wait(self, timeout_sec: float = MAX_TASK_DURATION_SEC) -> str:
        """阻塞等待完成，返回原因："log_keyword" / "game_exited" / "aborted" / "timeout"。

        timeout_sec 默认 24h 硬编码兜底；调用方可传更小值用于单测模拟。
        生产路径由 launcher 传入 min(task.timeout_min * 60, MAX_TASK_DURATION_SEC)。

        启动后首 self._startup_grace 秒内，B（游戏进程退出）判定被抑制，
        避免"游戏还没起来就被误判异常退出"。

        E 实况对账见类 docstring：BetterGI 消失 + 游戏从未出现 → "failed"。
        """
        deadline = time.monotonic() + timeout_sec
        poll_count = 0
        # ★ 启动预热期起点。
        # 注意：不能在循环外"提前判定 in_startup_grace"，否则当 startup_grace=0 时
        # 可能出现时序竞争：首次 poll 时 time.monotonic() == deadline，
        # 两者比较结果不稳定导致 in_startup_grace 无法归 False。
        # 故每次 poll 都重新计算 in_startup_grace。
        startup_grace_deadline = time.monotonic() + self._startup_grace
        while True:
            poll_count += 1
            remaining = deadline - time.monotonic()
            # 当前是否处于启动预热期（每次 poll 都重新判定）
            in_startup_grace = time.monotonic() < startup_grace_deadline

            # 0. 响应外部 abort
            if self._jobs.abort_requested():
                log.info("completion interrupted [A:aborted] at poll #%d "
                         "(%.1fs remaining): abort signal detected, "
                         "stopping monitor", poll_count, remaining)
                return "aborted"

            # B：游戏进程退出（★ 启动预热期内抑制）
            game_running = self._is_game_running()
            # ★ E 实况对账：游戏出现过就置位 seen_game（只增不减）
            if game_running:
                self._seen_game = True
            if not game_running:
                if in_startup_grace:
                    # 预热期内：仅打 DEBUG，不触发 game_exited
                    log.debug("poll #%d: game_process not found but still in "
                              "startup grace period (%.1fs left), ignoring",
                              poll_count,
                              startup_grace_deadline - time.monotonic())
                else:
                    # ★ E 实况对账（折叠在 B 分支内，条件与 B 重叠）：
                    #   BetterGI 进程消失 且 游戏从未出现过 → 执行器已死（拉起后崩了、
                    #   游戏没起来），返回 "failed"，不能干等超时兜底。
                    #   游戏出现过则维持原 B 行为（game_exited），保留正常完成路径。
                    if (self._is_bettergi_running is not None
                            and not self._seen_game
                            and not self._is_bettergi_running()):
                        log.info("completion detected [E:executor_died] at poll #%d "
                                 "(%.1fs remaining): BetterGI process gone and game "
                                 "never seen running, stopping monitor", poll_count, remaining)
                        return "failed"
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

            # D：超时兜底（生产路径 = min(task.timeout_min*60, MAX_TASK_DURATION_SEC)，
            #    默认 24h 硬编码）
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


def kill_processes(names: list[str], psutil_module=None,
                   wait_timeout: float = 5.0) -> list[str]:
    """终止所有名字匹配 names（进程 basename 集合）的进程。

    策略：先 terminate()（温和），最多等 wait_timeout 秒，仍未退出则 kill()
    （强制），再短暂 wait 确认。已退出的进程自动跳过。

    参数：
      names: 目标进程名集合（大小写不敏感，如 ["BetterGI.exe"] 或 game_processes）。
      psutil_module: 可注入的 psutil 模块（测试用 fake），None 时延迟导入真实 psutil。
      wait_timeout: terminate 后等待退出的秒数，超时升级为 kill。

    返回被终止（terminate 或 kill 过）的进程名列表（按实际进程名，可能含重复项，
    每个被终止的进程一条）。枚举/访问失败（NoSuchProcess、AccessDenied 等）单个
    进程失败不影响其余进程。
    """
    ps = psutil_module
    if ps is None:
        try:
            import psutil as ps  # 延迟导入：软依赖
        except ImportError:
            log.warning("kill_processes: psutil not available; cannot kill %s", names)
            return []
    targets = {n.lower() for n in names if n}
    killed: list[str] = []
    try:
        procs = list(ps.process_iter(["name"]))
    except ps.Error:
        return killed
    for p in procs:
        name = (p.info.get("name") or "")
        if name.lower() not in targets:
            continue
        try:
            if not p.is_running():
                continue
            p.terminate()
            try:
                p.wait(timeout=wait_timeout)
            except ps.TimeoutExpired:
                log.warning("kill_processes: %s (pid=%s) did not exit in %.0fs; killing",
                            name, p.pid, wait_timeout)
                try:
                    p.kill()
                    p.wait(timeout=3.0)
                except (ps.NoSuchProcess, ps.TimeoutExpired):
                    pass  # kill 后仍拿不到退出，尽力而为
            killed.append(name)
            log.info("kill_processes: terminated %s (pid=%s)", name, p.pid)
        except ps.NoSuchProcess:
            pass  # 进程已自行退出
        except ps.Error:
            log.warning("kill_processes: failed to kill %s (pid=%s)", name,
                        getattr(p, "pid", "?"), exc_info=True)
    return killed
