"""Windows 监听器 HTTP 客户端。

封装对 Windows 监听器各接口的调用：health / tasks / trigger / status / abort / stop。
传输层（request 可调用对象）可注入，便于单元测试（用假传输替代真实 httpx）。

错误模型：
- ListenerAuthError：监听器返回 401（密钥不对）——提示用户重新配对。
- ListenerNotFound：监听器返回 404（job_id 不存在）——对账循环据此把历史标记为 unknown。
- ListenerError：其他非 2xx 或网络异常——通用失败。

状态机常量（TERMINAL_STATES / ACTIVE_STATES）与 windows-listener
bgi_trigger/core/state.py 的 JobState 集合逐字对齐，修改一端必须同步另一端。
"""
from __future__ import annotations

from typing import Callable

# 与 windows-listener bgi_trigger/core/state.py 的 JobState 终态集合逐字对齐。
# 修改一端必须同步另一端（历史上 "timeout" 拼错 + 漏 abnormal_exit 导致历史卡 running）。
TERMINAL_STATES = frozenset({"done", "abnormal_exit", "timed_out", "failed", "aborted"})

# 仍需跟踪（前端轮询 / 后台对账）的活动态集合；"unknown" 等不在其中即停止跟踪。
ACTIVE_STATES = frozenset({"running", "completing"})


class ListenerError(Exception):
    """与监听器通信失败（网络异常或非 2xx 非 401 响应）。"""


class ListenerAuthError(ListenerError):
    """鉴权失败（401）。"""


class ListenerNotFound(ListenerError):
    """资源不存在（404，如 job_id 未知）。"""


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
        if resp.status_code == 404:
            raise ListenerNotFound(f"not found: {path}")
        if not (200 <= resp.status_code < 300):
            raise ListenerError(f"listener returned {resp.status_code}")
        return resp.json()

    def health(self) -> dict:
        """GET /health（免鉴权），返回服务身份签名。"""
        return self._call("GET", "/health", auth=False)

    def tasks(self) -> list[dict]:
        """GET /tasks，返回任务清单。"""
        return self._call("GET", "/tasks")

    def bgi_groups(self) -> list[str]:
        """GET /bgi/groups，返回 BetterGI 调度器已有组名列表。

        旧版监听器（未升级）返回 404 → 映射 ListenerNotFound（代理层
        据此兼容回退为空列表，前端自然走手写 textarea）。
        """
        data = self._call("GET", "/bgi/groups")
        groups = data.get("groups", [])
        return [str(g) for g in groups]

    def replace_tasks(self, tasks: list[dict]) -> list[dict]:
        """PUT /tasks，整体替换任务清单（Windows 端写回 tasks 文件）。

        任一任务非法 → Windows 400 → 映射 ListenerError（消息含 400）。
        返回替换后的完整清单（可能含 Windows 手写文件里的任务）。
        """
        return self._call("PUT", "/tasks", json={"tasks": tasks})

    def trigger(self, task_id: str) -> dict:
        """POST /trigger，启动任务，返回 {"job_id": ...}。"""
        return self._call("POST", "/trigger", json={"task_id": task_id})

    def status(self, job_id: str) -> dict:
        """GET /status?job_id=，返回任务状态。"""
        return self._call("GET", f"/status?job_id={job_id}")

    def abort(self) -> dict:
        """POST /abort，中止当前任务（仅在 completing 反悔窗口内有效，无活动任务 409）。"""
        return self._call("POST", "/abort")

    def stop(self) -> dict:
        """POST /stop，强制停止当前任务（杀进程级），返回 {"stopped": True, "killed": [...]}。"""
        return self._call("POST", "/stop")


def _default_request(method, url, headers=None, json=None, timeout=None):
    """默认传输：用 httpx.Client 发起请求。"""
    import httpx
    with httpx.Client(timeout=timeout) as c:
        if method == "GET":
            return c.get(url, headers=headers)
        return c.request(method, url, headers=headers, json=json)
