"""后台对账循环与终态修复回归测试（本次事故核心双保险）。

事故回顾：/api/status 曾把终态集合写成 ("done", "timeout", "failed", "aborted")——
"timeout" 拼错（实际是 timed_out）且漏 abnormal_exit，导致超时/异常退出任务在
jobs.json 永远卡 running；叠加"历史只在浏览器轮询时更新"，页面一关就再也没人修。

回归双保险：
  1) /api/status 对 timed_out / abnormal_exit 必须落终态 + finished_at；
  2) 后台对账循环即使浏览器不开也能把卡住的记录推进到终态/unknown。
"""
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from history import HistoryStore
from listener_client import ListenerError, ListenerNotFound
from main import create_app


class ReconcileFakeClient:
    """可编程假客户端：status 按 job_id 返回预设状态/异常，记录调用。"""

    def __init__(self, behavior=None):
        # behavior: {job_id: state} 或 {job_id: Exception}
        self.behavior = behavior or {}
        self.status_calls = []
        self.stopped = False

    def health(self):
        return {"service": "bgi-trigger", "hostname": "H", "version": "1"}

    def tasks(self):
        return []

    def trigger(self, task_id):
        return {"job_id": "x"}

    def status(self, job_id):
        self.status_calls.append(job_id)
        b = self.behavior.get(job_id, "running")
        if isinstance(b, Exception):
            raise b
        return {"id": job_id, "state": b, "task_id": "daily"}

    def abort(self):
        return {"aborted": True}

    def stop(self):
        self.stopped = True
        return {"stopped": True, "killed": ["bgi.exe"]}


def _wait_until(pred, timeout=5.0, interval=0.02):
    """轮询等待条件成立，避免精确 sleep 的竞态。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


def _seed_history(tmp_path, records):
    p = tmp_path / "jobs.json"
    HistoryStore(p).record.__self__  # noqa: B018 - 确认对象可用（防御性）
    h = HistoryStore(p)
    for rec in records:
        h.record(rec)
    return str(p)


def _paired_config(tmp_path, ip="10.0.0.5", port=8765):
    import json
    cfg = {"default_target": {"ip": ip, "port": port, "hostname": "PC"},
           "api_key": "k", "target_mac": ""}
    (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")


# ---------------- 回归双保险：/api/status 终态落盘 ----------------

@pytest.mark.parametrize("state", ["timed_out", "abnormal_exit"])
def test_status_terminal_states_record_finished_at(tmp_path, state):
    """回归：timed_out / abnormal_exit 必须被识别为终态并落 finished_at。"""
    fake = ReconcileFakeClient({"jobX": state})
    _paired_config(tmp_path)
    app = create_app(
        config_path=str(tmp_path / "config.json"),
        history_path=str(tmp_path / "jobs.json"),
        reconcile_interval=0,  # 关闭对账，聚焦 /api/status 行为
        client_factory=lambda url, key: fake,
    )
    with TestClient(app) as c:
        r = c.get("/api/status", params={"job_id": "jobX"})
        assert r.status_code == 200
        assert r.json()["state"] == state

        jobs = c.get("/api/jobs").json()
        assert jobs[0]["job_id"] == "jobX"
        assert jobs[0]["state"] == state, f"{state} 必须落终态（历史卡 running 事故回归）"
        assert jobs[0]["finished_at"] is not None, "终态必须带 finished_at"


def test_status_active_states_have_no_finished_at(tmp_path):
    """running/completing 仍是非终态：不写 finished_at。"""
    fake = ReconcileFakeClient({"jobA": "completing"})
    _paired_config(tmp_path)
    app = create_app(
        config_path=str(tmp_path / "config.json"),
        history_path=str(tmp_path / "jobs.json"),
        reconcile_interval=0,
        client_factory=lambda url, key: fake,
    )
    with TestClient(app) as c:
        c.get("/api/status", params={"job_id": "jobA"})
        jobs = c.get("/api/jobs").json()
        assert jobs[0]["state"] == "completing"
        assert jobs[0].get("finished_at") in (None, "")


# ---------------- 后台对账循环 ----------------

def test_reconcile_marks_timed_out_without_browser(tmp_path):
    """对账循环：浏览器不开也能把 running 记录推进到 timed_out。"""
    fake = ReconcileFakeClient({"jobT": "timed_out"})
    _paired_config(tmp_path)
    _seed_history(tmp_path, [{"job_id": "jobT", "task_id": "daily",
                              "state": "running", "created_at": 123.0}])
    app = create_app(
        config_path=str(tmp_path / "config.json"),
        history_path=str(tmp_path / "jobs.json"),
        reconcile_interval=0.05,
        client_factory=lambda url, key: fake,
    )
    with TestClient(app) as c:  # with 语句触发 lifespan → 启动对账线程
        ok = _wait_until(lambda: any(
            j["job_id"] == "jobT" and j["state"] == "timed_out"
            for j in HistoryStore(tmp_path / "jobs.json").all()))
        assert ok, "对账循环应在超时前把 jobT 推进到 timed_out"
        rec = [j for j in HistoryStore(tmp_path / "jobs.json").all() if j["job_id"] == "jobT"][0]
        assert rec["finished_at"] is not None
        assert rec["created_at"] == 123.0  # prev_fields_fallback 保留最早触发时间
    assert "jobT" in fake.status_calls


def test_reconcile_marks_abnormal_exit(tmp_path):
    fake = ReconcileFakeClient({"jobE": "abnormal_exit"})
    _paired_config(tmp_path)
    _seed_history(tmp_path, [{"job_id": "jobE", "state": "running"}])
    app = create_app(
        config_path=str(tmp_path / "config.json"),
        history_path=str(tmp_path / "jobs.json"),
        reconcile_interval=0.05,
        client_factory=lambda url, key: fake,
    )
    with TestClient(app):
        assert _wait_until(lambda: any(
            j["job_id"] == "jobE" and j["state"] == "abnormal_exit"
            for j in HistoryStore(tmp_path / "jobs.json").all()))


def test_reconcile_404_marks_unknown(tmp_path):
    """监听器 404（重启丢历史）→ 标记 unknown 并停止跟踪。"""
    fake = ReconcileFakeClient({"job404": ListenerNotFound("not found")})
    _paired_config(tmp_path)
    _seed_history(tmp_path, [{"job_id": "job404", "state": "running"}])
    app = create_app(
        config_path=str(tmp_path / "config.json"),
        history_path=str(tmp_path / "jobs.json"),
        reconcile_interval=0.05,
        client_factory=lambda url, key: fake,
    )
    with TestClient(app):
        assert _wait_until(lambda: any(
            j["job_id"] == "job404" and j["state"] == "unknown"
            for j in HistoryStore(tmp_path / "jobs.json").all()))

    # 停止跟踪：unknown 后不再继续查询该 job
    time.sleep(0.15)
    calls_before = fake.status_calls.count("job404")
    assert calls_before >= 1
    time.sleep(0.15)
    assert fake.status_calls.count("job404") == calls_before


def test_reconcile_network_error_keeps_tracking(tmp_path):
    """网络异常：记录保持 running（下轮重试），不炸线程。"""
    fake = ReconcileFakeClient({"jobN": ListenerError("network down")})
    _paired_config(tmp_path)
    _seed_history(tmp_path, [{"job_id": "jobN", "state": "running"}])
    app = create_app(
        config_path=str(tmp_path / "config.json"),
        history_path=str(tmp_path / "jobs.json"),
        reconcile_interval=0.05,
        client_factory=lambda url, key: fake,
    )
    with TestClient(app) as c:
        assert _wait_until(lambda: fake.status_calls.count("jobN") >= 2)
        jobs = c.get("/api/jobs").json()
        assert [j for j in jobs if j["job_id"] == "jobN"][0]["state"] == "running"


def test_reconcile_unpaired_does_not_crash(tmp_path):
    """未配对：对账循环静默跳过，不炸线程、不改历史。"""
    # 注意：不写 config.json → 未配对
    _seed_history(tmp_path, [{"job_id": "jobU", "state": "running"}])

    class BoomClient:
        def __init__(self, *a, **k):
            raise AssertionError("未配对时不应构造客户端")

    app = create_app(
        config_path=str(tmp_path / "config.json"),
        history_path=str(tmp_path / "jobs.json"),
        reconcile_interval=0.05,
        client_factory=BoomClient,
    )
    with TestClient(app) as c:
        # 循环跑了至少两轮且线程还活着（未崩）
        assert _wait_until(lambda: c.get("/api/jobs").json()[0]["state"] == "running")
        time.sleep(0.12)
        jobs = HistoryStore(tmp_path / "jobs.json").all()
        assert [j for j in jobs if j["job_id"] == "jobU"][0]["state"] == "running"


def test_reconcile_disabled_by_default_interval_zero(tmp_path):
    """reconcile_interval=0 → 完全不启动对账线程。"""
    fake = ReconcileFakeClient({"jobS": "timed_out"})
    _paired_config(tmp_path)
    _seed_history(tmp_path, [{"job_id": "jobS", "state": "running"}])
    app = create_app(
        config_path=str(tmp_path / "config.json"),
        history_path=str(tmp_path / "jobs.json"),
        reconcile_interval=0,
        client_factory=lambda url, key: fake,
    )
    with TestClient(app):
        time.sleep(0.2)
        assert fake.status_calls == []
    jobs = HistoryStore(tmp_path / "jobs.json").all()
    assert [j for j in jobs if j["job_id"] == "jobS"][0]["state"] == "running"


def test_reconcile_skips_non_active_states(tmp_path):
    """done/unknown 等非活动态记录不参与对账查询。"""
    fake = ReconcileFakeClient({"jobDone": "timed_out"})
    _paired_config(tmp_path)
    _seed_history(tmp_path, [
        {"job_id": "jobDone", "state": "done"},
        {"job_id": "jobUnknown", "state": "unknown"},
    ])
    app = create_app(
        config_path=str(tmp_path / "config.json"),
        history_path=str(tmp_path / "jobs.json"),
        reconcile_interval=0.05,
        client_factory=lambda url, key: fake,
    )
    with TestClient(app):
        time.sleep(0.15)
        assert "jobDone" not in fake.status_calls
        assert "jobUnknown" not in fake.status_calls


def test_reconcile_mixed_batch_partial_failure(tmp_path):
    """一轮里一条网络失败，另一条终态照常落盘（单条失败不影响其他记录）。"""
    fake = ReconcileFakeClient({
        "jobOK": "done",
        "jobBad": ListenerError("down"),
    })
    _paired_config(tmp_path)
    _seed_history(tmp_path, [
        {"job_id": "jobOK", "state": "running"},
        {"job_id": "jobBad", "state": "running"},
    ])
    app = create_app(
        config_path=str(tmp_path / "config.json"),
        history_path=str(tmp_path / "jobs.json"),
        reconcile_interval=0.05,
        client_factory=lambda url, key: fake,
    )
    with TestClient(app):
        assert _wait_until(lambda: any(
            j["job_id"] == "jobOK" and j["state"] == "done"
            for j in HistoryStore(tmp_path / "jobs.json").all()))
        jobs = HistoryStore(tmp_path / "jobs.json").all()
        assert [j for j in jobs if j["job_id"] == "jobBad"][0]["state"] == "running"
