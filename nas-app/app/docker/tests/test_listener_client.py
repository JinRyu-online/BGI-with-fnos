import pytest

from listener_client import ListenerClient, ListenerError, ListenerAuthError, ListenerNotFound, TERMINAL_STATES


class _FakeResp:
    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}

    def json(self):
        return self._json


class _FakeTransport:
    """记录请求并按预设返回。"""

    def __init__(self, responses):
        self._responses = list(responses)  # [(method, path, status, json), ...]
        self.calls = []

    def __call__(self, method, url, headers=None, json=None, timeout=None):
        from urllib.parse import urlparse
        path = urlparse(url).path
        self.calls.append((method, path, headers, json))
        for i, (m, p, status, data) in enumerate(self._responses):
            if m == method and p == path:
                self._responses.pop(i)
                return _FakeResp(status, data)
        return _FakeResp(404, {})


def _client(transport):
    return ListenerClient("http://192.168.1.100:8765", api_key="secret", request=transport)


def test_health_returns_signature():
    t = _FakeTransport([("GET", "/health", 200, {"service": "bgi-trigger", "hostname": "H", "version": "1"})])
    c = _client(t)

    h = c.health()
    assert h["service"] == "bgi-trigger"


def test_tasks_returns_list():
    t = _FakeTransport([("GET", "/tasks", 200, [{"id": "daily"}])])
    c = _client(t)

    tasks = c.tasks()
    assert tasks == [{"id": "daily"}]
    # 携带鉴权头
    assert t.calls[0][2]["Authorization"] == "Bearer secret"


def test_trigger_returns_job_id():
    t = _FakeTransport([("POST", "/trigger", 202, {"job_id": "abc123"})])
    c = _client(t)

    result = c.trigger("daily")
    assert result["job_id"] == "abc123"
    # 请求体包含 task_id
    assert t.calls[0][3] == {"task_id": "daily"}


def test_status_returns_state():
    t = _FakeTransport([("GET", "/status", 200, {"state": "running", "id": "abc123"})])
    c = _client(t)

    st = c.status("abc123")
    assert st["state"] == "running"


def test_auth_error_on_401():
    t = _FakeTransport([("GET", "/tasks", 401, {})])
    c = _client(t)

    with pytest.raises(ListenerAuthError):
        c.tasks()


def test_busy_409_raises_listener_error():
    t = _FakeTransport([("POST", "/trigger", 409, {})])
    c = _client(t)

    with pytest.raises(ListenerError):
        c.trigger("daily")


def test_network_error_raises_listener_error():
    def boom(method, url, headers=None, json=None, timeout=None):
        raise OSError("connection refused")
    c = ListenerClient("http://1.2.3.4:8765", "k", request=boom)

    with pytest.raises(ListenerError):
        c.tasks()


def test_404_raises_listener_not_found():
    """404 → ListenerNotFound（ListenerError 子类，兼容现有 except 顺序）。"""
    t = _FakeTransport([("GET", "/status", 404, {})])
    c = _client(t)

    with pytest.raises(ListenerNotFound):
        c.status("ghost-job")


def test_listener_not_found_is_listener_error():
    """ListenerNotFound 必须是 ListenerError 子类：现有 `except ListenerError` 不破坏。"""
    assert issubclass(ListenerNotFound, ListenerError)


def test_401_takes_precedence_over_404():
    """401 优先于 404 判定：鉴权错误不会被误判为 NotFound。"""
    t = _FakeTransport([("GET", "/tasks", 401, {})])
    c = _client(t)

    with pytest.raises(ListenerAuthError):
        c.tasks()


def test_stop_posts_to_listener():
    """stop() → POST /stop，携带鉴权头，返回响应 JSON。"""
    t = _FakeTransport([("POST", "/stop", 200, {"stopped": True, "killed": ["bgi.exe"]})])
    c = _client(t)

    result = c.stop()
    assert result == {"stopped": True, "killed": ["bgi.exe"]}
    method, path, headers, _ = t.calls[0]
    assert (method, path) == ("POST", "/stop")
    assert headers["Authorization"] == "Bearer secret"


def test_terminal_states_align_with_windows_listener():
    """与 windows-listener bgi_trigger/core/state.py JobState 终态逐字对齐。

    历史事故："timeout" 拼错（实际 timed_out）+ 漏 abnormal_exit。
    """
    assert TERMINAL_STATES == frozenset(
        {"done", "abnormal_exit", "timed_out", "failed", "aborted"})
    assert "timeout" not in TERMINAL_STATES  # 拼错的名字绝不能再出现
