"""WS 日志代理 /api/ws/logs/{job_id} 测试。

用 websockets.serve 在本机随机端口起一个假上游监听器 WS 服务，
配置 default_target 指向它，断言 NAS 代理把上游消息原样透传给浏览器客户端。
"""
import asyncio
import json
import threading
import time

import pytest
import websockets
from fastapi.testclient import TestClient

from main import create_app

# websockets 14+ 把服务端实现挪到了 websockets.asyncio.server
try:
    from websockets.asyncio.server import serve as ws_serve
except ImportError:  # 旧版本兜底
    from websockets.serve import ws_serve  # type: ignore


class FakeUpstream:
    """假监听器 WS 服务：接受连接后按剧本发消息，可记录收到的消息。"""

    def __init__(self):
        self.received = []          # 上游收到的客户端消息
        self.script = []            # 连接后要发送的消息（JSON 文本）
        self.server = None
        self.loop = None
        self.thread = None
        self.port = None
        self.connected = threading.Event()
        self._clients = set()

    def start(self):
        ready = threading.Event()

        async def handler(ws):
            self.connected.set()
            self._clients.add(ws)
            try:
                for msg in self.script:
                    await ws.send(msg)
                # 读客户端消息（若有；连接正常关闭时迭代结束）
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
            # 等待 server.close() 完成后干净退出（stop() 触发），避免悬空任务
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
        # websockets 15 的 Server.close() 是同步方法（内部调度 _close 任务），
        # 直接在事件循环线程调用即可；wait_closed 等 close_task 落幕。
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


def _app_pointing_at(tmp_path, port, *, api_key="k"):
    cfg = {"default_target": {"ip": "127.0.0.1", "port": port, "hostname": "PC"},
           "api_key": api_key, "target_mac": ""}
    (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return create_app(
        config_path=str(tmp_path / "config.json"),
        history_path=str(tmp_path / "jobs.json"),
        reconcile_interval=0,
    )


def test_ws_proxy_forwards_upstream_messages(tmp_path):
    """上游发的 2 条 JSON 消息必须原样透传到浏览器客户端。"""
    msg1 = json.dumps({"ts": 111.0, "lines": ["[Info] started", "[Info] mining"]})
    msg2 = json.dumps({"last": True, "state": "done"})

    with FakeUpstream() as up:
        up.script = [msg1, msg2]
        app = _app_pointing_at(tmp_path, up.port)
        with TestClient(app) as c:
            with c.websocket_connect("/api/ws/logs/jobXYZ") as client_ws:
                got1 = client_ws.receive_text()
                got2 = client_ws.receive_text()
        assert got1 == msg1
        assert got2 == msg2
        assert json.loads(got2)["state"] == "done"


def test_ws_proxy_unpaired_sends_error_then_closes(tmp_path):
    """未配对：accept 后发 {"error":"unpaired"} 并关闭。"""
    app = create_app(
        config_path=str(tmp_path / "config.json"),
        history_path=str(tmp_path / "jobs.json"),
        reconcile_interval=0,
    )
    with TestClient(app) as c:
        with c.websocket_connect("/api/ws/logs/jobXYZ") as ws:
            msg = json.loads(ws.receive_text())
            assert msg["error"] == "unpaired"
            # 服务器已关闭连接：再收应得到 WebSocketDisconnect
            with pytest.raises(Exception):
                ws.receive_text()


def test_ws_proxy_listener_unreachable_sends_error_then_closes(tmp_path):
    """上游连接失败：发 {"error":"listener unreachable"} 后关闭。"""
    # 占一个端口再释放 → 该端口当前确定无人监听
    import socket as _s
    s = _s.socket()
    s.bind(("127.0.0.1", 0))
    dead_port = s.getsockname()[1]
    s.close()

    app = _app_pointing_at(tmp_path, dead_port)
    with TestClient(app) as c:
        with c.websocket_connect("/api/ws/logs/jobXYZ") as ws:
            msg = json.loads(ws.receive_text())
            assert msg["error"] == "listener unreachable"
            with pytest.raises(Exception):
                ws.receive_text()


def test_ws_proxy_client_message_reaches_upstream(tmp_path):
    """反向：客户端发的文本也能到上游（薄双向桥）。"""
    with FakeUpstream() as up:
        up.script = []
        app = _app_pointing_at(tmp_path, up.port)
        with TestClient(app) as c:
            with c.websocket_connect("/api/ws/logs/jobXYZ") as ws:
                assert up.connected.wait(timeout=5)
                ws.send_text("ping")
                deadline = time.time() + 5
                while time.time() < deadline and not up.received:
                    time.sleep(0.02)
        assert "ping" in up.received
