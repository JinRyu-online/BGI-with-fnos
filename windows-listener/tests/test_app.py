import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from bgi_trigger.api.app import create_app, AppDeps
from bgi_trigger.service.auth import AuthState
from bgi_trigger.core.state import JobStore
from bgi_trigger.core.tasks import TaskRegistry
from bgi_trigger.core.log_harvester import LogHarvester

AUTH = {"Authorization": "Bearer secret"}


def _registry() -> TaskRegistry:
    # 写一个临时任务目录（新 API：TaskRegistry 接收路径，支持目录热加载）
    d = Path(tempfile.mkdtemp())
    (d / "daily.json").write_text(json.dumps({
        "id": "daily", "display_name": "日常一条龙",
        "groups": ["日常一条龙", "关闭游戏"], "timeout_min": 90, "after_done": "sleep",
    }, ensure_ascii=False), encoding="utf-8")
    return TaskRegistry(d)


def _deps(launch=None, jobs=None):
    return AppDeps(
        hostname="DESKTOP-TEST",
        version="1.0.0",
        tasks=_registry(),
        auth=AuthState(api_key="secret", trusted_ips=[]),
        jobs=jobs or JobStore(),
        launch=launch or (lambda job, task: None),
    )


def test_health_no_auth_returns_signature():
    client = TestClient(create_app(_deps()))
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "bgi-trigger"
    assert body["hostname"] == "DESKTOP-TEST"
    assert body["version"] == "1.0.0"


def test_key_no_auth_returns_api_key_and_hostname():
    client = TestClient(create_app(_deps()))
    r = client.get("/key")
    assert r.status_code == 200
    body = r.json()
    assert body["api_key"] == "secret"
    assert body["hostname"] == "DESKTOP-TEST"


def test_tasks_without_auth_rejected():
    client = TestClient(create_app(_deps()))
    r = client.get("/tasks")
    assert r.status_code == 401


def test_tasks_with_correct_key_returns_list():
    client = TestClient(create_app(_deps()))
    r = client.get("/tasks", headers=AUTH)
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["id"] == "daily"
    assert items[0]["display_name"] == "日常一条龙"
    assert items[0]["groups"] == ["日常一条龙", "关闭游戏"]


def test_wrong_key_rejected():
    client = TestClient(create_app(_deps()))
    r = client.get("/tasks", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_trigger_starts_job_and_returns_id():
    client = TestClient(create_app(_deps()))
    r = client.post("/trigger", json={"task_id": "daily"}, headers=AUTH)
    assert r.status_code == 202
    assert "job_id" in r.json()


def test_trigger_unknown_task_404():
    client = TestClient(create_app(_deps()))
    r = client.post("/trigger", json={"task_id": "nope"}, headers=AUTH)
    assert r.status_code == 404


def test_trigger_while_busy_returns_409():
    launched = []
    def fake_launch(job, task):
        # leave the job RUNNING (don't finalize)
        launched.append(job.id)

    client = TestClient(create_app(_deps(launch=fake_launch)))
    r1 = client.post("/trigger", json={"task_id": "daily"}, headers=AUTH)
    assert r1.status_code == 202
    r2 = client.post("/trigger", json={"task_id": "daily"}, headers=AUTH)
    assert r2.status_code == 409


def test_status_returns_job_state():
    def fake_launch(job, task):
        job.state  # already RUNNING from jobs.start

    deps = _deps(launch=fake_launch)
    client = TestClient(create_app(deps))
    r = client.post("/trigger", json={"task_id": "daily"}, headers=AUTH)
    job_id = r.json()["job_id"]

    s = client.get("/status", params={"job_id": job_id}, headers=AUTH)
    assert s.status_code == 200
    assert s.json()["state"] == "running"
    assert s.json()["task_id"] == "daily"


def test_status_unknown_job_404():
    client = TestClient(create_app(_deps()))
    s = client.get("/status", params={"job_id": "bogus"}, headers=AUTH)
    assert s.status_code == 404


def test_launch_is_called_with_job_and_task():
    captured = {}
    def fake_launch(job, task):
        captured["job_id"] = job.id
        captured["task_id"] = task.id
        captured["groups"] = task.groups

    client = TestClient(create_app(_deps(launch=fake_launch)))
    client.post("/trigger", json={"task_id": "daily"}, headers=AUTH)

    assert captured["task_id"] == "daily"
    assert captured["groups"] == ["日常一条龙", "关闭游戏"]
    assert captured["job_id"]


# ── v0.2.0 回归: log_path 注入 + /ws/logs 端点 ──

def _deps_with_log(tmp_log_path: str, launch=None):
    """构造带 log_path 的 AppDeps,供 /trigger 与 /ws/logs 测试共用。"""
    return AppDeps(
        hostname="DESKTOP-TEST",
        version="1.0.0",
        tasks=_registry(),
        auth=AuthState(api_key="secret", trusted_ips=[]),
        jobs=JobStore(),
        launch=launch or (lambda job, task: None),
        log_path=tmp_log_path,
    )


def test_trigger_injects_log_path_to_job():
    """v0.2.0 回归: /trigger 必须把 deps.log_path 注入 job,否则 harvester 不启动。
    修复前: app.py 用 getattr(deps, 'bettergi', None) -> 永远 None -> harvester 永不创建。"""
    captured = {}
    def fake_launch(job, task):
        captured["log_path"] = job.log_path

    tmp_log = Path(tempfile.mkdtemp()) / "test.log"
    tmp_log.write_text("", encoding="utf-8")

    client = TestClient(create_app(_deps_with_log(str(tmp_log), launch=fake_launch)))
    r = client.post("/trigger", json={"task_id": "daily"}, headers=AUTH)
    assert r.status_code == 202
    # 关键断言:log_path 必须被注入到 job
    assert captured["log_path"] == str(tmp_log)


def test_ws_logs_unknown_job_returns_error():
    """v0.2.0: /ws/logs/{job_id} 对未知 job 应返回 error + 关闭。"""
    from starlette.testclient import WebSocketTestSession as _  # noqa: F401 (仅确认导入)
    client = TestClient(create_app(_deps()))
    with client.websocket_connect("/ws/logs/nonexist") as ws:
        data = ws.receive_json()
        assert "error" in data


def test_ws_logs_unknown_job_id_format_rejected():
    """v0.2.0: WS 对不合法 job_id 格式应拒绝(防路径遍历/注入)。"""
    client = TestClient(create_app(_deps()))


def test_harvester_creates_and_buffers_lines():
    """v0.2.0: LogHarvester 行级收割——跨块切割的日志行应完整保留。
    收割器从文件末尾开始读,故先启动收割器(空文件),再写入内容。"""
    import time

    tmp_dir = Path(tempfile.mkdtemp())
    tmp_log = tmp_dir / "test.log"
    tmp_log.write_text("", encoding="utf-8")  # 创建空文件

    h = LogHarvester(job_id="a" * 12, log_path=str(tmp_log))
    h.start()
    try:
        # 写入跨 64KB 边界的内容:一条长行 + 短行
        # (harvester 线程在 _run 里等文件出现(空文件已存在即符合),随即开始收割)
        long_line = "x" * 100000
        with open(tmp_log, "a", encoding="utf-8") as f:
            f.write(f"[start] {long_line}\n[short] hello\n")

        # 等待收割完成
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(h.recent(10)) < 2:
            time.sleep(0.1)
        buf = h.recent(10)
        # 关键:跨块的长行应作为完整一行保留(行数 == 2,不是被切成多行)
        assert len(buf) == 2, f"行级收割应保留完整长行, got {len(buf)} lines"
        assert buf[0].startswith("[start]")
        assert buf[1].strip() == "[short] hello"
    finally:
        h.stop()
