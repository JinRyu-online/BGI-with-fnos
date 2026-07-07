"""Windows 监听器 HTTP 客户端。

封装对 Windows 监听器五个接口的调用：health / tasks / trigger / status。
传输层（request 可调用对象）可注入，便于单元测试（用假传输替代真实 httpx）。

错误模型：
- ListenerAuthError：监听器返回 401（密钥不对）——提示用户重新配对。
- ListenerError：其他非 2xx 或网络异常——通用失败。
"""
from __future__ import annotations

from typing import Callable


class ListenerError(Exception):
    """与监听器通信失败（网络异常或非 2xx 非 401 响应）。"""


class ListenerAuthError(ListenerError):
    """鉴权失败（401）。"""


class ListenerClient:
    """Windows 监听器客户端。

    request 签名：(method, url, headers, json, timeout) -> resp
    resp 需有 .status_code 与 .json()。默认实现用 httpx.Client。
    """

    def __init__(self, base_url: str, api_key: str, request: Callable | None = None,
                 timeout: float = 5.0) -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._timeout = timeout
        if request is None:
            request = _default_request
        self._request = request

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._key}"}

    def _call(self, method: str, path: str, *, json=None, auth: bool = True) -> dict:
        headers = self._auth_headers() if auth else {}
        url = f"{self._base}{path}"
        try:
            resp = self._request(method, url, headers=headers, json=json, timeout=self._timeout)
        except Exception as e:
            raise ListenerError(f"network error: {e}") from e
        if resp.status_code == 401:
            raise ListenerAuthError("invalid api key")
        if not (200 <= resp.status_code < 300):
            raise ListenerError(f"listener returned {resp.status_code}")
        return resp.json()

    def health(self) -> dict:
        """GET /health（免鉴权），返回服务身份签名。"""
        return self._call("GET", "/health", auth=False)

    def tasks(self) -> list[dict]:
        """GET /tasks，返回任务清单。"""
        return self._call("GET", "/tasks")

    def trigger(self, task_id: str) -> dict:
        """POST /trigger，启动任务，返回 {"job_id": ...}。"""
        return self._call("POST", "/trigger", json={"task_id": task_id})

    def status(self, job_id: str) -> dict:
        """GET /status?job_id=，返回任务状态。"""
        return self._call("GET", f"/status?job_id={job_id}")


def _default_request(method, url, headers=None, json=None, timeout=None):
    """默认传输：用 httpx.Client 发起请求。"""
    import httpx
    with httpx.Client(timeout=timeout) as c:
        if method == "GET":
            return c.get(url, headers=headers)
        return c.request(method, url, headers=headers, json=json)
