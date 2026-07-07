# BetterGI Trigger — 飞牛 fnOS FPK 应用

NAS 端应用：扫描局域网内的 Windows 监听器、配对、触发 BetterGI 任务、回报状态。

## ⚠️ 打包工作流（重要）

`manifest`、`config/privilege`、`config/resource`、`wizard/`、`ICON*.PNG` 这些 FPK 元数据
文件的**精确字段格式以 `fnpack` 生成的官方骨架为准**（官方文档部分页面为前端渲染，未能在线核实全部字段）。
推荐流程：

```bash
# 1. 用 fnpack 生成官方骨架（Docker 模板）
fnpack create bgi-trigger --template docker

# 2. 用本仓库 nas-app/ 下的内容覆盖/合并到生成的骨架：
#    - app/docker/Dockerfile           ← 直接覆盖
#    - app/docker/docker-compose.yaml  ← 直接覆盖
#    - app/docker/app/                 ← 直接覆盖（FastAPI 应用源码）
#    - cmd/main                        ← 直接覆盖
#    - manifest                        ← 合并字段（以 fnpack 生成的格式为准，补入本仓库的字段值）
#    - config/privilege、config/resource、wizard/  ← 以 fnpack 生成为准，按需合并
#    - ICON.PNG / ICON_256.PNG         ← 自备图标

# 3. 打包
fnpack build

# 4. 安装到 fnOS（二选一）
appcenter-cli install-fpk bgi-trigger.fpk   # SSH
# 或应用中心后台上传 .fpk
```

## 目录结构

```
nas-app/
├── manifest                     应用元信息（appname/version/source=thirdparty/platform=x86/ctl_stop=true）
├── config/
│   ├── privilege                权限声明（host 网络）
│   └── resource                 资源声明（端口 8000）
├── cmd/
│   └── main                     生命周期脚本（start/stop/status → docker compose）
├── wizard/
│   └── install                  安装期表单（M2 占位，配置走 Web GUI）
└── app/
    └── docker/
        ├── Dockerfile           python:3.12-slim + 依赖 + uvicorn
        ├── docker-compose.yaml  host 网络 + 挂载 etc/var
        ├── conftest.py          测试 sys.path 装配
        ├── app/                 FastAPI 应用源码
        │   ├── main.py          入口（首页 + /health）
        │   ├── settings.py      配置持久化（config.json）
        │   ├── requirements.txt
        │   └── templates/index.html
        └── tests/               单测
```

## M2 范围（已完成）

- 配置持久化模块 `settings.py`（load/save + 默认值合并）
- FastAPI 骨架：首页（Jinja2，展示配对目标）+ `/health`
- FPK 打包文件：Dockerfile、docker-compose.yaml、cmd/main、manifest、privilege、resource、wizard
- 单测：settings 4 个 + main 4 个，全绿

## 待实现（M3–M5）

- **M3** 设备扫描配对：子网探测 + `/health` 识别 + 密钥配对 + 保存默认目标
- **M4** 触发回报：`POST /trigger` + 10s 轮询 `/status` + 历史/日志展示（MVP 完成）
- **M5** 端口诊断：测试连接 + 端口扫描面板

## 本地开发/测试

```bash
cd nas-app/app/docker
python -m pytest tests/ -q
```
