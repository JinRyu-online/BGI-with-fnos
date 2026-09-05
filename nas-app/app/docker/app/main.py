"""NAS 应用 FastAPI 主模块。

提供 Web GUI（Jinja2 模板）与一组 JSON API：
  设备发现与配对（M3）：/api/scan  /api/pair  /api/unpair  /api/config
  任务触发与回报（M4）：/api/tasks  /api/trigger  /api/status  /api/jobs
  任务控制与运维：      /api/abort  /api/stop  /api/wol  /api/ws/logs/{job_id}

应用通过 create_app(...) 工厂构造，扫描器、监听器客户端工厂、历史路径均可注入，
便于单元测试（用假扫描器/假客户端替代真实网络）。

扫描进度通过 /api/scan-progress 轮询获取,前端实时展示每个子网的状态(pending/scanning/done)
与整体进度条。线程安全的进度状态保存在 _scan_progress 单例中,每完成一个子网就更新一次。

后台对账（reconcile）：lifespan 启动 daemon 线程，每 reconcile_interval 秒扫一遍
history 中 running/completing 的记录并向监听器查询状态，终态落盘——修复"浏览器一关
历史就永远卡 running"的问题（历史上还叠加过 "timeout" 拼错/漏 abnormal_exit 的事故）。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from discovery import auto_discover_and_scan, list_local_subnets, COMMON_SUBNETS
from history import HistoryStore, default_history_path
from listener_client import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    ListenerAuthError,
    ListenerClient,
    ListenerError,
    ListenerNotFound,
)
from settings import Settings

log = logging.getLogger("bgi_trigger.main")

# ---------- 子网级扫描进度（模块级，线程安全） ----------
_scan_lock = threading.Lock()
_scan_progress: dict = {
    "active": False,        # 扫描是否进行中
    "stage": "idle",        # idle / preparing / scanning / done / error
    "subnets": [],          # [{subnet, status: pending|scanning|done, found: int}]
    "scanned": 0,           # 已完成子网数
    "total": 0,             # 总子网数
    "devices": [],          # 已发现的设备
    "error": None,          # 错误信息
    "t0": 0.0,              # 开始时间戳
    "elapsed": 0.0,         # 耗时(秒)
}


def _progress_reset(plan: list[str]) -> None:
    """重置进度状态，plan 是所有待扫描的子网列表。"""
    now = time.time()
    with _scan_lock:
        _scan_progress.update({
            "active": True, "stage": "scanning",
            "subnets": [{"subnet": s, "status": "pending", "found": 0} for s in plan],
            "scanned": 0, "total": len(plan), "devices": [],
            "error": None, "t0": now, "elapsed": 0,
        })


def _progress_mark_subnet(subnet: str, found: list[dict]) -> None:
    """标记一个子网扫描完成。found 是该子网新发现的设备。"""
    with _scan_lock:
        for entry in _scan_progress["subnets"]:
            if entry["subnet"] == subnet:
                entry["status"] = "done"
                entry["found"] = len(found)
                break
        for d in found:
            if not any(x["ip"] == d["ip"] for x in _scan_progress["devices"]):
                _scan_progress["devices"].append(d)
        _scan_progress["scanned"] += 1
        _scan_progress["elapsed"] = time.time() - _scan_progress["t0"]


def _progress_snapshot() -> dict:
    """返回进度快照（线程安全拷贝）。"""
    with _scan_lock:
        snap = dict(_scan_progress)
        snap["subnets"] = list(_scan_progress["subnets"])
        snap["devices"] = list(_scan_progress["devices"])
        snap["elapsed"] = time.time() - _scan_progress["t0"] if _scan_progress["active"] else _scan_progress["elapsed"]
        return snap


def _progress_finish(devices: list[dict] | None = None, err: str | None = None) -> None:
    with _scan_lock:
        _scan_progress["active"] = False
        _scan_progress["stage"] = "error" if err else "done"
        _scan_progress["error"] = err
        _scan_progress["devices"] = devices or _scan_progress["devices"]
        if _scan_progress["subnets"]:
            for entry in _scan_progress["subnets"]:
                if entry["status"] == "pending":
                    entry["status"] = "skipped"
        _scan_progress["elapsed"] = time.time() - _scan_progress["t0"]


def _http_get_json(ip: str, port: int, path: str = "/", timeout: float = 3.0) -> dict | None:
    """内网 HTTP GET 取 JSON，供后端代理接口使用。失败返回 None。"""
    import httpx
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.get(f"http://{ip}:{port}{path}")
            if r.status_code == 200:
                return r.json()
    except Exception:
        return None
    return None

_STATIC_DIR = Path(__file__).resolve().parent / "static"


class _Unpaired(Exception):
    """未配对设备。"""


# 请求体模型（必须在模块级定义，配合 `from __future__ import annotations`
# 时 FastAPI 才能正确解析类型注解为请求体而非查询参数）。
class ScanBody(BaseModel):
    subnet: str | None = None
    port: int | None = None
    sync: bool = False
    """True 时同步返回 {devices},False 时启动异步扫描任务。"""


class PairBody(BaseModel):
    ip: str
    port: int
    hostname: str = ""
    api_key: str


class TriggerBody(BaseModel):
    task_id: str


class StopBody(BaseModel):
    """POST /api/stop 请求体（保留扩展位，当前无需字段）。"""


class WolBody(BaseModel):
    mac: str | None = None
    """为空时回退到配置 target_mac；两者都空返回 400。"""


class SchedulesBody(BaseModel):
    """PUT /api/schedules 请求体：整体替换列表（与 config.schedules 同构）。

    必须定义在模块级：main.py 启用 `from __future__ import annotations`，
    注解是字符串，FastAPI 解析时在模块命名空间找模型——闭包内定义的类
    会被当成 query 参数（真实踩坑：PUT 422 "query body missing"）。
    """
    schedules: list[dict]


class TaskItemBody(BaseModel):
    """PUT /api/tasks 单个任务（字段与 Windows 端 TaskBody 对齐）。"""
    id: str
    display_name: str = ""
    groups: list[str]
    timeout_min: int = 90
    after_done: str = "sleep"


class TasksBody(BaseModel):
    """PUT /api/tasks 请求体：整体替换任务清单。"""
    tasks: list[TaskItemBody]


def _default_config_path() -> str:
    """配置文件路径：容器内由 compose 注入 BGI_DATA_DIR=/data；
    开发环境回退到源码旁的 etc/config.json。"""
    return Settings.default_path()


def _default_scanner(subnet: str | None, port: int, *, progress_cb: Callable[[str, list[dict]], None] | None = None) -> list[dict]:
    """按子网粒度扫描，通过 progress_cb 每完成一个子网回调进度。

    扫描逻辑统一收敛到 discovery.auto_discover_and_scan,本函数只做
    progress_cb → on_subnet_done 的适配,避免两处发散。
    """
    return auto_discover_and_scan(
        port=port, subnet=subnet, common_fallback=True, stop_on_first_hit=True,
        max_workers=128, on_subnet_done=progress_cb,
    )


def _default_client_factory(url: str, api_key: str) -> ListenerClient:
    """默认监听器客户端工厂。"""
    return ListenerClient(url, api_key)


def create_app(
    config_path: str,
    *,
    scanner=None,
    client_factory=None,
    history_path: str | None = None,
    reconcile_interval: float | None = 30.0,
    reconciler_factory: Callable[[Callable[[], object], "HistoryStore"], Callable[[], None]] | None = None,
    scheduler_interval: float | None = 30.0,
    state_path: str | None = None,
    wake_fn: Callable[[str], None] | None = None,
    health_probe_fn: Callable[[ListenerClient], bool] | None = None,
    scheduler_injector: Callable[[Callable[[], object], Callable[[], dict]], object] | None = None,
) -> FastAPI:
    """创建 FastAPI 应用。

    可注入项（测试用）：
      scanner(ip, port)          -> 设备列表
      client_factory(url, key)   -> ListenerClient 实例
      history_path               -> 历史文件路径
      reconcile_interval         -> 后台对账循环间隔秒数；0 或 None 禁用
      reconciler_factory(client_getter, history) -> tick 可调用对象（每轮执行一次）；
                                    默认 None 用内置 _default_reconcile_tick。
      scheduler_interval         -> 定时调度循环间隔秒数；0 或 None 禁用
      state_path                 -> 定时任务运行状态文件路径（schedules_state.json）
      wake_fn(mac)               -> WOL 唤醒动作（默认 wol.send_magic_packet）
      health_probe_fn(client)    -> listener 就绪探测（默认 client.health() 可达即 True）
      scheduler_injector(client_getter, state_getter) -> Scheduler 实例（完全替换内置调度器）

    后台对账：lifespan 启动 daemon 线程，每 reconcile_interval 秒把 history 中
    running/completing 的记录向监听器查询一遍，终态/404 落盘，根治"浏览器一关
    历史就永远卡 running"。线程通过 stop event 退出，event.wait 保证退出快。

    定时调度：lifespan 再起一个 daemon 线程，每 scheduler_interval 秒调
    Scheduler.tick(now)：发现到期 schedule 投递独立 worker 执行
    （health 探测 → WOL → 等 listener 就绪 → trigger），不阻塞 tick 循环。
    """
    from scheduler import (
        Scheduler,
        ScheduleStateStore,
        default_state_path,
    )

    settings = Settings(config_path)
    history = HistoryStore(history_path or default_history_path())
    scan_fn = scanner or _default_scanner
    client_fn = client_factory or _default_client_factory
    schedule_state = ScheduleStateStore(state_path or default_state_path())

    # ---------- 后台对账（reconcile）----------

    def _reconcile_tick() -> int:
        """对账一轮：查询所有 running/completing 记录的最新状态并落盘。

        返回本轮仍在跟踪（活动态）的记录数。任何单条失败不影响其他记录。
        """
        try:
            jobs = history.all()
        except Exception:
            log.exception("reconcile: read history failed")
            return 0
        tracked = [j for j in jobs if j.get("state") in ACTIVE_STATES]
        still_active = 0
        for job in tracked:
            job_id = job.get("job_id")
            if not job_id:
                continue
            try:
                client, _ = _client_from_config()
            except _Unpaired:
                # 未配对：跳过本轮（配置可能稍后补上），保持记录原状
                still_active += len(tracked)
                break
            try:
                st = client.status(job_id)
            except ListenerNotFound:
                # 监听器已不认识该 job（重启丢历史等）：标记 unknown 停止跟踪
                # （unknown 不在跟踪集合，前端显示灰色徽章）
                history.record({"job_id": job_id, "state": "unknown"}, prev_fields_fallback=True)
                continue
            except ListenerError as e:
                # 网络/鉴权等异常：下轮重试（鉴权失败反复重试无害——NAS 未配对场景已被上面拦截）
                log.warning("reconcile: status(%s) failed: %s", job_id, e)
                still_active += 1
                continue
            state = st.get("state", "")
            if state in TERMINAL_STATES:
                history.record(
                    {"job_id": job_id, "state": state, "finished_at": time.time()},
                    prev_fields_fallback=True,
                )
            elif state == "unknown":
                # 监听器已不认识该 job（重启丢历史）：标记 unknown 停止跟踪（前端灰色徽章）
                history.record({"job_id": job_id, "state": "unknown"}, prev_fields_fallback=True)
            else:
                still_active += 1
        return still_active

    reconcile_tick = reconciler_factory(_client_from_config, history) if reconciler_factory else _reconcile_tick

    # ---------- 定时任务（scheduler）----------

    _wake = wake_fn  # None 时执行链内延迟 import wol（与 api_wol 的 patch 目标一致）

    def _default_wake(mac: str) -> None:
        from wol import send_magic_packet
        # 连发 3 包：UDP 广播不可靠（交换机可能丢弃全局广播）
        last_err: Exception | None = None
        for _ in range(3):
            try:
                send_magic_packet(mac)
                return
            except OSError as e:  # noqa: PERF203 - 重试间隔下再试
                last_err = e
                time.sleep(0.5)
        raise last_err if last_err else OSError("wake failed")

    def _default_health_probe(client: ListenerClient) -> bool:
        try:
            client.health()
            return True
        except Exception:
            return False

    def _execute_schedule(sched: dict) -> None:
        """定时任务执行链（独立 worker 线程内运行，阻塞不影 tick 循环）：
        health 探测（PC 已醒则短路）→ WOL → 等 listener 就绪 → trigger。
        所有失败分支统一落 state + history，绝不排队重试。"""
        import threading as _threading  # noqa: F401 - 本函数运行于 worker 线程
        sid = sched.get("id", "?")
        fired_wall = time.time()
        try:
            client, cfg = _client_from_config()
        except _Unpaired:
            schedule_state.record_fired(sid, fired_at=fired_wall, job_id=None,
                                        result="error", error="未配对设备")
            return
        try:
            # 1. 就绪探测：PC 已醒则跳过 WOL 与等待
            ready = health_probe_fn(client) if health_probe_fn else _default_health_probe(client)
            if not ready:
                # 2. WOL 唤醒（wake=false 表示 PC 常开，不再重试唤醒直接进等待）
                if sched.get("wake", True):
                    mac = cfg.get("target_mac") or ""
                    if not mac:
                        schedule_state.record_fired(sid, fired_at=fired_wall, job_id=None,
                                                    result="error", error="未配置 MAC，无法唤醒")
                        return
                    wake = _wake or _default_wake
                    try:
                        wake(mac)
                    except Exception as e:  # ValueError/OSError 统一落失败
                        schedule_state.record_fired(sid, fired_at=fired_wall, job_id=None,
                                                    result="wake_failed", error=str(e))
                        return
                # 3. 轮询 /health 等 listener 就绪（Windows 开机+自启 listener 通常 <2 分钟）
                timeout = int(sched.get("wake_timeout_sec", 300))
                deadline = time.time() + timeout
                while time.time() < deadline:
                    if health_probe_fn(client) if health_probe_fn else _default_health_probe(client):
                        ready = True
                        break
                    time.sleep(3)
                if not ready:
                    schedule_state.record_fired(sid, fired_at=fired_wall, job_id=None,
                                                result="wake_timeout", error=f"{timeout}s 内 listener 未就绪")
                    return
                # listener 刚起来时 TaskRegistry 热加载可能未完成：就绪后小缓冲
                time.sleep(1.0)
            # 4. 触发（409=Windows 忙 → 跳过；404 → 重试一次再失败才落 task_not_found）
            task_id = sched.get("task_id", "")
            try:
                result = client.trigger(task_id)
            except ListenerNotFound:
                time.sleep(2.0)
                try:
                    result = client.trigger(task_id)
                except ListenerNotFound:
                    schedule_state.record_fired(sid, fired_at=fired_wall, job_id=None,
                                                result="task_not_found", error=f"任务不存在：{task_id}")
                    return
            except ListenerError as e:
                msg = str(e)
                if "409" in msg or "busy" in msg.lower():
                    schedule_state.record_fired(sid, fired_at=fired_wall, job_id=None,
                                                result="skipped_busy", error="Windows 正在运行任务，本次跳过")
                    return
                raise
            job_id = result.get("job_id", "")
            t0 = time.time()
            # 与手动触发同构：落盘第一快照，带 schedule_id 溯源
            history.record({
                "job_id": job_id,
                "task_id": task_id,
                "display_name": result.get("display_name") or sched.get("name") or task_id,
                "state": "running",
                "created_at": t0,
                "finished_at": None,
                "schedule_id": sid,
            })
            schedule_state.record_fired(sid, fired_at=fired_wall, job_id=job_id, result="triggered")
            log.info("scheduler: schedule=%s triggered job=%s", sid, job_id)
        except Exception as e:  # 任何未预期异常都落 state，绝不中断调度循环
            log.exception("scheduler: execute schedule=%s failed", sid)
            schedule_state.record_fired(sid, fired_at=fired_wall, job_id=None,
                                        result="error", error=str(e))

    def _build_scheduler(client_getter, state_getter) -> "Scheduler":
        return Scheduler(client_getter, state_getter, execute_fn=_execute_schedule)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        stop_event = threading.Event()
        thread: threading.Thread | None = None
        sched_thread: threading.Thread | None = None
        if reconcile_interval:
            def _loop() -> None:
                while not stop_event.is_set():
                    try:
                        reconcile_tick()
                    except Exception:
                        log.exception("reconcile tick crashed (will retry next round)")
                    stop_event.wait(reconcile_interval)

            thread = threading.Thread(target=_loop, name="bgi-reconcile", daemon=True)
            thread.start()
        if scheduler_interval:
            def _sched_loop() -> None:
                from datetime import datetime
                while not stop_event.is_set():
                    try:
                        scheduler.tick(datetime.now(), settings.load().get("schedules", []))
                    except Exception:
                        log.exception("scheduler tick crashed (will retry next round)")
                    stop_event.wait(scheduler_interval)

            sched_thread = threading.Thread(target=_sched_loop, name="bgi-scheduler", daemon=True)
            sched_thread.start()
        try:
            yield
        finally:
            if thread is not None:
                stop_event.set()
                thread.join(timeout=5.0)
            if sched_thread is not None:
                stop_event.set()
                sched_thread.join(timeout=5.0)

    app = FastAPI(title="BetterGI Trigger NAS 应用", lifespan=lifespan)
    # 挂载静态目录，供 index.html 引用 /static/bgi_icon.png 作为标题图标
    if _STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    # ★ SPA（nas-app/frontend 构建产物，base=/spa/）经 /spa 挂载；
    #   html=True 使 GET /spa/ 返回其 index.html。产物不存在时跳过（不影响后端）。
    #   注意顺序：history 回退路由必须先于 StaticFiles Mount 注册——Starlette 按注册
    #   顺序匹配，Mount 在前会把 /spa/tasks 等深链吃掉直接 404，回退永远轮不到。
    _SPA_DIR = _STATIC_DIR / "spa"
    if _SPA_DIR.is_dir():
        _SPA_INDEX = _SPA_DIR / "index.html"

        @app.get("/spa/{rest:path}", include_in_schema=False)
        def spa_fallback(rest: str) -> FileResponse:
            # 真实资产文件（assets/*.js、favicon.png 等）直接返回该文件；
            # assets/ 下不存在的文件 404（资产缺失要暴露，不能静默回 HTML）；
            # 其余单段无扩展名路径视为前端 history 路由，回退 index.html。
            candidate = (_SPA_DIR / rest).resolve()
            if (
                rest
                and ".." not in rest
                and candidate.is_file()
                and candidate.is_relative_to(_SPA_DIR.resolve())
            ):
                return FileResponse(candidate)
            if "/" in rest or rest.endswith((".png", ".js", ".css", ".ico", ".map", ".webp")):
                raise HTTPException(status_code=404)
            return FileResponse(_SPA_INDEX)

        app.mount("/spa", StaticFiles(directory=str(_SPA_DIR), html=True), name="spa")

    def _client_from_config():
        """从已保存配置构造监听器客户端；未配对抛 _Unpaired。"""
        cfg = settings.load()
        target = cfg["default_target"]
        if not target:
            raise _Unpaired()
        url = f"http://{target['ip']}:{target['port']}"
        return client_fn(url, cfg["api_key"]), cfg

    # 定时调度器（必须在 _client_from_config 定义之后构建——执行链闭包引用它）
    scheduler = scheduler_injector(_client_from_config, lambda: schedule_state) if scheduler_injector \
        else _build_scheduler(_client_from_config, lambda: schedule_state)

    # ---------- 页面 ----------

    @app.get("/health")
    def health() -> dict:
        """NAS 应用自检。"""
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        """首页直接 307 跳转新版 SPA（/spa/）；旧 Jinja GUI 已移除。"""
        return RedirectResponse(url="/spa/", status_code=307)

    # ---------- M3：设备发现与配对 ----------

    def _compute_scan_plan(subnet: str | None, port: int) -> list[str]:
        """计算待扫描的子网顺序列表。不执行扫描，仅规划网段。"""
        from discovery import list_local_subnets, COMMON_SUBNETS, _safe_net_if_addrs
        if subnet:
            return [subnet]
        local = list_local_subnets(_safe_net_if_addrs())
        plan = list(local)
        if not local:
            # 宿主机(容器)未见任何 LAN 子网 → 把 COMMON_SUBNETS 作为完整 fallback
            plan = list(COMMON_SUBNETS)
        # 即使本地有子网,也把 COMMON 附在末尾(去重),万一本地不在 Windows 同网段
        for c in COMMON_SUBNETS:
            if c not in plan:
                plan.append(c)
        return plan

    def _run_scan_task(subnet: str | None, port: int) -> None:
        """后台扫描 worker：规划 → 发起 → 逐子网跟踪进度 → 完成。"""
        cfg = settings.load()
        try:
            plan = _compute_scan_plan(subnet, port)
            if not plan:
                _progress_finish(err="无可用子网")
                return
            _progress_reset(plan)

            def on_subnet_done(cidr: str, found: list[dict]) -> None:
                _progress_mark_subnet(cidr, found)

            scan_fn(subnet, port, progress_cb=on_subnet_done)
            _progress_finish(_progress_snapshot()["devices"])
        except Exception as e:
            _progress_finish(err=str(e))

    @app.post("/api/scan")
    def api_scan(body: ScanBody | None = None) -> dict:
        """启动后台扫描任务,返回 {started: True}; 结果通过 /api/scan-progress 轮询。

        请求体可选 {subnet, port},缺省自动规划子网。
        并发扫描同一时间只有一个进行,新请求需等待当前结束。
        """
        body = body or ScanBody()
        cfg = settings.load()
        subnet = body.subnet or cfg["scan"].get("subnet")
        port = body.port or cfg["scan"]["listener_port"]

        # 同步模式(单测/脚本): 直接返回设备,不启动后台任务
        if body.sync:
            return {"devices": scan_fn(subnet, port), "sync": True}

        snap = _progress_snapshot()
        if snap["active"]:
            return {"started": False, "reason": "已有扫描进行中", "progress": snap}
        # daemon 线程不会阻止 uvicorn 退出(容器重启时自然放弃)
        threading.Thread(target=_run_scan_task, args=(subnet, port), daemon=True).start()
        # 短暂等待让第一条进度同步到 _scan_progress,避免前端轮询拿到旧状态
        time.sleep(0.1)
        return {"started": True, "port": port, "subnet": subnet or "auto"}

    @app.get("/api/scan-progress")
    def api_scan_progress() -> dict:
        """扫描进度查询端点,前端每 ~800ms 轮询一次。

        返回:
          active: 是否正在扫描
          stage: idle|preparing|scanning|done|error
          subnets: [{subnet, status: pending|scanning|done|skipped, found}]
          scanned / total: 子网完成数
          devices: 当前发现的设备列表
          elapsed: 耗时(秒)
          error: 错误信息(如有)
          done_total: 已扫 IP 总数(估算,用于显示整体进度条)
        """
        snap = _progress_snapshot()
        # 给前端再多给一个 scanned_ips / total_ips 的估算,方便画连续进度条
        from discovery import _iter_hosts
        total_ips = sum(len(_iter_hosts(s["subnet"])) for s in snap["subnets"])
        scanned_ips = 0
        for s in snap["subnets"]:
            if s["status"] == "done":
                scanned_ips += len(_iter_hosts(s["subnet"]))
            elif s["status"] == "skipping":
                scanned_ips += len(_iter_hosts(s["subnet"])) // 2  # 粗略
        snap["scanned_ips"] = scanned_ips
        snap["total_ips"] = total_ips
        return snap

    @app.get("/api/discover-key")
    def api_discover_key(ip: str, port: int = 8765) -> dict:
        """服务端代理获取目标监听器的 /key。

        前端处于 HTTPS 时浏览器会拦截对 http://IP:port 的 mixed content 请求，
        故通过同源 HTTPS 后端代理中转。
        """
        if not ip:
            raise HTTPException(status_code=400, detail="missing ip")
        resp = _http_get_json(ip, port, path="/key", timeout=3.0)
        if resp is None:
            raise HTTPException(status_code=502, detail="无法连接监听器或返回无效")
        return resp

    @app.post("/api/pair")
    def api_pair(body: PairBody) -> dict:
        """配对：用提供的 ip/port/key 测试连接（health + tasks），通过则保存为默认目标。"""
        url = f"http://{body.ip}:{body.port}"
        client = client_fn(url, body.api_key)
        try:
            client.health()   # 验证服务身份
            client.tasks()    # 验证密钥有效
        except ListenerAuthError:
            raise HTTPException(status_code=401, detail="密钥错误或未授权")
        except ListenerError as e:
            raise HTTPException(status_code=502, detail=f"无法连接监听器：{e}")

        cfg = settings.load()
        cfg["default_target"] = {"ip": body.ip, "port": body.port, "hostname": body.hostname}
        cfg["api_key"] = body.api_key
        settings.save(cfg)
        return {"ok": True, "target": cfg["default_target"]}

    @app.post("/api/unpair")
    def api_unpair() -> dict:
        """取消配对：清除默认目标与密钥。"""
        cfg = settings.load()
        cfg["default_target"] = None
        cfg["api_key"] = ""
        settings.save(cfg)
        return {"ok": True}

    @app.get("/api/config")
    def api_config() -> dict:
        """返回当前配置（供前端展示配对状态）。"""
        return settings.load()

    # ---------- M4：任务触发与回报 ----------

    @app.get("/api/tasks")
    def api_tasks() -> list[dict]:
        """代理拉取 Windows 监听器的任务清单。"""
        try:
            client, _ = _client_from_config()
        except _Unpaired:
            raise HTTPException(status_code=400, detail="未配对设备，请先扫描配对")
        try:
            return client.tasks()
        except ListenerAuthError:
            raise HTTPException(status_code=401, detail="密钥失效，请重新配对")
        except ListenerError as e:
            raise HTTPException(status_code=502, detail=f"监听器不可达：{e}")

    @app.put("/api/tasks")
    def api_tasks_replace(body: TasksBody) -> list[dict]:
        """编辑任务清单：转发到 Windows 端 PUT /tasks（写回 tasks 文件，热加载生效）。

        Windows 400（任务定义非法）透传为 400；未配对 400；监听器不可达 502。
        """
        try:
            client, _ = _client_from_config()
        except _Unpaired:
            raise HTTPException(status_code=400, detail="未配对设备，请先扫描配对")
        try:
            return client.replace_tasks([t.model_dump() for t in body.tasks])
        except ListenerAuthError:
            raise HTTPException(status_code=401, detail="密钥失效，请重新配对")
        except ListenerError as e:
            if "400" in str(e):
                raise HTTPException(status_code=400, detail=f"任务定义非法：{e}")
            raise HTTPException(status_code=502, detail=f"保存失败：{e}")

    @app.post("/api/trigger", status_code=202)
    def api_trigger(body: TriggerBody) -> dict:
        """触发任务：转发到 Windows 监听器，并在 NAS 端记录历史(含 created_at 用于计算执行耗时)。"""
        try:
            client, _ = _client_from_config()
        except _Unpaired:
            raise HTTPException(status_code=400, detail="未配对设备，请先扫描配对")
        try:
            result = client.trigger(body.task_id)
        except ListenerAuthError:
            raise HTTPException(status_code=401, detail="密钥失效，请重新配对")
        except ListenerError as e:
            raise HTTPException(status_code=502, detail=f"触发失败：{e}")

        job_id = result.get("job_id", "")
        # display_name 优先取 Windows 返回的实际展示名,否则回退 task_id
        display_name = result.get("display_name") or body.task_id
        # 关键:整个触发-完成周期内只取一次时间戳,避免多次 time.time() 漂移
        t0 = time.time()
        # 落盘第一个快照,带上 created_at / display_name;
        # finished_at 在后续轮询到终态时补填(prev_fields_fallback 保最早的触发时间/显示名)
        history.record({
            "job_id": job_id,
            "task_id": body.task_id,
            "display_name": display_name,            # 可读名称(如 "挖矿");历史展示用
            "state": "running",
            "created_at": t0,                         # 触发时刻;用于前端计算执行时间
            "finished_at": None,
        })
        result["display_name"] = display_name
        result["created_at"] = t0                    # 一并返回,前端可立即显示
        return result

    @app.get("/api/status")
    def api_status(job_id: str) -> dict:
        """代理查询任务状态，并更新 NAS 端历史。"""
        try:
            client, _ = _client_from_config()
        except _Unpaired:
            raise HTTPException(status_code=400, detail="未配对设备，请先扫描配对")
        try:
            st = client.status(job_id)
        except ListenerAuthError:
            raise HTTPException(status_code=401, detail="密钥失效，请重新配对")
        except ListenerError as e:
            raise HTTPException(status_code=502, detail=f"查询失败：{e}")

        # 终态时补填 finished_at,保留最早触发的 created_at（不覆盖）。
        # TERMINAL_STATES 与 windows-listener JobState 逐字对齐
        # （历史上 "timeout" 拼错 + 漏 abnormal_exit 导致 timed_out/abnormal_exit 卡 running）。
        is_terminal = st.get("state") in TERMINAL_STATES
        rec = {"job_id": job_id, "task_id": st.get("task_id", ""), "state": st.get("state", "")}
        if is_terminal:
            rec["finished_at"] = time.time()
        history.record(rec, prev_fields_fallback=True)
        return st

    @app.get("/api/jobs")
    def api_jobs() -> list[dict]:
        """返回 NAS 端任务历史（最新在前）。"""
        return history.all()

    @app.post("/api/abort")
    def api_abort() -> dict:
        """中止当前任务：转发到 Windows 监听器（仅在 completing 反悔窗口内有效）。"""
        try:
            client, _ = _client_from_config()
        except _Unpaired:
            raise HTTPException(status_code=400, detail="未配对设备，请先扫描配对")
        try:
            return client.abort()
        except ListenerAuthError:
            raise HTTPException(status_code=401, detail="密钥失效，请重新配对")
        except ListenerError as e:
            raise HTTPException(status_code=502, detail=f"中止失败：{e}")

    @app.post("/api/stop")
    def api_stop(body: StopBody | None = None) -> dict:
        """强制停止当前任务：转发到 Windows 监听器 POST /stop（杀进程级）。

        返回 {"stopped": True, "killed": [...]}；错误映射与 /api/abort 一致。
        """
        try:
            client, _ = _client_from_config()
        except _Unpaired:
            raise HTTPException(status_code=400, detail="未配对设备，请先扫描配对")
        try:
            return client.stop()
        except ListenerAuthError:
            raise HTTPException(status_code=401, detail="密钥失效，请重新配对")
        except ListenerError as e:
            raise HTTPException(status_code=502, detail=f"停止失败：{e}")

    @app.post("/api/wol")
    def api_wol(body: WolBody | None = None) -> dict:
        """Wake-on-LAN：发送魔术包唤醒目标机器。

        body.mac 为空时回退配置 target_mac；两者都空返回 400。
        socket 异常（广播失败）映射为 502。
        """
        from wol import send_magic_packet

        body = body or WolBody()
        cfg = settings.load()
        mac = (body.mac or "").strip() or (cfg.get("target_mac") or "").strip()
        if not mac:
            raise HTTPException(status_code=400, detail="未配置 MAC 地址，请先在设置中填写 target_mac")
        try:
            send_magic_packet(mac)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except OSError as e:
            raise HTTPException(status_code=502, detail=f"魔术包发送失败：{e}")
        return {"sent": True, "mac": mac}

    # ---------- 定时任务（schedules）----------

    def _schedules_with_meta(cfg: dict | None = None) -> list[dict]:
        """读 schedules 并附运行元数据（next_fire_at / last_* ）。"""
        from datetime import datetime
        from scheduler import next_fire_at
        cfg = cfg or settings.load()
        state = schedule_state.all()
        now = datetime.now()
        out = []
        for s in cfg.get("schedules", []):
            item = dict(s)
            nf = next_fire_at(now, s.get("time", ""), s.get("weekdays"))
            item["next_fire_at"] = nf.timestamp() if nf else None
            st = state.get(s.get("id", "")) or {}
            item["last_fired_at"] = st.get("last_fired_at")
            item["last_result"] = st.get("last_result")
            item["last_error"] = st.get("last_error")
            out.append(item)
        return out

    @app.get("/api/schedules")
    def api_schedules_list() -> list[dict]:
        """定时任务列表（含下次触发时刻与上次执行结果）。"""
        return _schedules_with_meta()

    @app.put("/api/schedules")
    def api_schedules_put(body: SchedulesBody) -> list[dict]:
        """整体替换定时任务列表（前端编辑后全量回传）。

        校验：每条必须过 validate_schedule；task_id 做软校验（Windows 可达时
        校验存在性，不可达不阻断保存——离线也能编辑）。
        """
        from scheduler import validate_schedule
        for i, s in enumerate(body.schedules):
            errs = validate_schedule(s)
            if errs:
                raise HTTPException(status_code=400, detail=f"第 {i + 1} 条：{'；'.join(errs)}")
        # 软校验 task_id（不阻断）
        try:
            client, _ = _client_from_config()
            try:
                known = {t.get("id") for t in client.tasks()}
                unknown = [s.get("task_id") for s in body.schedules
                           if s.get("task_id") not in known]
                if unknown:
                    raise HTTPException(
                        status_code=400,
                        detail=f"任务不存在：{', '.join(map(str, unknown))}（Windows 端 tasks/*.json 已变更？）",
                    )
            except ListenerError:
                pass  # Windows 离线：跳过软校验
        except _Unpaired:
            pass
        cfg = settings.load()
        cfg["schedules"] = body.schedules
        settings.save(cfg)
        # 清理已删除 schedule 的残留状态
        keep_ids = {s.get("id") for s in body.schedules}
        for sid in list(schedule_state.all().keys()):
            if sid not in keep_ids:
                schedule_state.drop(sid)
        return _schedules_with_meta(cfg)

    @app.post("/api/schedules/{schedule_id}/run")
    def api_schedules_run(schedule_id: str) -> dict:
        """手动立即执行一条定时任务（与定时触发走同一条执行链）。

        直接同步投递 worker（不判断到点窗口）；返回前记录 dispatched，
        worker 完成后覆盖为最终结果。
        """
        cfg = settings.load()
        sched = next((s for s in cfg.get("schedules", []) if s.get("id") == schedule_id), None)
        if sched is None:
            raise HTTPException(status_code=404, detail="定时任务不存在")
        threading.Thread(
            target=_execute_schedule, args=(dict(sched),),
            name=f"bgi-sched-run-{schedule_id}", daemon=True,
        ).start()
        schedule_state.record_fired(
            schedule_id, fired_at=time.time(), job_id=None, result="dispatched",
        )
        return {"dispatched": True, "id": schedule_id}

    @app.get("/api/schedules/{schedule_id}/state")
    def api_schedules_state(schedule_id: str) -> dict:
        """单条定时任务的运行状态（轮询手动 run 的最终结果用）。"""
        st = schedule_state.get(schedule_id)
        if st is None:
            raise HTTPException(status_code=404, detail="无运行记录")
        return st

    # ---------- 实时日志 WS 代理（薄透传，消息不解析转换） ----------

    @app.websocket("/api/ws/logs/{job_id}")
    async def api_ws_logs(websocket: WebSocket, job_id: str) -> None:
        """代理浏览器与 Windows 监听器 ws://{ip}:{port}/ws/logs/{job_id} 之间的连接。

        上游消息均为 JSON 文本帧（{"ts","lines"} / {"state",...} / {"last":true} / {"error"}），
        原样透传不做解析。异常统一发一条 {"error": ...} 后关闭。
        """
        cfg = settings.load()
        target = cfg.get("default_target")
        await websocket.accept()
        if not target:
            await websocket.send_text('{"error": "unpaired"}')
            await websocket.close()
            return
        upstream_url = f"ws://{target['ip']}:{target['port']}/ws/logs/{job_id}"
        try:
            import websockets

            upstream = await websockets.connect(upstream_url, open_timeout=5)
        except Exception:
            log.warning("ws proxy: listener unreachable: %s", upstream_url)
            await websocket.send_text('{"error": "listener unreachable"}')
            await websocket.close()
            return

        async def pump_upstream_to_client() -> None:
            try:
                async for msg in upstream:
                    if isinstance(msg, bytes):
                        await websocket.send_bytes(msg)
                    else:
                        await websocket.send_text(msg)
            finally:
                await upstream.close()

        async def pump_client_to_upstream() -> None:
            """浏览器 → 监听器（一般只有 close / 偶发文本，均薄转发）。"""
            try:
                while True:
                    msg = await websocket.receive()
                    if msg.get("type") == "websocket.disconnect":
                        break
                    text = msg.get("text")
                    if text is not None:
                        await upstream.send(text)
                    elif msg.get("bytes") is not None:
                        await upstream.send(msg["bytes"])
            except WebSocketDisconnect:
                pass

        tasks = [asyncio.create_task(pump_upstream_to_client()),
                 asyncio.create_task(pump_client_to_upstream())]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                # 取出异常避免 "Task exception was never retrieved" 噪音
                if not t.cancelled() and t.exception() is not None:
                    log.warning("ws proxy: pump error: %s", t.exception())
        finally:
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            try:
                await upstream.close()
            except Exception:
                pass
            try:
                await websocket.close()
            except Exception:
                pass

    return app


# 模块级应用实例：供 `uvicorn main:app` 直接启动。
app = create_app(_default_config_path())
