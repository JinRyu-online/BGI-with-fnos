"""POST /stop 端点与 kill_processes 单元测试。

psutil 相关一律用注入 fake，不依赖真实进程。
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from bgi_trigger.api.app import AppDeps, create_app
from bgi_trigger.core.execution import kill_processes
from bgi_trigger.core.state import JobState, JobStore
from bgi_trigger.service.auth import AuthState
from bgi_trigger.core.tasks import TaskRegistry

AUTH = {"Authorization": "Bearer secret"}

import json
import tempfile
from pathlib import Path


def _registry() -> TaskRegistry:
    d = Path(tempfile.mkdtemp())
    (d / "daily.json").write_text(json.dumps({
        "id": "daily", "display_name": "日常一条龙",
        "groups": ["日常一条龙"], "timeout_min": 90, "after_done": "none",
    }, ensure_ascii=False), encoding="utf-8")
    return TaskRegistry(d)


def _deps(jobs=None, kill=None, abort_kills_game=True) -> AppDeps:
    return AppDeps(
        hostname="DESKTOP-TEST",
        version="1.0.0",
        tasks=_registry(),
        auth=AuthState(api_key="secret", trusted_ips=[]),
        jobs=jobs or JobStore(),
        launch=lambda job, task: None,
        kill_processes=kill,
        bettergi_name="BetterGI.exe",
        game_processes=["YuanShen.exe"],
        abort_kills_game=abort_kills_game,
    )


# ── fake psutil 基建 ──

class FakePsutil:
    """最小 psutil 模块替身：process_iter + 异常类 + TimeoutExpired。"""

    class Error(Exception):
        pass

    class NoSuchProcess(Error):
        pass

    class TimeoutExpired(Error):
        pass

    class AccessDenied(Error):
        pass

    def __init__(self, procs):
        self._procs = procs   # list[FakeProcess]

    def process_iter(self, attrs=None):
        return list(self._procs)


class FakeProcess:
    """psutil.Process 替身：记录 terminate/kill 调用，可脚本化 wait 行为。"""

    def __init__(self, name="BetterGI.exe", pid=1, exits_on_wait=False,
                 wait_timeout=False):
        self.info = {"name": name}
        self.pid = pid
        self.terminate_called = False
        self.kill_called = False
        self._exits_on_wait = exits_on_wait       # wait() 立即视为已退出
        self._wait_timeout = wait_timeout         # wait() 抛 TimeoutExpired

    def is_running(self):
        return not (self._exits_on_wait and self.terminate_called)

    def terminate(self):
        self.terminate_called = True

    def kill(self):
        self.kill_called = True

    def wait(self, timeout=None):
        if self._wait_timeout:
            raise FakePsutil.TimeoutExpired()
        self._exits_on_wait = True


# ── kill_processes ──

def test_kill_processes_terminate_success():
    """terminate 成功路径：不调用 kill，返回进程名列表。"""
    p1 = FakeProcess("BetterGI.exe", pid=10)
    p2 = FakeProcess("YuanShen.exe", pid=11)
    ps = FakePsutil([p1, FakeProcess("explorer.exe", pid=9), p2])

    killed = kill_processes(["BetterGI.exe", "YuanShen.exe"], psutil_module=ps)

    assert killed == ["BetterGI.exe", "YuanShen.exe"]
    assert p1.terminate_called and p1.kill_called is False
    assert p2.terminate_called and p2.kill_called is False


def test_kill_processes_timeout_escalates_to_kill():
    """terminate 后 wait 超时 → 升级 kill 路径。"""
    p = FakeProcess("BetterGI.exe", pid=10, wait_timeout=True)
    ps = FakePsutil([p])

    killed = kill_processes(["BetterGI.exe"], psutil_module=ps)

    assert killed == ["BetterGI.exe"]
    assert p.terminate_called and p.kill_called


def test_kill_processes_skips_other_names():
    p = FakeProcess("chrome.exe", pid=5)
    ps = FakePsutil([p])

    killed = kill_processes(["BetterGI.exe"], psutil_module=ps)

    assert killed == []
    assert not p.terminate_called


def test_kill_processes_no_match_returns_empty():
    ps = FakePsutil([FakeProcess("explorer.exe", pid=1)])
    assert kill_processes(["BetterGI.exe"], psutil_module=ps) == []


# ── POST /stop 端点 ──

def test_stop_requires_auth():
    client = TestClient(create_app(_deps()))
    r = client.post("/stop")
    assert r.status_code == 401


def test_stop_with_active_job_finalizes_aborted_and_kills():
    """有活动 job：finalize ABORTED + 清理进程（mock kill_processes）。"""
    jobs = JobStore()
    job = jobs.start("daily", ["日常一条龙"])
    killed_names = []

    def fake_kill(names):
        killed_names.extend(names)
        return list(names)

    client = TestClient(create_app(_deps(jobs=jobs, kill=fake_kill)))
    r = client.post("/stop", headers=AUTH)

    assert r.status_code == 200
    body = r.json()
    assert body["stopped"] is True
    # 请求目标是 BetterGI + 游戏进程名
    assert set(body["killed"]) <= {"BetterGI.exe", "YuanShen.exe"}
    assert killed_names  # kill_processes 确实被调用了
    # job 已归档为 aborted 且槽位释放
    assert jobs.is_idle() is True
    assert jobs.get(job.id).state == JobState.ABORTED


def test_stop_without_active_job_still_kills():
    """无活动 job 也照常清理残留进程（"卡死后自救"入口）。"""
    killed_calls = []

    def fake_kill(names):
        killed_calls.append(list(names))
        return list(names)

    client = TestClient(create_app(_deps(jobs=JobStore(), kill=fake_kill)))
    r = client.post("/stop", headers=AUTH)

    assert r.status_code == 200
    body = r.json()
    assert body["stopped"] is True
    assert killed_calls == [["BetterGI.exe", "YuanShen.exe"]]


def test_stop_without_kill_callback_still_200():
    """kill_processes 未注入（None）时不 500，killed 为空列表。"""
    client = TestClient(create_app(_deps(kill=None)))
    r = client.post("/stop", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"stopped": True, "killed": []}


def test_stop_kills_exception_swallowed():
    """注入的 kill_processes 抛异常 → /stop 仍 200（不让自救入口挂掉）。"""
    def bad_kill(names):
        raise RuntimeError("boom")

    client = TestClient(create_app(_deps(kill=bad_kill)))
    r = client.post("/stop", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["killed"] == []


# ── /abort 语义 ──

def test_abort_after_finalize_returns_409_and_history_not_duplicated():
    """★ 回归修复:已终态任务(is_idle)的 /abort → 409,历史不重复 append。"""
    jobs = JobStore()
    jobs.start("daily", ["g"])
    jobs.mark_completing("game_exited")
    jobs.finalize(JobState.DONE)   # 终态但 current 未清空

    client = TestClient(create_app(_deps(jobs=jobs)))
    r = client.post("/abort", headers=AUTH)

    assert r.status_code == 409
    assert r.json()["detail"] == "no active job"
    assert len(jobs.history()) == 1   # ★ 不重复归档


def test_abort_no_job_returns_409():
    client = TestClient(create_app(_deps()))
    r = client.post("/abort", headers=AUTH)
    assert r.status_code == 409


def test_abort_active_job_sets_signal_and_kills_game():
    """活动任务 abort：置信号 + 终止游戏进程（abort_kills_game=True）。"""
    jobs = JobStore()
    job = jobs.start("daily", ["g"])
    killed_calls = []
    terminated = []

    def fake_kill(names):
        killed_calls.append(list(names))
        return list(names)

    deps = _deps(jobs=jobs, kill=fake_kill, abort_kills_game=True)
    deps.terminate_current_proc = lambda: terminated.append(True)
    client = TestClient(create_app(deps))

    r = client.post("/abort", headers=AUTH)

    assert r.status_code == 200
    assert r.json() == {"aborted": True}
    assert jobs.abort_requested() is True          # 信号置位
    assert jobs.get(job.id).state == JobState.ABORTED
    assert killed_calls == [["YuanShen.exe"]]      # 游戏进程被终止
    assert terminated == [True]                    # 本 job 拉起的 proc 被终止


def test_abort_with_abort_kills_game_false_spares_game():
    """abort_kills_game=False：只置信号，不杀游戏进程。"""
    jobs = JobStore()
    jobs.start("daily", ["g"])
    killed_calls = []

    def fake_kill(names):
        killed_calls.append(list(names))
        return list(names)

    client = TestClient(create_app(_deps(jobs=jobs, kill=fake_kill,
                                         abort_kills_game=False)))
    r = client.post("/abort", headers=AUTH)

    assert r.status_code == 200
    assert killed_calls == []
