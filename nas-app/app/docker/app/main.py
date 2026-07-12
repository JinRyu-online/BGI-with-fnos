"""NAS 应用 FastAPI 主模块。

提供 Web GUI（Jinja2 模板）与一组 JSON API：
  设备发现与配对（M3）：/api/scan  /api/pair  /api/unpair  /api/config
  任务触发与回报（M4）：/api/tasks  /api/trigger  /api/status  /api/jobs

应用通过 create_app(...) 工厂构造，扫描器、监听器客户端工厂、历史路径均可注入，
便于单元测试（用假扫描器/假客户端替代真实网络）。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from discovery import auto_discover_and_scan  # 替代 psutil+scan_subnet 串接
from history import HistoryStore, default_history_path
from listener_client import ListenerAuthError, ListenerClient, ListenerError
from settings import Settings

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


def _default_scanner(subnet: str | None, port: int) -> list[dict]:
    """默认扫描器：自动发现子网 + scan；无果则回退扫描常见家用/办公子网。

    流程见 discovery.auto_discover_and_scan：
      1. 用户显式指定的 subnet（如有）
      2. 宿主机网卡自动发现的本地子网
      3. COMMON_SUBNETS 列表（家用/办公常见网段）作为兜底
    """
    return auto_discover_and_scan(port=port, subnet=subnet,
                                  max_workers=128, common_fallback=True)


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

    @app.post("/api/scan")
    def api_scan(body: ScanBody | None = None) -> dict:
        """扫描局域网，返回识别为 BetterGI 监听器的设备列表。

        请求体可选：{subnet: "192.168.1.0/24", port: 8765}，缺省自动探测子网、用配置端口。
        """
        body = body or ScanBody()
        cfg = settings.load()
        subnet = body.subnet or cfg["scan"].get("subnet")
        port = body.port or cfg["scan"]["listener_port"]
        devices = scan_fn(subnet, port)
        return {"devices": devices}

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
        """触发任务：转发到 Windows 监听器，并在 NAS 端记录历史。"""
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
        history.record({
            "job_id": job_id, "task_id": body.task_id, "state": "running"
        })
        return {"job_id": job_id}

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

        history.record({
            "job_id": job_id, "task_id": st.get("task_id", ""), "state": st.get("state", "")
        })
        return st

    @app.get("/api/jobs")
    def api_jobs() -> list[dict]:
        """返回 NAS 端任务历史（最新在前）。"""
        return history.all()

    return app


# 模块级应用实例：供 `uvicorn main:app` 直接启动。
app = create_app(_default_config_path())
