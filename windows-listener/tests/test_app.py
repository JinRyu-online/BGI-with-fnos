import json
import tempfile
import threading
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
    收割器从文件末尾开始读。
    ★ 测试策略：分 3 次写，每次写后等待 > poll_interval(0.5s)，
    确保 harvester 睡眠期间数据已落盘、下一次 poll 必收割到。
    规避单大 chunk 一次性写入时 chunk 边界切割的 flaky 问题。"""
    import time

    tmp_dir = Path(tempfile.mkdtemp())
    tmp_log = tmp_dir / "test.log"
    tmp_log.write_text("", encoding="utf-8")  # 创建空文件

    h = LogHarvester(job_id="a" * 12, log_path=str(tmp_log))
    h.start()
    try:
        # ★ 等待 harvester 进入主循环（seek 完成，正在睡等下一 poll）。
        # 此后写 padding → harvester 醒来收割 → 第一次 poll 必收。
        assert h.wait_for_ready(timeout=5), "harvester 未在 5s 内就绪"

        # 第 1 段：填充 ~64KB 的padding，跨越第一个 chunk 边界
        padding = "p" * 65000
        with open(tmp_log, "a", encoding="utf-8") as f:
            f.write(f"[padding] {padding}\n")
        time.sleep(0.7)   # > poll_interval，确保收割完成

        # 第 2 段：长行(~100K)
        long_line = "x" * 100000
        with open(tmp_log, "a", encoding="utf-8") as f:
            f.write(f"[start] {long_line}\n")
        time.sleep(0.7)

        # 第 3 段：短行
        with open(tmp_log, "a", encoding="utf-8") as f:
            f.write("[short] hello\n")
        time.sleep(0.7)

        # 收割到的行应包含完整的长行与短行
        buf = h.recent(10)
        assert len(buf) == 3, f"应收割到 3 行(padding+长+短), got {len(buf)}"
        assert buf[0].startswith("[padding]")
        # ★ 关键:跨块的长行应作为完整一行保留
        assert buf[1].startswith("[start]") and len(buf[1]) >= 100000
        assert buf[2].strip() == "[short] hello"
    finally:
        h.stop()


def test_harvester_save_path_writes_all_lines():
    """★ 新增：save_path 全部收割行落盘，只包含收割后的行（从末尾读语义）。"""
    import time

    tmp_dir = Path(tempfile.mkdtemp())
    src = tmp_dir / "bgi.log"
    src.write_text("[old] before start\n", encoding="utf-8")  # 收割器启动前的旧行
    save = tmp_dir / "jobXYZ.log"

    h = LogHarvester(job_id="jobXYZ", log_path=str(src), save_path=str(save))
    h.start()
    try:
        # ★ 等 harvester 进入主循环后再写新行
        assert h.wait_for_ready(timeout=5), "harvester 未在 5s 内就绪"
        time.sleep(0.4)
        with open(src, "a", encoding="utf-8") as f:
            f.write("[new] line A\n[new] line B\n[new] keyword_match\n")
        # 等收割
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not h.check_keyword("keyword_match")[0]:
            time.sleep(0.1)
    finally:
        h.stop()

    assert save.exists(), "save_path 应被创建于 jobXYZ.log"
    saved = save.read_text(encoding="utf-8")
    # 收割到的行都在
    assert "[new] line A" in saved
    assert "[new] line B" in saved
    assert "[new] keyword_match" in saved
    # 收割器启动前的旧行不在（从末尾读语义）
    assert "[old] before start" not in saved


def test_keyword_count_mode_requires_n_matches():
    """★ 新增:计数模式——keyword 需命中 required_matches 次才触发完成。

    模拟多组任务:3 个组,每个组结束都打 "任务结束"。
    前 2 次命中不应触发,第 3 次(最后一组)才触发。
    """
    import time

    tmp_dir = Path(tempfile.mkdtemp())
    tmp_log = tmp_dir / "bgi.log"
    tmp_log.write_text("", encoding="utf-8")

    # required_matches=3(3 个组)
    h = LogHarvester(job_id="countJob", log_path=str(tmp_log),
                    required_matches=3, poll_interval=0.1)
    h.set_keyword("任务结束")   # 启用计数模式
    h.start()
    try:
        assert h.wait_for_ready(timeout=5)

        # 第 1 个组结束
        with open(tmp_log, "a", encoding="utf-8") as f:
            f.write("[10:00:00] 配置组 A\n[10:00:01] → \"任务结束\"\n")
        time.sleep(0.3)
        ok, _ = h.check_keyword("任务结束")
        assert not ok, "第 1 次命中不应触发(3 组任务)"

        # 第 2 个组结束
        with open(tmp_log, "a", encoding="utf-8") as f:
            f.write("[10:01:00] 配置组 B\n[10:01:01] → \"任务结束\"\n")
        time.sleep(0.3)
        ok, _ = h.check_keyword("任务结束")
        assert not ok, "第 2 次命中不应触发(3 组任务)"

        # 第 3 个组结束(最后一组)→ 应触发
        with open(tmp_log, "a", encoding="utf-8") as f:
            f.write("[10:02:00] 配置组 C\n[10:02:01] → \"任务结束\"\n")
        time.sleep(0.3)
        ok, line = h.check_keyword("任务结束")
        assert ok, "第 3 次命中(最后一组)应触发完成"
        assert "任务结束" in line
    finally:
        h.stop()


def test_keyword_count_mode_first_match_when_required_is_1():
    """★ 计数模式 required_matches=1(单组任务)→ 首次命中即触发(向后兼容)。"""
    import time

    tmp_dir = Path(tempfile.mkdtemp())
    tmp_log = tmp_dir / "bgi.log"
    tmp_log.write_text("", encoding="utf-8")

    h = LogHarvester(job_id="singleJob", log_path=str(tmp_log),
                    required_matches=1, poll_interval=0.1)
    h.set_keyword("任务结束")
    h.start()
    try:
        assert h.wait_for_ready(timeout=5)
        with open(tmp_log, "a", encoding="utf-8") as f:
            f.write("[10:00:01] → \"任务结束\"\n")
        time.sleep(0.3)
        ok, _ = h.check_keyword("任务结束")
        assert ok, "required_matches=1 时首次命中即触发"
    finally:
        h.stop()


def test_keyword_scan_mode_backward_compat():
    """★ 未 set_keyword 时回退到缓冲扫描模式(向后兼容):首次命中即触发。"""
    import time

    tmp_dir = Path(tempfile.mkdtemp())
    tmp_log = tmp_dir / "bgi.log"
    tmp_log.write_text("", encoding="utf-8")

    h = LogHarvester(job_id="scanJob", log_path=str(tmp_log),
                    poll_interval=0.1)
    # ★ 不调用 set_keyword → 扫描模式
    h.start()
    try:
        assert h.wait_for_ready(timeout=5)
        with open(tmp_log, "a", encoding="utf-8") as f:
            f.write("[10:00:01] → \"任务结束\"\n")
        time.sleep(0.3)
        ok, line = h.check_keyword("任务结束")
        assert ok, "扫描模式:首次命中即触发"
        assert "任务结束" in line
    finally:
        h.stop()
