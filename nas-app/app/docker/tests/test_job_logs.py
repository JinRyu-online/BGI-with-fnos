"""历史日志回看（B2 录制 + tee 接管 + 读取接口 + spa_fallback 深链）测试。

Fake upstream 沿用 test_ws_proxy.py 的线程内 websockets.serve 模式。
覆盖计划 §5 用例组：store 白名单/往返/prune、GET /api/logs（404/400/钳制）、
WS 代理 tee（写盘/标记时不写/接管/非 lines 帧跳过/非法 job_id）、
B2 录制器（error 停留标记/初始重试耗尽/请求路径置位/中途断开重连接续）、
spa_fallback 两段路由回退与资产 404。
"""
import asyncio
import json
import os
import time

import pytest
import websockets
from fastapi.testclient import TestClient

from job_log_store import JobLogStore, valid_job_id
from main import create_app
try:
    from websockets.asyncio.server import serve as ws_serve
except ImportError:  # 旧版本兜底
    from websockets.serve import ws_serve  # type: ignore


class FakeUpstream:
    """假监听器 WS 服务：接受连接后按剧本发消息，可记录收到的消息与连接数。

    script 为 None 表示连接后不发消息挂住等待；否则连接建立即依次发送。
    connections 计数用于断言 B2 重连行为。
    """

    def __init__(self):
        self.received = []
        self.script = None
        self.server = None
        self.loop = None
        self.thread = None
        self.port = None
        import threading
        self.connected = threading.Event()
        self.connections = 0
        self._clients = set()

    def start(self):
        import threading
        ready = threading.Event()

        async def handler(ws):
            self.connections += 1
            self.connected.set()
            self._clients.add(ws)
            try:
                for msg in (self.script or []):
                    await ws.send(msg)
                async for msg in ws:
                    self.received.append(msg)
            except websockets.ConnectionClosed:
                pass
            finally:
                self._clients.discard(ws)

        async def _run():
            self.server = await ws_serve(handler, "127.0.0.1", 0)
            self.port = self.server.sockets[0].getsockname()[1]
            ready.set()
            await self.server.wait_closed()

        def _target():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            try:
                self.loop.run_until_complete(_run())
            except asyncio.CancelledError:
                pass
            finally:
                self.loop.close()

        self.thread = threading.Thread(target=_target, daemon=True)
        self.thread.start()
        assert ready.wait(timeout=5), "fake upstream failed to start"

    def stop(self):
        if self.loop is not None and self.loop.is_running():
            async def _shutdown():
                self.server.close()
                await self.server.wait_closed()
            try:
                asyncio.run_coroutine_threadsafe(_shutdown(), self.loop).result(timeout=5)
            except Exception:
                pass
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread is not None:
            self.thread.join(timeout=5)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()


import threading  # noqa: E402  (FakeUpstream.start/stop 内部引用)


def _app_pointing_at(tmp_path, port, *, api_key="k"):
    """app 已配对指向 fake upstream 端口；日志目录注入 tmp_path。"""
    cfg = {"default_target": {"ip": "127.0.0.1", "port": port, "hostname": "PC"},
           "api_key": api_key, "target_mac": ""}
    (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return create_app(
        config_path=str(tmp_path / "config.json"),
        history_path=str(tmp_path / "jobs.json"),
        logs_dir=str(tmp_path / "jobs_log"),
        reconcile_interval=0,
    )


def _app_unpaired(tmp_path):
    """未配对 app（读取接口/spa_fallback 测试用；不触网）。"""
    return create_app(
        config_path=str(tmp_path / "config.json"),
        history_path=str(tmp_path / "jobs.json"),
        logs_dir=str(tmp_path / "jobs_log"),
        reconcile_interval=0,
    )


def _read_lines(tmp_path, job_id):
    p = tmp_path / "jobs_log" / f"{job_id}.log"
    if not p.exists():
        return []
    return p.read_text(encoding="utf-8").splitlines()


def _wait_release(store, job_id, timeout=20.0):
    """等录制线程退出（写者释放）。"""
    deadline = time.time() + timeout
    while time.time() < deadline and store.is_recording(job_id):
        time.sleep(0.05)
    return not store.is_recording(job_id)


# ============ 1. store 白名单校验 ============

def test_valid_job_id_whitelist():
    """合法（uuid hex+连字符/下划线）通过；穿越/特殊字符/空串拒绝。"""
    assert valid_job_id("abc-123_ABC")
    assert valid_job_id("a1b2c3d4e5f6")
    assert not valid_job_id("")
    assert not valid_job_id("../evil")
    assert not valid_job_id("a/b")
    assert not valid_job_id("a b")
    assert not valid_job_id("a\nb")
    assert not valid_job_id("任务")


# ============ 2. append/read_tail 往返 ============

def test_store_append_read_tail_roundtrip(tmp_path):
    """追加读回往返：tail<N 取全部、tail<=N 取末 n 行、无尾换行残行截断。"""
    s = JobLogStore(logs_dir=tmp_path / "jobs_log")
    s.append_lines("j1", [f"l{i}" for i in range(10)])
    assert s.has_log("j1")
    assert s.read_tail("j1", 5) == ["l5", "l6", "l7", "l8", "l9"]
    assert s.read_tail("j1", 100) == [f"l{i}" for i in range(10)]
    assert s.read_tail("nope", 10) == []
    # 残行：末尾无换行符 → 末行丢弃（写了一半的半行）
    (tmp_path / "jobs_log" / "j2.log").write_text("a\nb\nc", encoding="utf-8")
    assert s.read_tail("j2", 10) == ["a", "b"]
    # 空批不落盘
    s.append_lines("j3", [])
    assert not s.has_log("j3")


def test_store_read_tail_reverse_chunked_large_file(tmp_path):
    """大文件（>64KB，跨多个 64KB 分块）反向读取正确。"""
    s = JobLogStore(logs_dir=tmp_path / "jobs_log")
    big = [f"line-{i:06d}" for i in range(5000)]
    s.append_lines("big", big)
    assert s.read_tail("big", 3) == big[-3:]
    assert s.read_tail("big", 5000) == big


def test_store_prune_keeps_newest(tmp_path):
    """prune(keep)：文件数超 keep 删最旧（append 新 job 首行时触发）。"""
    s = JobLogStore(logs_dir=tmp_path / "jobs_log", keep=3)
    d = tmp_path / "jobs_log"
    d.mkdir(parents=True)
    # 直接铺 5 个旧文件（set mtime 递增），避免逐个 append 触发中途 prune
    for i in range(5):
        (d / f"old{i}.log").write_text("x\n", encoding="utf-8")
        os.utime(d / f"old{i}.log", (1000000 + i, 1000000 + i))
    s.append_lines("new", ["y"])  # 新 job 首行 → prune(keep=3)：6 个文件删最旧 3 个
    files = sorted(p.name for p in d.glob("*.log"))
    assert files == ["new.log", "old3.log", "old4.log"], files


# ============ 3+7. GET /api/logs：404 / 白名单 400 / tail 钳制 ============

def test_api_logs_missing_returns_404(tmp_path):
    app = _app_unpaired(tmp_path)
    with TestClient(app) as c:
        r = c.get("/api/logs/ghost-job")
        assert r.status_code == 404
        assert r.json()["detail"] == "无日志记录"


def test_api_logs_invalid_job_id_returns_400(tmp_path):
    app = _app_unpaired(tmp_path)
    with TestClient(app) as c:
        # FastAPI 路径参数按 / 分段：%2F 会被 ASGI 层还原成路径分隔符，
        # 落不到本路由（404 由路由系统给出）——白名单 400 用不含 / 的非法字符验证
        assert c.get("/api/logs/a%20b").status_code == 400       # 空格
        assert c.get("/api/logs/%E4%BB%BB%E5%8A%A1").status_code == 400  # 中文
        # 含 / 的穿越串到不了路由（404），同样不允许——两条路径都不可达
        assert c.get("/api/logs/..%2Fevil").status_code == 404


def test_api_logs_tail_clamped_and_roundtrip(tmp_path):
    """tail 默认 1000、上限 5000 钳制；响应 {job_id, lines}。"""
    store = JobLogStore(logs_dir=tmp_path / "jobs_log")
    store.append_lines("j9", [f"L{i}" for i in range(5)])
    app = _app_unpaired(tmp_path)
    with TestClient(app) as c:
        r = c.get("/api/logs/j9")
        assert r.status_code == 200
        assert r.json()["job_id"] == "j9"
        assert r.json()["lines"] == ["L0", "L1", "L2", "L3", "L4"]
        # tail 小于行数 → 取末 tail 行
        r = c.get("/api/logs/j9?tail=2")
        assert r.json()["lines"] == ["L3", "L4"]
        # tail 超 5000 → 钳制到 5000（不报错，照常返回可用行）
        r = c.get("/api/logs/j9?tail=999999")
        assert r.status_code == 200
        assert len(r.json()["lines"]) == 5


# ============ 4. WS 代理 tee ============

def test_ws_proxy_tee_writes_frames_to_disk(tmp_path):
    """无录制标记时 tee 接管：上游 lines 帧顺带写盘，断开后 release_recorder。"""
    msg1 = json.dumps({"ts": 1.0, "lines": ["[Info] a", "[Info] b"]})
    with FakeUpstream() as up:
        up.script = [msg1]
        app = _app_pointing_at(tmp_path, up.port)
        with TestClient(app) as c:
            with c.websocket_connect("/api/ws/logs/jobT1") as ws:
                assert up.connected.wait(timeout=5)
                time.sleep(0.3)  # 让 pump 循环处理完帧
                ws.close()  # 显式关闭（模拟浏览器离开页面）
                # 断开后：写者释放（轮询必须在 ws 上下文内——TestClient 退出会关闭
                # portal 事件循环，handler finally 的收尾会随其中断）
                deadline = time.time() + 5
                while time.time() < deadline and app.state.job_logs.is_recording("jobT1"):
                    time.sleep(0.05)
            assert _read_lines(tmp_path, "jobT1") == ["[Info] a", "[Info] b"]
            assert not app.state.job_logs.is_recording("jobT1")


def test_ws_proxy_tee_skipped_when_recorder_flagged(tmp_path):
    """B2 已置标记并持有写者 → tee acquire 失败 → 纯透传不写盘。"""
    msg1 = json.dumps({"ts": 1.0, "lines": ["[Info] tee-should-not-write"]})
    with FakeUpstream() as up:
        up.script = [msg1]
        app = _app_pointing_at(tmp_path, up.port)
        store = app.state.job_logs
        # 模拟 B2 写者就位：set_recorder(True) 即原子登记（与 try_acquire 同一语义）
        assert store.set_recorder("jobT2", True)
        assert store.is_recording("jobT2")
        with TestClient(app) as c:
            with c.websocket_connect("/api/ws/logs/jobT2") as ws:
                assert up.connected.wait(timeout=5)
                time.sleep(0.3)
                ws.close()
            # B2 释放（模拟录制结束）
            store.release_recorder("jobT2")
        # tee 未写盘
        assert not (tmp_path / "jobs_log" / "jobT2.log").exists()


def test_ws_proxy_tee_takes_over_after_flag_lost(tmp_path):
    """NAS 重启后 flag 丢失场景：无 B2 写者 → tee 接管独占写盘（角色接管模型）。"""
    msg1 = json.dumps({"ts": 1.0, "lines": ["[Info] taken-over"]})
    with FakeUpstream() as up:
        up.script = [msg1]
        app = _app_pointing_at(tmp_path, up.port)
        with TestClient(app) as c:
            assert not app.state.job_logs.is_recording("jobT3")
            with c.websocket_connect("/api/ws/logs/jobT3") as ws:
                assert up.connected.wait(timeout=5)
                time.sleep(0.3)
                ws.close()
                deadline = time.time() + 5
                while time.time() < deadline and app.state.job_logs.is_recording("jobT3"):
                    time.sleep(0.05)
            assert _read_lines(tmp_path, "jobT3") == ["[Info] taken-over"]
            # 断开已 release
            assert not app.state.job_logs.is_recording("jobT3")


def test_ws_proxy_tee_ignores_non_lines_frames(tmp_path):
    """tee try-parse：state/last 帧、非 JSON 帧静默跳过，不写盘。"""
    with FakeUpstream() as up:
        up.script = [
            json.dumps({"state": {"state": "running"}}),
            "not-json{{",
            json.dumps({"last": True, "state": {"state": "done"}}),
        ]
        app = _app_pointing_at(tmp_path, up.port)
        with TestClient(app) as c:
            with c.websocket_connect("/api/ws/logs/jobT4"):
                assert up.connected.wait(timeout=5)
                time.sleep(0.3)
        assert not (tmp_path / "jobs_log" / "jobT4.log").exists()


def test_ws_proxy_invalid_job_id_rejected(tmp_path):
    """WS 代理白名单（三处同用之三）：非法 job_id 发 {"error":"invalid job_id"} 后关闭。

    注：含 / 的 id（%2F）在路由匹配前就被 ASGI 层还原成分段，到不了本端点
    （直接 close）——非 / 的非法字符（空格/中文/..）才能验证端点内白名单。
    """
    with FakeUpstream() as up:
        app = _app_pointing_at(tmp_path, up.port)
        with TestClient(app) as c:
            for bad in ["a%20b", "%E4%BB%BB%E5%8A%A1", "job%7Bx%7D"]:
                with c.websocket_connect(f"/api/ws/logs/{bad}") as ws:
                    msg = json.loads(ws.receive_text())
                    assert msg["error"] == "invalid job_id"
        assert up.connections == 0  # 从未尝试连上游


# ============ 5. B2 录制器 ============

def test_b2_recorder_error_frame_stops_with_marker(tmp_path):
    """B2 收 {"error":...} 帧：写 [系统] 日志录制中断标记后停，finally 释放写者。"""
    with FakeUpstream() as up:
        up.script = [
            json.dumps({"ts": 1.0, "lines": ["x"]}),
            json.dumps({"error": "listener restarted"}),
        ]
        app = _app_pointing_at(tmp_path, up.port)
        store = app.state.job_logs
        # start_recorder 内部原子登记写者并投递线程（等价于请求路径的同步置位）
        app.state.start_recorder("jobB1")
        assert _wait_release(store, "jobB1"), "B2 线程应在 error 帧后退出"
        lines = _read_lines(tmp_path, "jobB1")
        assert lines[0] == "x"
        assert any("日志录制中断" in ln and "listener restarted" in ln for ln in lines), lines


def test_b2_recorder_initial_connect_retry_exhausted(tmp_path):
    """B2 初始连接重试耗尽（1s/2s/5s）：写失败标记后放弃，释放写者。"""
    import socket as _s
    s = _s.socket()
    s.bind(("127.0.0.1", 0))
    dead_port = s.getsockname()[1]
    s.close()
    app = _app_pointing_at(tmp_path, dead_port)
    store = app.state.job_logs
    t0 = time.time()
    app.state.start_recorder("jobB2")
    assert _wait_release(store, "jobB2", timeout=20)
    assert time.time() - t0 >= 7.0  # 1+2+5 退避至少走完
    lines = _read_lines(tmp_path, "jobB2")
    assert any("日志录制失败" in ln and "无法连接监听器" in ln for ln in lines), lines


def test_b2_recorder_records_until_last(tmp_path):
    """B2 正常路径：连上后收 lines 帧落盘，last 帧正常停，finally 释放写者。"""
    with FakeUpstream() as up:
        up.script = [
            json.dumps({"ts": 1.0, "lines": ["a", "b"]}),
            json.dumps({"last": True, "state": {"state": "done"}}),
        ]
        app = _app_pointing_at(tmp_path, up.port)
        store = app.state.job_logs
        app.state.start_recorder("jobB0")
        assert _wait_release(store, "jobB0", timeout=15)
        assert _read_lines(tmp_path, "jobB0") == ["a", "b"]


def test_trigger_request_starts_recorder_flag(tmp_path):
    """请求路径（评审必须项）：POST /api/trigger 成功后返回前同步置位写者标记。

    用假 client_factory 模拟 listener 触发成功；录制线程连不上 WS（port=1 无人听）
    → 走"初始重试耗尽"路径，最终落失败标记并释放（daemon 线程不泄漏）。
    """
    class FakeClient:
        def trigger(self, task_id):
            return {"job_id": "jobReq1", "task_id": task_id,
                    "display_name": "演示任务", "created_at": time.time()}

    cfg = {"default_target": {"ip": "127.0.0.1", "port": 1, "hostname": "PC"},
           "api_key": "k", "target_mac": ""}
    (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    app = create_app(
        config_path=str(tmp_path / "config.json"),
        history_path=str(tmp_path / "jobs.json"),
        logs_dir=str(tmp_path / "jobs_log"),
        reconcile_interval=0,
        client_factory=lambda url, key: FakeClient(),
    )
    with TestClient(app) as c:
        r = c.post("/api/trigger", json={"task_id": "t1"})
        assert r.status_code == 202
        assert r.json()["job_id"] == "jobReq1"
        store = app.state.job_logs
        # 评审必须项：202 返回时 flag 必已为 True（无竞态窗口）
        assert store.is_recording("jobReq1")
    # 等录制线程退避耗尽（1+2+5=8s）后落失败标记并释放
    assert _wait_release(store, "jobReq1", timeout=20)
    lines = _read_lines(tmp_path, "jobReq1")
    assert any("日志录制失败" in ln for ln in lines), lines


def test_b2_recorder_reconnects_after_drop(tmp_path):
    """B2 中途断开（未收 last/error）→ 退避重连恢复录制，恢复后写"接续"标记行。"""
    up = FakeUpstream()
    up.script = None  # 连接后不发消息挂住
    with up:
        app = _app_pointing_at(tmp_path, up.port)
        store = app.state.job_logs
        app.state.start_recorder("jobB3")

        # 等第一次连接建立
        deadline = time.time() + 10
        while time.time() < deadline and up.connections < 1:
            time.sleep(0.05)
        assert up.connections >= 1

        # 从服务端踢掉第一个连接 → 模拟网络抖动断开
        async def _kick():
            for ws in list(up._clients):
                await ws.close(code=1011)
        asyncio.run_coroutine_threadsafe(_kick(), up.loop).result(timeout=5)

        # 等第二次连接（重连成功）
        deadline = time.time() + 20
        while time.time() < deadline and up.connections < 2:
            time.sleep(0.05)
        assert up.connections >= 2, "B2 应在中途断开后重连"

        # 第二次连接建立后推一行，验证恢复录制
        time.sleep(0.3)

        async def _send():
            for ws in list(up._clients):
                await ws.send(json.dumps({"ts": 2.0, "lines": ["recovered-line"]}))
        asyncio.run_coroutine_threadsafe(_send(), up.loop).result(timeout=5)

        deadline = time.time() + 10
        while time.time() < deadline and "recovered-line" not in _read_lines(tmp_path, "jobB3"):
            time.sleep(0.05)
        # 测试收尾：发 last 让录制线程干净退出
        async def _last():
            for ws in list(up._clients):
                await ws.send(json.dumps({"last": True}))
        asyncio.run_coroutine_threadsafe(_last(), up.loop).result(timeout=5)
        assert _wait_release(store, "jobB3", timeout=15)

        lines = _read_lines(tmp_path, "jobB3")
        assert any("录制接续，可能重复最近缓冲" in ln for ln in lines), lines
        assert "recovered-line" in lines, lines


# ============ 6. spa_fallback 深链修复 ============

def test_spa_fallback_two_segment_route_serves_index(tmp_path):
    """/spa/logs/{id} 两段路由刷新 → 回退 index.html（深链可达，修复 latent bug）。"""
    app = _app_unpaired(tmp_path)
    with TestClient(app) as c:
        r = c.get("/spa/logs/abc-123")
        if r.status_code == 404:
            pytest.skip("SPA 产物未构建（纯后端 CI）")
        assert r.status_code == 200
        assert "assets/index-" in r.text  # 是 index.html 而非资产


def test_spa_fallback_asset_deep_path_still_404(tmp_path):
    """含 / 且末段带资产扩展名 → 仍 404（缺失资产要暴露，不回 HTML）。"""
    app = _app_unpaired(tmp_path)
    with TestClient(app) as c:
        assert c.get("/spa/assets/nonexistent.js").status_code == 404
        assert c.get("/spa/assets/nonexistent.css").status_code == 404


def test_spa_fallback_single_segment_route_serves_index(tmp_path):
    """既有行为回归：单段无扩展名路由回退 index.html。"""
    app = _app_unpaired(tmp_path)
    with TestClient(app) as c:
        r = c.get("/spa/history")
        if r.status_code == 404:
            pytest.skip("SPA 产物未构建（纯后端 CI）")
        assert r.status_code == 200
        assert "assets/index-" in r.text
