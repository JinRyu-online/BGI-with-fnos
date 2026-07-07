"""NAS 应用 FastAPI 主模块。

提供 Web GUI（Jinja2 模板）与服务自检接口。M2 仅含首页（展示当前配对目标）
与 /health；M3/M4/M5 将在此基础上增加设备扫描、触发回报、端口诊断等路由。

应用通过 create_app(config_path) 工厂构造，配置路径可注入，便于测试。
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from settings import Settings

# 模板目录位于 app/templates/（容器内 /app/templates）。
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _default_config_path() -> str:
    """配置文件路径：优先用 fnOS 注入的 TRIM_PKGETC，否则用本地 etc/config.json（开发）。"""
    base = os.environ.get("TRIM_PKGETC")
    if base:
        return str(Path(base) / "config.json")
    # 开发回退：源码目录旁的 etc/config.json
    return str(Path(__file__).resolve().parent.parent / "etc" / "config.json")


def create_app(config_path: str) -> FastAPI:
    """创建 FastAPI 应用。config_path 指向 config.json 的存储路径。"""
    app = FastAPI(title="BetterGI Trigger NAS 应用")
    settings = Settings(config_path)

    @app.get("/health")
    def health() -> dict:
        """服务自检。"""
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        """首页：展示当前配对目标，未配对时提示去扫描设备。"""
        cfg = settings.load()
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"config": cfg},
        )

    return app


# 模块级应用实例：供 `uvicorn main:app` 直接启动。
# 配置路径取自环境变量 TRIM_PKGETC（容器内由 docker-compose 注入）。
app = create_app(_default_config_path())
