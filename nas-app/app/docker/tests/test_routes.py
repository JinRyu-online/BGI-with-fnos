import json
from pathlib import Path

from fastapi.testclient import TestClient

from main import create_app


class FakeClient:
    """可编程的假监听器客户端，用于路由测试。"""

    def __init__(self, *, health_data=None, tasks_data=None, job_id="job1",
                 status_state="running", raise_auth=False, raise_network=False,
                 trigger_error=False):
        self._health = health_data or {"service": "bgi-trigger", "hostname": "H", "version": "1"}
        self._tasks = tasks_data if tasks_data is not None else [{"id": "daily", "display_name": "日常", "groups": ["g"], "timeout_min": 90, "after_done": "sleep"}]
        self._job_id = job_id
        self._status_state = status_state
        self._raise_auth = raise_auth
        self._raise_network = raise_network
        self._trigger_error = trigger_error

    def health(self):
        return self._health

    def tasks(self):
        if self._raise_auth:
            from listener_client import ListenerAuthError
            raise ListenerAuthError("bad key")
        if self._raise_network:
            from listener_client import ListenerError
            raise ListenerError("down")
        return self._tasks

    def trigger(self, task_id):
        if self._trigger_error:
            from listener_client import ListenerError
            raise ListenerError("down")
        return {"job_id": self._job_id}

    def status(self, job_id):
        return {"id": job_id, "state": self._status_state, "task_id": "daily"}


def _make(tmp_path, *, scanner=None, client=None):
    """构造测试 app。client 为 FakeClient 实例，作为 client_factory 的返回。"""
    fake = client or FakeClient()
    return create_app(
        config_path=str(tmp_path / "config.json"),
        history_path=str(tmp_path / "jobs.json"),
        scanner=scanner or (lambda subnet, port: []),
        client_factory=lambda url, key: fake,
    )


def test_scan_returns_devices(tmp_path):
    devices = [{"ip": "192.168.1.10", "port": 8765, "hostname": "DESKTOP-A", "version": "1"}]
    client = TestClient(_make(tmp_path, scanner=lambda subnet, port: devices))

    r = client.post("/api/scan")

    assert r.status_code == 200
    assert r.json()["devices"] == devices


def test_pair_saves_target_and_key(tmp_path):
    client = TestClient(_make(tmp_path, client=FakeClient()))
    r = client.post("/api/pair", json={
        "ip": "192.168.1.10", "port": 8765, "hostname": "DESKTOP-A", "api_key": "secret"
    })

    assert r.status_code == 200
    # 配置已落盘
    cfg = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert cfg["default_target"]["ip"] == "192.168.1.10"
    assert cfg["api_key"] == "secret"


def test_pair_with_bad_key_returns_401(tmp_path):
    client = TestClient(_make(tmp_path, client=FakeClient(raise_auth=True)))
    r = client.post("/api/pair", json={
        "ip": "192.168.1.10", "port": 8765, "hostname": "X", "api_key": "wrong"
    })
    assert r.status_code == 401


def test_tasks_unpaired_returns_400(tmp_path):
    client = TestClient(_make(tmp_path))
    r = client.get("/api/tasks")
    assert r.status_code == 400


def test_tasks_after_pair_returns_list(tmp_path):
    app = _make(tmp_path, client=FakeClient(tasks_data=[{"id": "daily", "display_name": "日常", "groups": ["g"], "timeout_min": 90, "after_done": "sleep"}]))
    c = TestClient(app)
    c.post("/api/pair", json={"ip": "1.1.1.1", "port": 8765, "hostname": "H", "api_key": "k"})

    r = c.get("/api/tasks")
    assert r.status_code == 200
    assert r.json()[0]["id"] == "daily"


def test_trigger_returns_job_id_and_records_history(tmp_path):
    app = _make(tmp_path, client=FakeClient(job_id="job42", status_state="running"))
    c = TestClient(app)
    c.post("/api/pair", json={"ip": "1.1.1.1", "port": 8765, "hostname": "H", "api_key": "k"})

    r = c.post("/api/trigger", json={"task_id": "daily"})
    assert r.status_code == 202
    assert r.json()["job_id"] == "job42"

    # 历史已记录
    jobs = c.get("/api/jobs").json()
    assert jobs[0]["job_id"] == "job42"


def test_status_proxies_and_updates_history(tmp_path):
    app = _make(tmp_path, client=FakeClient(job_id="job42", status_state="done"))
    c = TestClient(app)
    c.post("/api/pair", json={"ip": "1.1.1.1", "port": 8765, "hostname": "H", "api_key": "k"})
    c.post("/api/trigger", json={"task_id": "daily"})

    r = c.get("/api/status", params={"job_id": "job42"})
    assert r.status_code == 200
    assert r.json()["state"] == "done"

    # 历史更新为 done
    jobs = c.get("/api/jobs").json()
    assert jobs[0]["state"] == "done"


def test_trigger_network_error_returns_502(tmp_path):
    app = _make(tmp_path, client=FakeClient(trigger_error=True))
    c = TestClient(app)
    c.post("/api/pair", json={"ip": "1.1.1.1", "port": 8765, "hostname": "H", "api_key": "k"})

    r = c.post("/api/trigger", json={"task_id": "daily"})
    assert r.status_code == 502
