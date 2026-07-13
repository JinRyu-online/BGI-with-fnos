# BetterGI Trigger — 飞牛 fnOS FPK 应用

NAS 端应用：扫描局域网内的 Windows 监听器、配对、触发 BetterGI 任务、回报状态。

## 打包

本目录已是一个合法的 fnpack 项目，可直接打包，**无需**先 `fnpack create` 再覆盖。

```bash
# 1. 装 fnpack（见 打包说明.md §1）
# 2. 打包
./build.sh                # 或 build.ps1（Windows）
# 3. 产出 bgi-trigger.fpk，安装到飞牛 NAS
```

详见 **[打包说明.md](打包说明.md)**。

## 目录结构

```
nas-app/
├── manifest                 INI 元信息（appname/version/source/platform/desktop_*）
├── ICON.PNG / ICON_256.PNG  应用图标
├── config/
│   ├── privilege            JSON：运行身份（run-as=package）
│   └── resource             JSON：docker-project + data-share 声明
├── cmd/                     生命周期脚本（main 仅 status；start/stop 空操作，docker 由 appcenter 管）
├── wizard/                  安装期表单（留空，配置走 Web GUI）
├── app/
│   ├── ui/
│   │   ├── config           JSON：fnOS 桌面入口（port=8000, url=/）
│   │   └── images/          桌面图标
│   └── docker/
│       ├── Dockerfile       python:3.12-slim + uvicorn
│       ├── docker-compose.yaml   container_name=bgi-trigger，host 网络，数据卷→/data
│       └── app/             FastAPI 源码
│           ├── main.py          入口（页面 + 全部 /api 路由）
│           ├── settings.py      配置持久化（config.json，BGI_DATA_DIR）
│           ├── discovery.py     psutil 子网发现 + 并行 LAN 扫描
│           ├── listener_client.py  Windows 监听器 HTTP 客户端
│           ├── history.py       任务历史持久化
│           ├── requirements.txt
│           └── templates/index.html
├── build.sh / build.ps1     打包脚本
└── README.md / 打包说明.md
```

## 关键设计

- **容器生命周期**：docker 由 fnOS appcenter 依据 `config/resource` 的 `docker-project` 统一启停；`cmd/main` 仅 `status` 查容器。
- **数据持久化**：`data-share` 挂载到 `/data`，应用经 `BGI_DATA_DIR=/data` 读写 `config.json`/`jobs.json`。
- **host 网络**：扫描局域网必需（容器需见宿主 LAN 子网）。若 fnOS 不允许，回退 bridge + 手填子网（见打包说明.md §5.3）。
- **桌面入口**：`app/ui/config` 声明跳转 `http://<NAS>:8000/`。

## 范围

- **M2** 骨架：配置持久化 + 首页 + /health + 打包文件 ✅
- **M3** 设备扫描配对：子网探测 + /health/`/key` 识别 + 密钥配对 ✅
- **M4** 触发回报：/trigger + 10s 轮询 /status + 历史 + WS 实时日志（**MVP 完成**） ✅

> v0.2.0：M1–M4 端到端真机跑通。

## 本地开发/测试

```bash
cd nas-app/app/docker
python -m pytest tests/ -q          # 38 个单测

# 不走 Docker，直接跑 FastAPI 验证
cd app
BGI_DATA_DIR=/tmp/bgi_data python -m uvicorn main:app --host 0.0.0.0 --port 8766
```
