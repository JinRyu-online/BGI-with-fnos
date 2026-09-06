"""FastAPI 应用模块：装配监听器的 HTTP 接口。

依赖（配置、任务清单、鉴权、任务存储、启动回调）通过 AppDeps 注入，
使本模块可在不依赖真实 BetterGI 的情况下单元测试（用 TestClient + 假启动回调）。

接口概览：
  GET  /health     免鉴权，返回服务身份签名（供 NAS 扫描识别）
  GET  /key        免鉴权，返回 {api_key, hostname}（供 NAS 自动配对）
  GET  /tasks      鉴权，返回任务清单
  POST /trigger    鉴权，启动任务，返回 job_id（202）；忙时 409；未知任务 404
  GET  /status     鉴权，按 job_id 查任务状态
  POST /abort      鉴权，中止当前任务（含主动终止 BetterGI 进程）
  POST /stop       鉴权，急停：清理所有 BetterGI/游戏进程 + abort 活动任务
                   （"卡死后自救"入口：无活动 job 时也照常清理残留进程）
  GET  /bgi/groups 鉴权，枚举 BetterGI「全自动-调度器」已有组名（供 NAS 编排任务）
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field as dataclass_field
from typing import Callable

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from bgi_trigger.service.auth import AuthError, AuthState
from bgi_trigger.core.state import Job, JobState, JobStore
from bgi_trigger.core.tasks import Task, TaskRegistry, TaskNotFound
from bgi_trigger.core.launcher import get_harvester

# ★ 404 频率抑制:同一 job_id 首次 + 每 5 分钟最多提醒一次。
# NAS 轮询一个已归档的旧 job_id 是正常时序现象(任务完成 → 清收割器 → 历史超 20 条截掉)，
# 若每 10 秒轮询都打 WARNING 会刷爆日志。用一个 dict 记"最近一次告警时间"来节流。
_status_404_log_state: dict[str, float] = {}   # job_id → 上次告警时间(monotonic)
_STATUS_404_SUPPRESS_SEC = 300.0               # 同一 job_id 5 分钟内不重复告警

SERVICE_NAME = "bgi-trigger"  # NAS 扫描时 /health 返回的服务标识，必须固定
log = logging.getLogger("bgi_trigger.app")

# ★ WS 单次推送的日志行数上限；超出部分下一轮循环继续推。
_WS_MAX_LINES_PER_PUSH = 50


@dataclass
class AppDeps:
    """应用依赖容器：所有外部依赖通过此对象注入。"""
    hostname: str
    version: str
    tasks: TaskRegistry
    auth: AuthState
    jobs: JobStore
    launch: Callable[[Job, Task], None]  # 启动回调：非阻塞，实际工作在守护线程
    log_path: str = ""  # BetterGI 日志路径（空=不收割，WS 无日志流）

    # ★ /stop 与 /abort(abort_kills_game=True 时) 的进程清理回调：
    #   函数签名 kill_processes(names) -> list[str]（被终止的进程名列表）。
    #   None 时退化为 no-op（测试/无 psutil 场景）。
    kill_processes: Callable[[list[str]], list[str]] | None = None

    # ★ /abort 时是否同时终止游戏进程（对应配置 [execution] abort_kills_game，
    #   默认 True）。注入为标量便于测试；listener 从 config 传入。
    abort_kills_game: bool = True

    # ★ /abort 主动终止"本 job 拉起的 BetterGI 子进程"的回调（terminate → wait →
    #   kill）。None 时跳过（测试场景）。
    terminate_current_proc: Callable[[], None] | None = None

    # BetterGI exe basename（/stop 匹配用；空则只匹配 game_processes）
    bettergi_name: str = ""

    # 完成判定 B 监视的游戏进程名（/stop 与 /abort 的进程清理匹配用）
    game_processes: list[str] = dataclass_field(default_factory=list)

    # ★ GET /bgi/groups 的组名枚举回调（DI——端点层绝不读 config.toml）：
    #   默认实现由 listener.py 装配（bettergi.dir → <dir>/User/ScriptGroup/*.json
    #   的文件名 stem 排序）。None 时端点返回 {"groups": []}（测试/未配置场景）。
    bgi_groups_reader: Callable[[], list[str]] | None = None

    @property
    def api_key(self) -> str:
        """配对密钥（供 /key 接口暴露）。见 auth.api_key。"""
        return self.auth.api_key

    def stop_target_names(self) -> list[str]:
        """/stop 应终止的进程名集合：BetterGI exe basename + game_processes。

        bettergi_name 为空时仅用 game_processes。
        """
        names: list[str] = []
        if self.bettergi_name:
            names.append(self.bettergi_name)
        names.extend(self.game_processes)
        return names

    def launch_game_process_names(self) -> list[str]:
        """/abort(abort_kills_game=True) 应终止的游戏进程名集合。"""
        return list(self.game_processes)


class TriggerBody(BaseModel):
    """POST /trigger 请求体。"""
    task_id: str


class TaskBody(BaseModel):
    """PUT /tasks 单个任务定义（校验规则同 TaskRegistry._build）。"""
    id: str
    display_name: str = ""
    groups: list[str]
    timeout_min: int = 90
    after_done: str = "sleep"


class TaskListBody(BaseModel):
    """PUT /tasks 请求体：整体替换清单。"""
    tasks: list[TaskBody]


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
    def health(request: Request) -> dict:
        """免鉴权健康检查 + 身份签名。"""
        client_ip = request.client.host if request.client else "?"
        log.info("/health probe from %s", client_ip)
        return {"service": SERVICE_NAME, "hostname": deps.hostname, "version": deps.version}

    @app.get("/key")
    def key() -> dict:
        """免鉴权返回配对密钥（api_key）。

        MVP / 内网可信场景下由 NAS 应用拉取后填充配对表单，避免用户手敲密钥。
        """
        return {"api_key": deps.api_key, "hostname": deps.hostname}

    @app.get("/tasks")
    def list_tasks(request: Request, authorization: str | None = Header(default=None)) -> list[dict]:
        """返回全部任务（供 NAS 渲染触发按钮）。"""
        client_ip = request.client.host if request.client else "?"
        authenticate(request, authorization)
        tasks = [t.to_dict() for t in deps.tasks.all()]
        log.info("tasks listed for %s (%d tasks)", client_ip, len(tasks))
        return tasks

    @app.put("/tasks")
    def replace_tasks(request: Request, body: TaskListBody,
                      authorization: str | None = Header(default=None)) -> list[dict]:
        """整体替换任务清单（NAS GUI 编辑用）：全量校验后写回 tasks 文件/目录。

        任一任务非法 → 400（不落盘）；成功返回替换后的完整清单（目录模式含
        手写 *.json 里的任务，同 id 时手写文件覆盖 GUI 版本）。写回后 mtime
        热加载自动生效，无需重启监听器。
        """
        client_ip = request.client.host if request.client else "?"
        authenticate(request, authorization)
        try:
            tasks = deps.tasks.save_all([t.model_dump() for t in body.tasks])
        except ValueError as e:
            log.warning("tasks replace rejected from %s: %s", client_ip, e)
            raise HTTPException(status_code=400, detail=str(e))
        log.info("tasks replaced by %s (%d tasks)", client_ip, len(tasks))
        return [t.to_dict() for t in tasks]

    @app.get("/bgi/groups")
    def bgi_groups(request: Request, authorization: str | None = Header(default=None)) -> dict:
        """枚举 BetterGI「全自动-调度器」已有组名（供 NAS 编排任务时勾选）。

        组名来源由 deps.bgi_groups_reader 注入（listener.py 装配：
        bettergi.dir → <dir>/User/ScriptGroup/*.json 的文件名 stem 排序）。
        未注入（None）或读取异常 → {"groups": []}，不阻塞调用方。
        """
        client_ip = request.client.host if request.client else "?"
        authenticate(request, authorization)
        groups: list[str] = []
        if deps.bgi_groups_reader is not None:
            try:
                groups = list(deps.bgi_groups_reader())
            except Exception:
                log.exception("bgi/groups: groups reader failed (from %s)", client_ip)
                groups = []
        log.debug("bgi/groups listed for %s (%d groups)", client_ip, len(groups))
        return {"groups": groups}

    @app.post("/trigger", status_code=202)
    def trigger(body: TriggerBody, request: Request,
                authorization: str | None = Header(default=None)) -> dict:
        """启动任务：校验 → 查任务 → 占槽 → 调启动回调 → 返回 job_id。

        未知任务 404；槽位忙 409；成功 202。
        """
        client_ip = request.client.host if request.client else "?"
        authenticate(request, authorization)
        log.info("trigger request from %s: task_id=%s", client_ip, body.task_id)
        try:
            task = deps.tasks.get(body.task_id)
        except TaskNotFound:
            log.warning("trigger failed: unknown task_id=%s from %s", body.task_id, client_ip)
            raise HTTPException(status_code=404, detail=f"task not found: {body.task_id}")
        try:
            job = deps.jobs.start(task.id, task.groups)
        except Exception:
            log.warning("trigger refused: slot busy for task_id=%s from %s", body.task_id, client_ip)
            raise HTTPException(status_code=409, detail="a job is already running")
        # ★ 注入 BetterGI 日志路径:有日志才启动收割器供 WS 推流
        job.log_path = deps.log_path
        log.info("job %s log_path=%s", job.id, job.log_path or "(none)")
        deps.launch(job, task)  # 非阻塞：守护线程内执行
        log.info("trigger accepted: job=%s task=%s groups=%s", job.id, task.id, task.groups)
        # display_name 一并返回，供 NAS 历史显示任务可读名称（“挖矿” 而不是 “miner”）
        return {"job_id": job.id, "task_id": task.id,
                "display_name": task.display_name or task.id}

    @app.get("/status")
    def status(job_id: str, request: Request,
               authorization: str | None = Header(default=None)) -> dict:
        """按 job_id 查任务状态（当前或历史）。找不到 404。"""
        client_ip = request.client.host if request.client else "?"
        authenticate(request, authorization)
        job = deps.jobs.get(job_id)
        if job is None:
            # ★ 频率抑制：仅首次 + 每 5 分钟打一条 INFO（不是 WARNING），
            # 避免 NAS 轮询已归档的旧 job_id 每 10 秒刷一次 WARNING。
            now = time.monotonic()
            last = _status_404_log_state.get(job_id, -1.0)
            if now - last >= _STATUS_404_SUPPRESS_SEC:
                _status_404_log_state[job_id] = now
                # ★ 简易 GC:顺手清掉已超期的条目,避免 dict 无限增长。
                expired = [k for k, v in _status_404_log_state.items()
                           if now - v > _STATUS_404_SUPPRESS_SEC]
                for k in expired:
                    del _status_404_log_state[k]
                log.info("status: job_id=%s not found (from %s); "
                         "suppressing repeats for %.0fs",
                         job_id, client_ip, _STATUS_404_SUPPRESS_SEC)
            else:
                log.debug("status: job_id=%s not found (from %s)", job_id, client_ip)
            raise HTTPException(status_code=404, detail="job not found")
        log.debug("status polled: job=%s state=%s from %s", job_id, job.state.value, client_ip)
        return job.to_dict()

    @app.post("/abort")
    def abort(request: Request, authorization: str | None = Header(default=None)) -> dict:
        """中止当前任务。无活动任务 409。

        活动任务收到 abort：
          1. 置 abort 信号（现有机制，launcher 线程感知后尽快退出并跳过 after_done）；
          2. 主动终止本 job 拉起的 BetterGI 进程（terminate → wait(5) → kill）；
          3. abort_kills_game=True 时同时终止游戏进程（kill_processes）。

        已终态任务（is_idle()==True 或 current 为 None）→ 409 "no active job"：
        终态 job 不再归档（修复旧版重复 append 历史的 bug）。
        """
        client_ip = request.client.host if request.client else "?"
        authenticate(request, authorization)
        current = deps.jobs.current
        # ★ 语义修正:is_idle() 为 True(无 current 或 current 已终态)一律 409,
        #   不再对终态 job 重复 abort/归档。
        if current is None or deps.jobs.is_idle():
            log.warning("abort refused: no active job (from %s)", client_ip)
            raise HTTPException(status_code=409, detail="no active job")
        deps.jobs.abort()
        log.info("abort accepted: job=%s (from %s)", current.id, client_ip)
        # ★ 主动终止本 job 拉起的 BetterGI 子进程（terminate → wait(5) → kill）
        if deps.terminate_current_proc is not None:
            try:
                deps.terminate_current_proc()
            except Exception:
                log.exception("abort: failed to terminate BetterGI proc")
        # ★ abort_kills_game=True 时同时终止游戏进程
        if deps.abort_kills_game and deps.kill_processes is not None:
            killed = _safe_kill(deps, deps.launch_game_process_names())
            if killed:
                log.info("abort: killed game processes %s", killed)
        return {"aborted": True}

    @app.post("/stop")
    def stop(request: Request, authorization: str | None = Header(default=None)) -> dict:
        """急停端点（"卡死后自救"入口）。

        行为：
          1. 用 kill_processes 终止所有名字匹配 BetterGI exe basename 或
             game_processes 的进程（terminate → 最多等 5s → kill → 确认）；
          2. 若有活动 job（is_idle()==False）：置 abort 信号，并在 current 仍处于
             running/completing 态时 finalize(JobState.ABORTED)；
          3. 无活动 job 时也照常清理残留进程。

        返回 {"stopped": true, "killed": [被终止的进程名列表]}。
        """
        client_ip = request.client.host if request.client else "?"
        authenticate(request, authorization)
        log.warning("stop requested from %s", client_ip)
        # 1. 清理 BetterGI + 游戏进程（先杀进程再动状态，避免状态先变但进程残留）
        killed = _safe_kill(deps, deps.stop_target_names())
        # 2. 活动 job → abort 信号 + finalize ABORTED（仅 running/completing 态）
        current = deps.jobs.current
        if current is not None and not deps.jobs.is_idle():
            deps.jobs.set_abort_signal()  # 置 abort 信号（幂等，launcher 线程感知后退出）
            if current.state in (JobState.RUNNING, JobState.COMPLETING):
                deps.jobs.finalize(JobState.ABORTED)
            log.info("stop: job %s aborted (state=%s)", current.id, current.state.value)
        return {"stopped": True, "killed": killed}

    # ★★★ WebSocket 端点:浏览器直接连此端点获取实时日志 ★★★
    @app.websocket("/ws/logs/{job_id}")
    async def ws_logs(websocket: WebSocket, job_id: str) -> None:
        """实时推送 BetterGI 日志给前端。

        协议:
          → 客户端连接即推送最近 3 条历史 + 最新任务状态
          → 之后每批新日志以 {"ts","lines":[...]} 推送（burst 修复：
            本地维护 sent_seq，用 harvester.since(sent_seq) 增量取行，
            单次最多推 50 行，超出部分下一轮继续推，不再丢行）
          → 任务结束（mark_finished）后主循环退出前再 flush 一次 since，
            确保最后几行不丢，最后推一条 {"last":True, "state":"done/..."} 后关闭
        安全: 无需鉴权(MVP 内网,同 NAS 当前模型);IP 取自 X-Forwarded-For 或 client。
        """
        await websocket.accept()
        harvester = get_harvester(job_id)
        if harvester is None:
            await websocket.send_json({"error": f"no running job: {job_id}"})
            await websocket.close(code=1008)
            return

        # 注册 asyncio event loop(异步桥的关键)
        harvester.attach_loop(asyncio.get_running_loop())

        # 推送最近 3 条
        recent = harvester.recent(3)
        if recent:
            await websocket.send_json({"ts": time.time(), "lines": recent})

        # 状态快照
        try:
            job_snapshot = deps.jobs.get(job_id)
            if job_snapshot:
                await websocket.send_json({"state": job_snapshot.to_dict()})
        except Exception:
            pass

        # ★ 主循环: 等新行 or 收割器退出 or 客户端断开。
        # ★ burst 丢行修复:不再用 recent(1)（burst 多行只推最后一条），
        #   改为本地 sent_seq + since() 增量推送，且 mark_finished 后退出前
        #   再 flush 一次，确保最后一波行不丢。
        sent_seq = 0
        try:
            while True:
                new = await harvester.wait_new(timeout=2.0)
                if new:
                    lines, sent_seq = harvester.since(sent_seq)
                    # 单次最多推 _WS_MAX_LINES_PER_PUSH 行,超出部分下轮继续推
                    while lines:
                        batch, lines = lines[:_WS_MAX_LINES_PER_PUSH], lines[_WS_MAX_LINES_PER_PUSH:]
                        await websocket.send_json({"ts": time.time(), "lines": batch})
                # 退出条件
                if harvester.finished:
                    break
        except WebSocketDisconnect:
            log.info("ws/logs/%s: client disconnected", job_id)
        except Exception:
            log.exception("ws/logs/%s error", job_id)
        finally:
            # ★ 最后 flush:mark_finished 后收割线程可能又收割了最后几行,
            #   退出前把 since(sent_seq) 的剩余行全部推完。
            try:
                lines, sent_seq = harvester.since(sent_seq)
                while lines:
                    batch, lines = lines[:_WS_MAX_LINES_PER_PUSH], lines[_WS_MAX_LINES_PER_PUSH:]
                    await websocket.send_json({"ts": time.time(), "lines": batch})
            except Exception:
                pass
            # ★ 终止帧:任务已结束时先推 {"last":True, "state":...} 再关闭,
            #   客户端据此干净收尾(不再依赖轮询终态兜底)。与 docstring 协议一致。
            try:
                if harvester.finished:
                    final_job = deps.jobs.get(job_id)
                    await websocket.send_json({
                        "last": True,
                        "state": final_job.to_dict() if final_job else {"state": "unknown"},
                    })
            except Exception:
                pass
            # 非终态断开(客户端主动断)也补一次状态快照,便于前端恢复现场
            try:
                if not harvester.finished:
                    await websocket.send_json({"state": (deps.jobs.get(job_id) or {}).to_dict()
                                               if deps.jobs.get(job_id) else {"state": "unknown"}})
            except Exception:
                pass
            await websocket.close()

    return app


def _safe_kill(deps: AppDeps, names: list[str]) -> list[str]:
    """调用注入的 kill_processes，失败（含未注入）返回 []，不让 /stop /abort 500。"""
    if deps.kill_processes is None or not names:
        return []
    try:
        return list(deps.kill_processes(names))
    except Exception:
        log.exception("kill_processes failed for %s", names)
        return []
