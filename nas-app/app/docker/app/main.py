"""NAS 应用 FastAPI 主模块。

提供 Web GUI（Jinja2 模板）与一组 JSON API：
  设备发现与配对（M3）：/api/scan  /api/pair  /api/unpair  /api/config
  任务触发与回报（M4）：/api/tasks  /api/trigger  /api/status  /api/jobs

应用通过 create_app(...) 工厂构造，扫描器、监听器客户端工厂、历史路径均可注入，
便于单元测试（用假扫描器/假客户端替代真实网络）。

扫描进度通过 /api/scan-progress 轮询获取,前端实时展示每个子网的状态(pending/scanning/done)
与整体进度条。线程安全的进度状态保存在 _scan_progress 单例中,每完成一个子网就更新一次。
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from discovery import auto_discover_and_scan, list_local_subnets, COMMON_SUBNETS
from history import HistoryStore, default_history_path
from listener_client import ListenerAuthError, ListenerClient, ListenerError
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

# 模板目录位于 app/templates/（容器内 /app/templates）。
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


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


def _default_config_path() -> str:
    """配置文件路径：容器内由 compose 注入 BGI_DATA_DIR=/data；
    开发环境回退到源码旁的 etc/config.json。"""
    return Settings.default_path()


def _default_scanner(subnet: str | None, port: int, *, progress_cb: Callable[[str, list[dict]], None] | None = None) -> list[dict]:
    """按子网粒度扫描，每完成一个子网调 progress_cb(subnet, found_devices)。

    优化策略（解决容器内扫描慢的问题）：
      - 跳过本地超大子网（prefix < 24，如 docker 网桥 172.17.0.0/16 = 65534 IP）
      - 优先扫 COMMON_SUBNETS（家用常见网段，命中概率最高）
      - 发现任一设备后立即停止，不再扫剩余网段
      - 仅当 COMMON 全未命中时，补扫本地的 /24 子网

    进度通过 progress_cb 逐子网回调，由 api_scan 接入 _progress_mark_subnet。
    单独测试/使用时 progress_cb=None 即回退到静默模式。
    """
    import ipaddress
    from discovery import _safe_net_if_addrs
    _seen: dict[str, dict] = {}

    def _scan_one(cidr: str) -> list[dict]:
        from discovery import scan_subnet_parallel, default_probe, default_http_get
        devs = scan_subnet_parallel(cidr, port, default_probe, default_http_get, max_workers=128)
        for d in devs:
            _seen[d["ip"]] = d
        if devs:
            log.info("scan hit %s -> %s", cidr, [d["ip"] for d in devs])
        return devs

    def _plan_and_scan(cidrs: list[str]) -> bool:
        """扫描列表，遇到首个命中即返回 True（用于 early exit）。"""
        for c in cidrs:
            found = _scan_one(c)
            if progress_cb:
                progress_cb(c, found)
            if _seen:
                return True
        return False

    # 1. 用户指定的子网（唯一，无 fallback）
    if subnet:
        log.info("scan: explicit subnet %s", subnet)
        _plan_and_scan([subnet])
        return list(_seen.values())

    # 收集本地 /24（丢弃 >/24 的大网段如 docker 网桥 /16, 避免扫几十万 IP）
    local_small = []
    for s in list_local_subnets(_safe_net_if_addrs()):
        try:
            net = ipaddress.ip_network(s, strict=False)
            if net.prefixlen >= 24:          # 仅保留 /24 或更小（点对点 /30, /31, /32）
                local_small.append(s)
            else:
                log.info("scan: skip large local subnet %s (%d hosts)", s, net.num_addresses)
        except ValueError:
            pass

    # 2. 优先扫 COMMON（家用/办公网段最有可能命中, 每条 ≤ 254 IP）
    log.info("scan: plan COMMON(%d ranges) first, local /24(%d) as fallback",
             len(COMMON_SUBNETS), len(local_small))
    if _plan_and_scan(COMMON_SUBNETS):
        return list(_seen.values())

    # 3. COMMON 全没命中 → 补扫本地 /24 子网
    if local_small:
        if _plan_and_scan(local_small):
            return list(_seen.values())

    return list(_seen.values())


def _default_client_factory(url: str, api_key: str) -> ListenerClient:
    """默认监听器客户端工厂。"""
    return ListenerClient(url, api_key)


def create_app(
    config_path: str,
    *,
    scanner=None,
    client_factory=None,
    history_path: str | None = None,
) -> FastAPI:
    """创建 FastAPI 应用。

    可注入项（测试用）：
      scanner(ip, port)          -> 设备列表
      client_factory(url, key)   -> ListenerClient 实例
      history_path               -> 历史文件路径
    """
    app = FastAPI(title="BetterGI Trigger NAS 应用")
    settings = Settings(config_path)
    history = HistoryStore(history_path or default_history_path())
    scan_fn = scanner or _default_scanner
    client_fn = client_factory or _default_client_factory

    def _client_from_config():
        """从已保存配置构造监听器客户端；未配对抛 _Unpaired。"""
        cfg = settings.load()
        target = cfg["default_target"]
        if not target:
            raise _Unpaired()
        url = f"http://{target['ip']}:{target['port']}"
        return client_fn(url, cfg["api_key"]), cfg

    # ---------- 页面 ----------

    @app.get("/health")
    def health() -> dict:
        """NAS 应用自检。"""
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        """首页：GUI 单页，配对状态由前端通过 API 获取。"""
        cfg = settings.load()
        return templates.TemplateResponse(
            request=request, name="index.html", context={"config": cfg}
        )

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
        # 落盘第一个快照,带上 created_at (Unix 时间);finished_at 在后续轮询到终态时补填
        history.record({
            "job_id": job_id,
            "task_id": body.task_id,
            "state": "running",
            "created_at": time.time(),               # 触发时刻;用于前端计算执行时间
            "finished_at": None,
        })
        result["task_id"] = body.task_id
        result["created_at"] = time.time()           # 一并返回,前端可立即显示
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

        # 终态时补填 finished_at,保留最早触发的 created_at（不覆盖）
        is_terminal = st.get("state") in ("done", "timeout", "failed", "aborted")
        rec = {"job_id": job_id, "task_id": st.get("task_id", ""), "state": st.get("state", "")}
        if is_terminal:
            rec["finished_at"] = time.time()
        history.record(rec, keep_created_at=True)
        return st

    @app.get("/api/jobs")
    def api_jobs() -> list[dict]:
        """返回 NAS 端任务历史（最新在前）。"""
        return history.all()

    return app


# 模块级应用实例：供 `uvicorn main:app` 直接启动。
app = create_app(_default_config_path())
