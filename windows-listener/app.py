"""FastAPI 应用模块：装配监听器的五个 HTTP 接口。

依赖（配置、任务清单、鉴权、任务存储、启动回调）通过 AppDeps 注入，
使本模块可在不依赖真实 BetterGI 的情况下单元测试（用 TestClient + 假启动回调）。

接口概览：
  GET  /health   免鉴权，返回服务身份签名（供 NAS 扫描识别）
  GET  /tasks    鉴权，返回任务清单
  POST /trigger  鉴权，启动任务，返回 job_id（202）；忙时 409；未知任务 404
  GET  /status   鉴权，按 job_id 查任务状态
  POST /abort    鉴权，中止当前任务
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from auth import AuthError, AuthState
from state import Job, JobStore
from tasks import Task, TaskRegistry, TaskNotFound

SERVICE_NAME = "bgi-trigger"  # NAS 扫描时 /health 返回的服务标识，必须固定


@dataclass
class AppDeps:
    """应用依赖容器：所有外部依赖通过此对象注入。"""
    hostname: str
    version: str
    tasks: TaskRegistry
    auth: AuthState
    jobs: JobStore
    launch: Callable[[Job, Task], None]  # 启动回调：非阻塞，实际工作在守护线程


class TriggerBody(BaseModel):
    """POST /trigger 请求体。"""
    task_id: str


def create_app(deps: AppDeps) -> FastAPI:
    """创建并返回配置好路由的 FastAPI 应用。"""
    app = FastAPI(title="BetterGI Trigger Listener")

    def authenticate(request: Request, authorization: str | None = Header(default=None)) -> None:
        """鉴权依赖：从请求头取 Bearer token、从连接取来源 IP，交由 AuthState 校验。

        失败抛 401。注意 IP 自动学习在此发生（见 auth 模块）。
        """
        token = ""
        if authorization:
            token = authorization.removeprefix("Bearer ").strip()
        client_ip = request.client.host if request.client else ""
        try:
            deps.auth.verify(token, client_ip)
        except AuthError:
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/health")
    def health() -> dict:
        """免鉴权健康检查 + 身份签名。"""
        return {"service": SERVICE_NAME, "hostname": deps.hostname, "version": deps.version}

    @app.get("/tasks")
    def list_tasks(request: Request, authorization: str | None = Header(default=None)) -> list[dict]:
        """返回全部任务（供 NAS 渲染触发按钮）。"""
        authenticate(request, authorization)
        return [t.to_dict() for t in deps.tasks.all()]

    @app.post("/trigger", status_code=202)
    def trigger(body: TriggerBody, request: Request,
                authorization: str | None = Header(default=None)) -> dict:
        """启动任务：校验 → 查任务 → 占槽 → 调启动回调 → 返回 job_id。

        未知任务 404；槽位忙 409；成功 202。
        """
        authenticate(request, authorization)
        try:
            task = deps.tasks.get(body.task_id)
        except TaskNotFound:
            raise HTTPException(status_code=404, detail=f"task not found: {body.task_id}")
        try:
            job = deps.jobs.start(task.id, task.groups)
        except Exception:
            raise HTTPException(status_code=409, detail="a job is already running")
        deps.launch(job, task)  # 非阻塞：守护线程内执行
        return {"job_id": job.id}

    @app.get("/status")
    def status(job_id: str, request: Request,
               authorization: str | None = Header(default=None)) -> dict:
        """按 job_id 查任务状态（当前或历史）。找不到 404。"""
        authenticate(request, authorization)
        job = deps.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job.to_dict()

    @app.post("/abort")
    def abort(request: Request, authorization: str | None = Header(default=None)) -> dict:
        """中止当前任务。无活动任务时 409。"""
        authenticate(request, authorization)
        if deps.jobs.current is None:
            raise HTTPException(status_code=409, detail="no active job")
        deps.jobs.abort()
        return {"aborted": True}

    return app
