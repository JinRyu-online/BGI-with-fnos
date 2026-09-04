# BGI-with-fnos

> 让飞牛 fnOS NAS 通过局域网触发一台 Windows 11 电脑，自动运行 [BetterGI](https://www.bgi.com/) 原神自动化调度组。

整个系统由两端组成，通过纯 HTTP（可信局域网，无 TLS）通信：

```
 飞牛 fnOS NAS                           Windows 11 PC
┌──────────────────┐   HTTP/WS     ┌──────────────────────────┐
│   nas-app (FPK)  │ ◄──────────► │   windows-listener       │
│   FastAPI 单页   │   8765 端口   │   FastAPI + uvicorn      │
│  扫描 / 配对 /   │              │   拉起 BetterGI.exe      │
│  触发 / 状态回报 │              │   完成判定 + WS 实时日志  │
└──────────────────┘              └──────────────────────────┘
        ▲                                       ▲
        │ WOL + AUTOLOGON 开机自启              │ 计划任务(管理员/HIGHEST)
        └───────────────────────────────────────┘
```

- **Windows 端**监听 HTTP 请求，拉取任务、拉起 BetterGI、判定完成、在收尾窗口期接受中止，任务结束后执行休眠/关机等收尾动作。
- **NAS 端**是一个飞牛 FPK 应用：扫描局域网发现 Windows 监听器 → 配对（交换 API 密钥）→ 显示任务清单 → 触发 → 轮询状态 + WebSocket 实时看日志。

## 目录

- [适用场景](#适用场景)
- [功能特性](#功能特性)
- [架构总览](#架构总览)
- [快速开始](#快速开始)
- [状态机（单槽）](#状态机单槽)
- [完成判定](#完成判定)
- [接口一览](#接口一览)
- [安全模型](#安全模型)
- [测试](#测试)
- [子文档](#子文档)

## 适用场景

- 人不在电脑前，想通过手机/NAS 远程让 Windows 机跑一轮原神日常 / 深渊 / 钓鱼 / 挖矿 等调度组。
- 配合 **WOL（局域网唤醒）+ Windows 自动登录**：NAS 先 WOL 唤醒电脑，等 Windows 启动后监听器自启，再触发任务 —— 真正的"远程开机-执行-休眠"闭环。

## 功能特性

- **单槽**：同一时刻只跑一个 BetterGI 实例（避免抢游戏窗口），新触发在忙时返回 409。
- **完成判定多通道**（任一命中即完成，先命中者胜）：B 游戏进程退出 → C 日志关键字 → D 超时兜底。
- **30s 反悔窗口**（`completing` 状态）：完成判定命中后给 30 秒调 `/abort` 阻止收尾动作（防误触）。
- **收尾动作可选**：休眠（默认，可被 WOL 唤醒）/ 关机 / 锁屏 / 不动作。
- **实时日志**：后台线程收割 BetterGI 日志，通过 WebSocket（`/ws/logs/{job_id}`）推送到浏览器；支持 glob 按天自动发现最新日志。
- **任务热加载**：`tasks/*.json` 变更在下一次 `/tasks` 请求即生效，无需重启监听器。
- **自动恢复**：浏览器刷新或 NAS 重启后，若有仍在运行的旧任务，页面会自动恢复轮询 + WS 日志（不丢现场）。

## 架构总览

```
nas-app/app/docker/app/                    windows-listener/
 ├── main.py  ← 应用工厂                    ├── listener.py  ← 唯一入口
 ├── discovery.py   LAN 扫描                ├── install.ps1  一键部署
 ├── listener_client.py                     └── bgi_trigger/
 ├── history.py                                 ├── api/app.py     七接口+WS
 ├── settings.py                                ├── service/
 └── templates/index.html                      │   ├── auth.py   密钥+IP 白名单
                                                │   └── config.py TOML+首启生成
                                                └── core/
                                                    ├── state.py       单槽状态机
                                                    ├── launcher.py    拉起 BetterGI
                                                    ├── execution.py   完成判定 B/C/D
                                                    ├── log_harvester.py 日志收割+WS
                                                    └── tasks.py       任务热加载
```

两端都采用**依赖注入**：FastAPI 工厂（`create_app`）接收外部注入的依赖（scanner / client / launcher / auth / store），方便测试里直接塞 fake。

详细架构见 [`docs/开发方案.md`](docs/开发方案.md)。

## 快速开始

### 1. Windows 端部署（一次性）

```powershell
# 把整个 windows-listener/ 目录拷到目标机（如 D:\bgi_trigger\）
cd D:\bgi_trigger
# 以管理员身份运行（自动弹 UAC）
.\install.ps1
```

`install.ps1` 会自动：建 venv → 装依赖（优先 uv，回退 pip，国内走清华镜像）→ 从 `.example` 生成 `config.toml` + `tasks/tasks.json` → 注册计划任务 `BGI-Trigger-Listener`（登录后自启，最高权限，`pythonw.exe` 无控制台窗口）。

启动后首次会**弹窗显示 API 密钥**，复制到 NAS 应用配对用。想再看一次：`python listener.py --show-key`。

编辑 `config.toml`：填 `bettergi.dir`（或 `exe_path`/`log_path`），`groups` 须与 BetterGI「全自动-调度器」里的组名逐字一致。

如需带**可见控制台窗口**的调试版，再跑一次 `enable_autostart.ps1`，会注册第二个任务 `BGI-Trigger-Listener-Console`。两个任务可并存（详见子文档「部署」一节）。

### 2. NAS 端部署

```bash
cd nas-app
./build.sh                # 或 Windows 下 build.ps1；产出 bgi-trigger.fpk
# 把 .fpk 安装到飞牛 NAS（appcenter-cli install-fpk 或后台上传）
```

容器用 `network_mode: host`（扫描局域网必需），数据卷挂载到 `/data`（`BGI_DATA_DIR`）持久化 `config.json` / `jobs.json`。详见本目录 [`nas-app/README.md`](nas-app/README.md) 与 [`nas-app/打包说明.md`](nas-app/打包说明.md)。

### 3. 使用流程

1. NAS 应用首页 → **扫描** → 局域网发现 Windows 监听器。
2. **配对**：自动拉取监听器的 `/key`（API 密钥 + 主机名）并落盘。
3. 任务清单自动从监听器拉取（`tasks.json` 定义）。
4. **触发** → 拿到 `job_id` → 页面每 10s 轮询 `/status`，同时走 WS 实时看日志。
5. 命中完成判定后进入 30s 反悔窗口，可点**中止**阻止收尾动作。
6. 反悔窗口过后执行收尾动作（默认休眠，可被下次 WOL 唤醒）。

## 状态机（单槽）

```
idle ──POST /trigger──▶ running ──B/C 命中──▶ completing(grace 30s) ──▶ done
                            │                        │ POST /abort         │
                            └─── 超时 ────▶ timed_out                    └──▶ aborted
```

终态：`done` / `abnormal_exit`（游戏闪退）/ `timed_out` / `failed` / `aborted`。只有 `done` 走收尾动作。

状态枚举（浏览器与监听器一致）：`running` / `completing` / `done` / `abnormal_exit` / `timed_out` / `failed` / `aborted`。

## 完成判定

优先级 **A > B > C > D**（A 中止最高优先，任意一个命中即完成）：

| 通道 | 判定方式 | 说明 |
|---|---|---|
| **A** abort | 用户调 `/abort` | 人工中止 |
| **B** game_exited | `psutil` 探 `YuanShen.exe`/`GenshinImpact.exe` 消失 | 最可靠，推荐让任务列表末尾放「关闭游戏」组来触发 |
| **C** log_keyword | BetterGI 日志命中关键字（默认 `"`任务结束`"`，带双引号） | 需配 `log_path` 才启用；旧版无引号值会命中子任务导致误判 |
| **D** timeout | 到达 `timeout_min`（默认 90 分钟）安全兜底 | 永远生效 |

> ⚠️ 关于 **C 通道的引号**：BetterGI 真正的整组结束打印 `→ "任务结束"`（**带双引号**），而子任务结束打 `→ 钓鱼任务结束`/`→ 挖矿任务结束`（无引号，带组名前缀）。所以关键字**必须写作带双引号的 `"`任务结束`"`**（TOML 里写作 `'"任务结束"'`），否则会误把子任务当整体完成、提前终止。

## 接口一览

Windows 监听器暴露 8 个端点（协议细节见 [`windows-listener/api/openapi.yaml`](windows-listener/api/openapi.yaml)）：

| 方法 | 路径 | 鉴权 | 作用 |
|---|---|---|---|
| GET | `/health` | 否 | 服务身份签名（NAS 扫描识别用） |
| GET | `/key` | 否 | 返回 `{api_key, hostname}` 供 NAS 自动配对 |
| GET | `/tasks` | 是 | 任务清单 |
| POST | `/trigger` | 是 | `{"task_id":"daily"}` → 202 + `{"job_id":...}`，忙时 409 |
| GET | `/status?job_id=` | 是 | 任务状态（含完成原因） |
| POST | `/abort` | 是 | 中止任务：杀 BetterGI（可配连游戏），归档 aborted |
| POST | `/stop` | 是 | 强制清理 BetterGI + 游戏进程（残留/卡死自救） |
| WS | `/ws/logs/{job_id}` | 否 | 实时推送 BetterGI 日志（增量推送不丢行，终态推 `last` 帧） |

除 `/health`、`/key`、WS 外，都需要 `Authorization: Bearer <api_key>`。

NAS 应用自身的代理接口：`/api/scan` `/api/pair` `/api/tasks` `/api/trigger` `/api/status` `/api/abort` `/api/stop` `/api/wol` `/api/jobs` `/api/discover-key` `/api/ws/logs/{job_id}`（WS 同源代理——浏览器**不直连** Windows，规避 HTTPS mixed content 与防火墙问题）。

Web GUI 有两版：新版移动端优先 SPA（`/spa/`，源码 [`nas-app/frontend/`](nas-app/frontend/)，Vue3+TS）；旧版单页（`/`，保留为兼容入口）。NAS 后台每 30s 自动对账：浏览器关闭后运行中的历史任务也会被刷成终态，不再卡"运行中"。设置页支持 WOL 一键唤醒（需填目标机 MAC）。

## 安全模型

- **密钥鉴权**：除三个豁免端点外全部走 `Bearer <api_key>`；`api_key` 首启随机生成（32 位 hex），也可在 `config.toml` 手填。
- **IP 自动学习**：第一个带正确密钥的请求，其来源 IP 会被自动加入白名单并持久化到 `trusted.json`；也可手动预授权。
- **任务白名单**：`/trigger` 只接受 `tasks.json` 中已定义的 `task_id`，不接受原始命令行。
- **可信局域网**：MVP 用明文 HTTP，不配置 TLS。请确保运行在家庭/办公可信 LAN，不要暴露到公网。

## 测试

```bash
# Windows 端（124 个单测）
cd windows-listener
python -m pytest tests/ -q

# NAS 端（83 个单测）
cd nas-app/app/docker
python -m pytest tests/ -q
```

两端都大量使用依赖注入 + fake，HTTP 层用 FastAPI `TestClient`，覆盖完成判定逻辑、路由、配置装配、扫描、历史持久化。

## 子文档

| 文档 | 内容 |
|---|---|
| [`windows-listener/README.md`](windows-listener/README.md) | Windows 端详解：部署、开机自启、任务清单与热加载、依赖镜像、开发 |
| [`nas-app/README.md`](nas-app/README.md) | NAS FPK 应用：打包、目录结构、容器/数据/网络设计 |
| [`nas-app/打包说明.md`](nas-app/打包说明.md) | fnpack 打包全流程与 fallback |
| [`docs/开发方案.md`](docs/开发方案.md) | 完整设计文档（架构真相源） |
| [`docs/测试步骤.md`](docs/测试步骤.md) | 测试流程 |
| [`docs/测试注意事项.md`](docs/测试注意事项.md) | 测试避坑 |
| [`docs/背景.md`](docs/背景.md) | 项目背景 |
| [`CLAUDE.md`](CLAUDE.md) | 面向 AI 协作者的代码库指南（架构、命令、关键细节） |

## 已知陷阱

- **`.ps1` 文件必须保存为 UTF-8 with BOM** —— PowerShell 5.1 在无 BOM 时按系统 ANSI/GBK 读取，UTF-8 中文会被误解析成 `$变量` 插值语法，脚本直接解析失败。VS Code 右下角可切换「Save with Encoding → UTF-8 with BOM」。
- **BetterGI 必须在独立进程组+控制台窗口里跑**（`CREATE_NEW_PROCESS_GROUP`）—— 否则在 SSH / 计划任务等非交互会话里 BetterGI 看不见游戏窗口。`launcher.py` 已处理。
- **`psutil` 是软依赖**：`execution.py` 延迟 `import psutil`，保证纯逻辑测试不依赖它也能导入。

## License

本项目为个人自用工具，按仓库内 LICENSE 文件（如有）使用。
