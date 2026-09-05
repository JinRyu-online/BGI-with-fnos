import json
import time
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
        self._aborted = False

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

    def abort(self):
        self._aborted = True
        return {"aborted": True}

    def stop(self):
        self._stopped = True
        return {"stopped": True, "killed": ["BetterGI.exe"]}


def _make(tmp_path, *, scanner=None, client=None):
    """构造测试 app。client 为 FakeClient 实例，作为 client_factory 的返回。"""
    fake = client or FakeClient()
    return create_app(
        config_path=str(tmp_path / "config.json"),
        history_path=str(tmp_path / "jobs.json"),
        scanner=scanner or (lambda subnet, port: []),
        client_factory=lambda url, key: fake,
    )


def test_scan_returns_devices_sync(tmp_path):
    """同步模式：POST /api/scan {"sync":true} 直接返回设备。"""
    devices = [{"ip": "192.168.1.10", "port": 8765, "hostname": "DESKTOP-A", "version": "1"}]
    client = TestClient(_make(tmp_path, scanner=lambda subnet, port: devices))

    r = client.post("/api/scan", json={"sync": True})

    assert r.status_code == 200
    assert r.json()["devices"] == devices
    assert r.json()["sync"] is True


def test_scan_async_started(tmp_path):
    """异步模式：POST /api/scan 启动后台任务，返回 {"started": True}。"""
    devices = [{"ip": "192.168.1.10", "port": 8765, "hostname": "A", "version": "1"}]

    def fake_scanner(subnet, port, progress_cb=None):
        if progress_cb:
            progress_cb(subnet, devices)
        return devices

    client = TestClient(_make(tmp_path, scanner=fake_scanner))

    r = client.post("/api/scan")
    assert r.status_code == 200
    assert r.json()["started"] is True

    # 等待后台扫描完成
    for _ in range(50):
        p = client.get("/api/scan-progress").json()
        if not p["active"]:
            break
        time.sleep(0.05)
    final = client.get("/api/scan-progress").json()
    assert final["stage"] == "done"
    assert len(final["devices"]) == 1
    assert final["devices"][0]["ip"] == "192.168.1.10"


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


def test_abort_forwards_to_listener(tmp_path):
    """POST /api/abort 转发到监听器，返回 {"aborted": True} 并落地历史。"""
    fake = FakeClient(status_state="completing")
    app = _make(tmp_path, client=fake)
    c = TestClient(app)
    c.post("/api/pair", json={"ip": "1.1.1.1", "port": 8765, "hostname": "H", "api_key": "k"})

    r = c.post("/api/abort")
    assert r.status_code == 200
    assert r.json() == {"aborted": True}
    assert fake._aborted is True  # 确实转发到了客户端 abort()


def test_abort_unpaired_returns_400(tmp_path):
    """未配对时 POST /api/abort 返回 400。"""
    c = TestClient(_make(tmp_path))
    r = c.post("/api/abort")
    assert r.status_code == 400


def test_stop_forwards_to_listener(tmp_path):
    """POST /api/stop 转发到监听器，返回 {"stopped": True, "killed": [...]}。"""
    fake = FakeClient()
    app = _make(tmp_path, client=fake)
    c = TestClient(app)
    c.post("/api/pair", json={"ip": "1.1.1.1", "port": 8765, "hostname": "H", "api_key": "k"})

    r = c.post("/api/stop")
    assert r.status_code == 200
    assert r.json() == {"stopped": True, "killed": ["BetterGI.exe"]}
    assert fake._stopped is True  # 确实转发到了客户端 stop()


def test_stop_unpaired_returns_400(tmp_path):
    """未配对时 POST /api/stop 返回 400。"""
    c = TestClient(_make(tmp_path))
    r = c.post("/api/stop")
    assert r.status_code == 400


def test_stop_auth_error_returns_401(tmp_path):
    """密钥失效时 POST /api/stop 返回 401（与 /api/abort 映射一致）。"""
    from listener_client import ListenerAuthError

    class AuthFailClient(FakeClient):
        def stop(self):
            raise ListenerAuthError("bad key")

    app = _make(tmp_path, client=AuthFailClient())
    c = TestClient(app)
    c.post("/api/pair", json={"ip": "1.1.1.1", "port": 8765, "hostname": "H", "api_key": "k"})
    r = c.post("/api/stop")
    assert r.status_code == 401


def test_stop_listener_error_returns_502(tmp_path):
    """监听器不可达时 POST /api/stop 返回 502（与 /api/abort 映射一致）。"""
    from listener_client import ListenerError

    class DownClient(FakeClient):
        def stop(self):
            raise ListenerError("down")

    app = _make(tmp_path, client=DownClient())
    c = TestClient(app)
    c.post("/api/pair", json={"ip": "1.1.1.1", "port": 8765, "hostname": "H", "api_key": "k"})
    r = c.post("/api/stop")
    assert r.status_code == 502


def test_discover_key_returns_api_key(tmp_path, monkeypatch):
    """GET /api/discover-key 代理目标监听器的 /key，返回 api_key + hostname。"""
    import main as main_mod

    def fake_http_get_json(ip, port, path="/", timeout=3.0):
        assert (ip, port, path) == ("192.168.31.43", 8765, "/key")
        return {"api_key": "abc", "hostname": "H"}

    monkeypatch.setattr(main_mod, "_http_get_json", fake_http_get_json)
    c = TestClient(_make(tmp_path))
    r = c.get("/api/discover-key", params={"ip": "192.168.31.43", "port": 8765})
    assert r.status_code == 200
    assert r.json()["api_key"] == "abc"
    assert r.json()["hostname"] == "H"


def test_discover_key_missing_ip_400(tmp_path):
    """缺 ip 返回 400。"""
    c = TestClient(_make(tmp_path))
    r = c.get("/api/discover-key")
    assert r.status_code == 422  # FastAPI Query(...) 必填校验


def test_scan_with_explicit_subnet_skips_fallback(tmp_path):
    """显式 subnet: 只扫该子网，不做 COMMON_SUBNETS 回退。"""
    calls = []

    def tracking_scanner(subnet, port):
        calls.append(subnet)
        return [{"ip": "1.2.3.4", "port": port, "hostname": "X", "version": "1"}]

    c = TestClient(_make(tmp_path, scanner=tracking_scanner))
    r = c.post("/api/scan", json={"subnet": "1.2.3.0/24", "port": 8765, "sync": True})

    assert r.status_code == 200
    assert calls == ["1.2.3.0/24"]  # 显式 → 只搜这一网,无 fallback


def test_tasks_replace_proxy(tmp_path):
    """PUT /api/tasks 转发到 Windows PUT /tasks；未配对 400。"""
    from main import create_app

    captured = {}

    def fake_factory(url, key):
        class _C:
            def replace_tasks(self, tasks):
                captured["tasks"] = tasks
                return [{"id": t["id"], "display_name": t["display_name"],
                         "groups": t["groups"], "timeout_min": t["timeout_min"],
                         "after_done": t["after_done"]} for t in tasks]
        return _C()

    app = create_app(
        config_path=str(tmp_path / "config.json"),
        history_path=str(tmp_path / "jobs.json"),
        client_factory=fake_factory, reconcile_interval=0, scheduler_interval=0,
    )
    from settings import Settings
    s = Settings(tmp_path / "config.json")
    cfg = s.load()
    cfg["default_target"] = {"ip": "10.0.0.5", "port": 8765, "hostname": "PC"}
    cfg["api_key"] = "k"
    s.save(cfg)

    client = TestClient(app)
    body = {"tasks": [{"id": "mining", "display_name": "挖矿", "groups": ["采矿"],
                       "timeout_min": 45, "after_done": "sleep"}]}
    r = client.put("/api/tasks", json=body)
    assert r.status_code == 200, r.text
    assert r.json()[0]["id"] == "mining"
    assert captured["tasks"][0]["id"] == "mining"


def test_tasks_replace_unpaired(tmp_path):
    from main import create_app
    app = create_app(
        config_path=str(tmp_path / "config.json"),
        history_path=str(tmp_path / "jobs.json"),
        reconcile_interval=0, scheduler_interval=0,
    )
    client = TestClient(app)
    r = client.put("/api/tasks", json={"tasks": []})
    assert r.status_code == 400
