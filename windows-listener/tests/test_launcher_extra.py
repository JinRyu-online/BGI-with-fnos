"""Launcher 行为补充测试：timeout_min 接入、启动预检三分支、
line_sink 桥接、log_save_dir 落盘路径、terminate 后 wait。

不依赖真实进程：Popen / 进程检测全部注入 fake。
"""
import subprocess
import threading
import time
from pathlib import Path

from bgi_trigger.core import launcher as launcher_mod
from bgi_trigger.core.launcher import Launcher
from bgi_trigger.core.state import JobState, JobStore


class FakeProc:
    """subprocess.Popen 替身：记录 terminate/wait 调用。"""

    def __init__(self):
        self.terminated = False
        self.killed = False
        self._wait_calls = []
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False

    def kill(self):
        self.killed = True
        self._alive = False

    def wait(self, timeout=None):
        self._wait_calls.append(timeout)
        self._alive = False
        return 0


class FakeChecker:
    """脚本化进程检测谓词。"""

    def __init__(self, bettergi: bool, game: bool):
        self.bettergi = bettergi
        self.game = game
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.bettergi if getattr(self, "_which", "b") == "b" else self.game


def _make_launcher(jobs, popen_records, monkeypatch,
                   log_save_dir=None, line_sink=None, grace=0,
                   pre_launch_script="", pre_launch_runner=None):
    """构造 Launcher 并把 make_game_checker 换成可控 fake。"""
    L = Launcher(
        bettergi_exe=r"C:\BGI\BetterGI.exe",
        game_processes=["YuanShen.exe"],
        log_path="",
        log_done_keyword="",
        grace_seconds=grace,
        jobs=jobs,
        subprocess_runner=lambda cmd: popen_records.append(cmd) or FakeProc(),
        sleep=lambda s: None,
        log_save_dir=log_save_dir,
        line_sink=line_sink,
        pre_launch_script=pre_launch_script,
        pre_launch_runner=pre_launch_runner,
    )
    checkers = {}

    def fake_checker_factory(names):
        name = names[0]
        if "BetterGI" in name:
            c = FakeChecker(False, False)
            c._which = "b"
        else:
            c = FakeChecker(False, False)
            c._which = "g"
        checkers[c._which] = c
        return c

    monkeypatch.setattr(launcher_mod, "make_game_checker", fake_checker_factory)
    return L, checkers


def _wait_thread_done(job, jobs, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if jobs.is_idle():
            return True
        time.sleep(0.02)
    return jobs.is_idle()


class _Task:
    def __init__(self, timeout_min=90, after_done="none", groups=None):
        self.id = "t"
        self.timeout_min = timeout_min
        self.after_done = after_done
        self.groups = groups or ["g1"]


# ── 任务 3：完成判定 D 接入 task.timeout_min ──

def test_timeout_min_converts_to_wait_deadline(monkeypatch):
    """task.timeout_min=1 → monitor.wait 收到 60 秒 deadline。"""
    captured = {}

    class FakeMonitor:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        def wait(self, timeout_sec=None):
            captured["timeout_sec"] = timeout_sec
            return "timeout"

    monkeypatch.setattr(launcher_mod, "CompletionMonitor", FakeMonitor)

    jobs = JobStore()
    job = jobs.start("t", ["g1"])
    popen_records = []
    L, _ = _make_launcher(jobs, popen_records, monkeypatch)

    ex = launcher_mod._BetterGIExecutor(L)
    ex._is_bettergi_running = lambda: False
    ex._is_game_running = lambda: False
    ex.execute(job, _Task(timeout_min=1))

    assert captured["timeout_sec"] == 60   # 1 分钟 → 60 秒


def test_timeout_min_capped_at_max_duration(monkeypatch):
    """timeout_min 超过 24h 上限时被截断为 MAX_TASK_DURATION_SEC。"""
    from bgi_trigger.core.execution import MAX_TASK_DURATION_SEC
    captured = {}

    class FakeMonitor:
        def __init__(self, **kwargs):
            pass

        def wait(self, timeout_sec=None):
            captured["v"] = timeout_sec
            return "timeout"

    monkeypatch.setattr(launcher_mod, "CompletionMonitor", FakeMonitor)

    jobs = JobStore()
    job = jobs.start("t", ["g1"])
    L, _ = _make_launcher(jobs, [], monkeypatch)
    ex = launcher_mod._BetterGIExecutor(L)
    ex._is_bettergi_running = lambda: False
    ex._is_game_running = lambda: False
    ex.execute(job, _Task(timeout_min=10 ** 6))

    assert captured["v"] == MAX_TASK_DURATION_SEC


def test_timeout_min_zero_falls_back_to_max(monkeypatch):
    """timeout_min<=0（0 = 不限的官方语义）回退 MAX_TASK_DURATION_SEC 24h 安全网。"""
    from bgi_trigger.core.execution import MAX_TASK_DURATION_SEC
    captured = {}

    class FakeMonitor:
        def __init__(self, **kwargs):
            pass

        def wait(self, timeout_sec=None):
            captured["v"] = timeout_sec
            return "timeout"

    monkeypatch.setattr(launcher_mod, "CompletionMonitor", FakeMonitor)

    jobs = JobStore()
    job = jobs.start("t", ["g1"])
    L, _ = _make_launcher(jobs, [], monkeypatch)
    ex = launcher_mod._BetterGIExecutor(L)
    ex._is_bettergi_running = lambda: False
    ex._is_game_running = lambda: False
    ex.execute(job, _Task(timeout_min=0))

    assert captured["v"] == MAX_TASK_DURATION_SEC


def test_timeout_min_small_value_really_times_out(monkeypatch):
    """端到端：task.timeout_min=1 → wait(60) 自然走到 D 超时返回 "timeout"。
    用注入 monitor 的真实实现 + 恒真谓词 + 极短 deadline 的等价小值直接验证。"""
    from bgi_trigger.core.execution import CompletionMonitor
    jobs = JobStore()
    job = jobs.start("t", ["g1"])
    mon = CompletionMonitor(is_game_running=lambda: True, harvester=None,
                            log_done_keyword="", jobs=jobs,
                            poll_interval=0, startup_grace=0)
    # 等价于 timeout_min=1 产生的 60s deadline（用小值验证同一路径）
    assert mon.wait(timeout_sec=0.05) == "timeout"


# ── 任务 4：启动预检三分支 ──

def test_preflight_both_running_handoff_no_popen(monkeypatch):
    """BetterGI 与游戏都在跑 → handoff，不 Popen。"""
    jobs = JobStore()
    job = jobs.start("t", ["g1"])
    popen_records = []
    L, _ = _make_launcher(jobs, popen_records, monkeypatch)

    class FakeMonitor:
        def __init__(self, **kwargs):
            pass

        def wait(self, timeout_sec=None):
            return "game_exited"

    monkeypatch.setattr(launcher_mod, "CompletionMonitor", FakeMonitor)

    ex = launcher_mod._BetterGIExecutor(L)
    ex._is_bettergi_running = lambda: True
    ex._is_game_running = lambda: True
    ex.execute(job, _Task())

    assert popen_records == []            # 未拉起
    assert jobs.get(job.id).state == JobState.ABNORMAL_EXIT


def test_preflight_only_bettergi_residue_killed_then_popen(monkeypatch):
    """仅 BetterGI 残留（游戏没跑）→ 先 kill 残留再正常 Popen 拉起。"""
    jobs = JobStore()
    job = jobs.start("t", ["g1"])
    popen_records = []
    L, _ = _make_launcher(jobs, popen_records, monkeypatch)

    killed_names = []
    monkeypatch.setattr(launcher_mod, "kill_processes",
                        lambda names: killed_names.extend(names) or list(names))

    class FakeMonitor:
        def __init__(self, **kwargs):
            pass

        def wait(self, timeout_sec=None):
            return "game_exited"

    monkeypatch.setattr(launcher_mod, "CompletionMonitor", FakeMonitor)

    ex = launcher_mod._BetterGIExecutor(L)
    ex._is_bettergi_running = lambda: True
    ex._is_game_running = lambda: False
    ex.execute(job, _Task())

    assert killed_names == ["BetterGI.exe"]   # 残留被清
    assert len(popen_records) == 1            # 之后重新拉起
    assert popen_records[0][:3] == [r"C:\BGI\BetterGI.exe", "--startGroups", "g1"]


def test_preflight_only_game_handoff_no_popen(monkeypatch):
    """仅游戏在跑 → handoff 接管，不 Popen。"""
    jobs = JobStore()
    job = jobs.start("t", ["g1"])
    popen_records = []
    L, _ = _make_launcher(jobs, popen_records, monkeypatch)

    class FakeMonitor:
        def __init__(self, **kwargs):
            pass

        def wait(self, timeout_sec=None):
            return "game_exited"

    monkeypatch.setattr(launcher_mod, "CompletionMonitor", FakeMonitor)

    ex = launcher_mod._BetterGIExecutor(L)
    ex._is_bettergi_running = lambda: False
    ex._is_game_running = lambda: True
    ex.execute(job, _Task())

    assert popen_records == []


def test_preflight_none_running_popen_called(monkeypatch):
    """都没跑 → 正常 Popen 拉起。"""
    jobs = JobStore()
    job = jobs.start("t", ["g1"])
    popen_records = []
    L, _ = _make_launcher(jobs, popen_records, monkeypatch)

    class FakeMonitor:
        def __init__(self, **kwargs):
            pass

        def wait(self, timeout_sec=None):
            return "game_exited"

    monkeypatch.setattr(launcher_mod, "CompletionMonitor", FakeMonitor)

    ex = launcher_mod._BetterGIExecutor(L)
    ex._is_bettergi_running = lambda: False
    ex._is_game_running = lambda: False
    ex.execute(job, _Task())

    assert len(popen_records) == 1


# ── pre_launch_script：拉起前脚本（空不执行/执行/失败不阻断/abort 复查跳过）──

def test_pre_launch_empty_script_not_executed(monkeypatch):
    """pre_launch_script 为空（默认）→ runner 不被调用，正常拉起。"""
    jobs = JobStore()
    job = jobs.start("t", ["g1"])
    popen_records = []
    runner_calls = []
    L, _ = _make_launcher(jobs, popen_records, monkeypatch,
                          pre_launch_script="",
                          pre_launch_runner=lambda s: runner_calls.append(s))

    class FakeMonitor:
        def __init__(self, **kwargs):
            pass

        def wait(self, timeout_sec=None):
            return "game_exited"

    monkeypatch.setattr(launcher_mod, "CompletionMonitor", FakeMonitor)

    ex = launcher_mod._BetterGIExecutor(L)
    ex._is_bettergi_running = lambda: False
    ex._is_game_running = lambda: False
    ex.execute(job, _Task())

    assert runner_calls == []          # 空脚本不执行
    assert len(popen_records) == 1     # 正常拉起


def test_pre_launch_script_called_before_popen(monkeypatch):
    """配置了脚本 → runner 收到脚本内容，且先于 Popen 执行。"""
    jobs = JobStore()
    job = jobs.start("t", ["g1"])
    order = []
    popen_records = []

    def recording_popen(cmd):
        order.append("popen")
        popen_records.append(cmd)
        return FakeProc()

    L = Launcher(
        bettergi_exe=r"C:\BGI\BetterGI.exe",
        game_processes=["YuanShen.exe"],
        log_path="", log_done_keyword="", grace_seconds=0,
        jobs=jobs,
        subprocess_runner=recording_popen,
        sleep=lambda s: None,
        pre_launch_script="taskkill /IM Weixin.exe /F",
        pre_launch_runner=lambda s: order.append("script:" + s),
    )
    checkers = {}

    def fake_checker_factory(names):
        c = FakeChecker(False, False)
        c._which = "b" if "BetterGI" in names[0] else "g"
        checkers[c._which] = c
        return c

    monkeypatch.setattr(launcher_mod, "make_game_checker", fake_checker_factory)

    class FakeMonitor:
        def __init__(self, **kwargs):
            pass

        def wait(self, timeout_sec=None):
            return "game_exited"

    monkeypatch.setattr(launcher_mod, "CompletionMonitor", FakeMonitor)

    ex = launcher_mod._BetterGIExecutor(L)
    ex._is_bettergi_running = lambda: False
    ex._is_game_running = lambda: False
    ex.execute(job, _Task())

    assert order == ["script:taskkill /IM Weixin.exe /F", "popen"]   # 先脚本后拉起
    assert len(popen_records) == 1


def test_pre_launch_runner_failure_does_not_block(monkeypatch):
    """runner 抛异常（非零退出/超时的模拟）→ 仅 WARNING，不阻断拉起。"""
    jobs = JobStore()
    job = jobs.start("t", ["g1"])
    popen_records = []

    def bad_runner(script):
        raise subprocess.TimeoutExpired(cmd=script, timeout=60)

    L, _ = _make_launcher(jobs, popen_records, monkeypatch,
                          pre_launch_script="bad command",
                          pre_launch_runner=bad_runner)

    class FakeMonitor:
        def __init__(self, **kwargs):
            pass

        def wait(self, timeout_sec=None):
            return "game_exited"

    monkeypatch.setattr(launcher_mod, "CompletionMonitor", FakeMonitor)

    ex = launcher_mod._BetterGIExecutor(L)
    ex._is_bettergi_running = lambda: False
    ex._is_game_running = lambda: False
    ex.execute(job, _Task())   # 不应抛异常

    assert len(popen_records) == 1                     # 仍正常拉起
    assert jobs.get(job.id).state == JobState.ABNORMAL_EXIT


def test_pre_launch_abort_requested_skips_launch(monkeypatch):
    """脚本执行期间 /abort 置位 → 归档 aborted，不拉起 BetterGI。"""
    jobs = JobStore()
    job = jobs.start("t", ["g1"])
    popen_records = []

    def aborting_runner(script):
        jobs.set_abort_signal()   # 模拟脚本执行期间收到 /abort

    L, _ = _make_launcher(jobs, popen_records, monkeypatch,
                          pre_launch_script="long task",
                          pre_launch_runner=aborting_runner)

    class FakeMonitor:
        def __init__(self, **kwargs):
            pass

        def wait(self, timeout_sec=None):
            return "timeout"

    monkeypatch.setattr(launcher_mod, "CompletionMonitor", FakeMonitor)

    ex = launcher_mod._BetterGIExecutor(L)
    ex._is_bettergi_running = lambda: False
    ex._is_game_running = lambda: False
    ex.execute(job, _Task())

    assert popen_records == []                         # 未拉起
    assert jobs.get(job.id).state == JobState.ABORTED  # 直接归档 aborted


def test_pre_launch_not_executed_on_handoff(monkeypatch):
    """handoff 接管分支（都在跑）→ 脚本不执行。"""
    jobs = JobStore()
    job = jobs.start("t", ["g1"])
    popen_records = []
    runner_calls = []
    L, _ = _make_launcher(jobs, popen_records, monkeypatch,
                          pre_launch_script="echo hi",
                          pre_launch_runner=lambda s: runner_calls.append(s))

    class FakeMonitor:
        def __init__(self, **kwargs):
            pass

        def wait(self, timeout_sec=None):
            return "game_exited"

    monkeypatch.setattr(launcher_mod, "CompletionMonitor", FakeMonitor)

    ex = launcher_mod._BetterGIExecutor(L)
    ex._is_bettergi_running = lambda: True
    ex._is_game_running = lambda: True
    ex.execute(job, _Task())

    assert runner_calls == []
    assert popen_records == []


# ── 任务 9：解除对 listener.BASE_DIR 的反向依赖 ──

def test_job_log_save_path_from_injected_dir():
    jobs = JobStore()
    L = Launcher(bettergi_exe="x", game_processes=[], log_path="",
                 log_done_keyword="", grace_seconds=1, jobs=jobs,
                 log_save_dir="D:/logs")
    assert L._job_log_save_path("abc") == Path("D:/logs") / "abc.log"


def test_job_log_save_path_none_when_not_configured():
    jobs = JobStore()
    L = Launcher(bettergi_exe="x", game_processes=[], log_path="",
                 log_done_keyword="", grace_seconds=1, jobs=jobs)
    assert L._job_log_save_path("abc") is None


def test_launcher_no_longer_imports_listener():
    """launcher 模块源码不得再 import listener.BASE_DIR（反向依赖解除）。"""
    src = Path(launcher_mod.__file__).read_text(encoding="utf-8")
    assert "from listener import" not in src
    assert "import listener" not in src


# ── 任务 8：terminate 后必须 wait ──

def test_terminate_proc_calls_wait_after_terminate():
    proc = FakeProc()
    launcher_mod._terminate_proc(proc)
    assert proc.terminated is True
    assert proc._wait_calls == [5]   # terminate → wait(timeout=5)


def test_terminate_proc_escalates_to_kill_after_timeout():
    class SlowProc(FakeProc):
        def wait(self, timeout=None):
            import subprocess
            if self.terminated and not self.killed:
                raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)
            self._wait_calls.append(timeout)
            return 0

    proc = SlowProc()
    launcher_mod._terminate_proc(proc)
    assert proc.terminated and proc.killed
    assert proc._wait_calls[-1] == 3   # kill → wait(timeout=3)


def test_terminate_proc_none_is_noop():
    assert launcher_mod._terminate_proc(None) is None


# ── 任务 7：line_sink 桥接 ──

def test_launcher_passes_line_sink_to_harvester(monkeypatch):
    """Launcher 构造的 line_sink 应透传给 LogHarvester。"""
    captured = {}

    class FakeHarvester:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def set_keyword(self, kw):
            captured["keyword"] = kw

        def start(self):
            pass

    monkeypatch.setattr(launcher_mod, "LogHarvester", FakeHarvester)
    monkeypatch.setattr(launcher_mod.threading, "Thread",
                        lambda *a, **kw: SimpleThread())

    sink = lambda ln: None
    jobs = JobStore()
    L = Launcher(bettergi_exe="x", game_processes=[], log_path="C:/x.log",
                 log_done_keyword="", grace_seconds=1, jobs=jobs,
                 line_sink=sink)
    job = jobs.start("t", ["g"])
    job.log_path = "C:/x.log"
    L(job, _Task())

    assert captured["line_sink"] is sink


class SimpleThread:
    """daemon Thread 替身：不真正起线程。"""

    def __init__(self, *a, **kw):
        self._target = kw.get("target")
        self._args = kw.get("args", ())
        self.daemon = False

    def start(self):
        pass   # 不执行 target：测试只关心 harvester 构造参数


def test_harvester_line_sink_called_per_line(tmp_path):
    """收割线程每收割一行就调用 line_sink 一次（真实 LogHarvester + fake 回调）。"""
    log_file = tmp_path / "bgi.log"
    log_file.write_text("", encoding="utf-8")
    got = []
    evt = threading.Event()

    def sink(ln):
        got.append(ln)
        if len(got) >= 3:
            evt.set()

    h = launcher_mod.LogHarvester(job_id="sinkjob", log_path=str(log_file),
                                  poll_interval=0.05, line_sink=sink)
    h.start()
    try:
        assert h.wait_for_ready(timeout=5)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("line one\nline two\nline three\n")
        assert evt.wait(timeout=5), f"line_sink 未收到 3 行, got={got}"
        assert got == ["line one", "line two", "line three"]
    finally:
        h.stop()


def test_harvester_line_sink_exception_does_not_break_harvest(tmp_path):
    """line_sink 抛异常不得拖垮收割线程（后续行仍进缓冲）。"""
    log_file = tmp_path / "bgi.log"
    log_file.write_text("", encoding="utf-8")

    def bad_sink(ln):
        raise RuntimeError("sink down")

    h = launcher_mod.LogHarvester(job_id="sinkjob2", log_path=str(log_file),
                                  poll_interval=0.05, line_sink=bad_sink)
    h.start()
    try:
        assert h.wait_for_ready(timeout=5)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("alpha\n")
        time.sleep(0.3)
        # 回调持续异常 → 桥接停止，但收割主流程继续
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("beta\n")
        lines = h.wait_for_lines(min_count=2, timeout=5)
        assert "alpha" in lines and "beta" in lines
    finally:
        h.stop()
