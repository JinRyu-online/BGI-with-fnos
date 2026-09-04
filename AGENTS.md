# AGENTS.md

面向 AI 代理/编码协作的代码库指南。项目已有面向人的文档（[README.md](README.md)、[CLAUDE.md](CLAUDE.md)、[docs/开发方案.md](docs/开发方案.md)），本文件聚焦"改代码前必须知道的约定与陷阱"。

## 项目概览

双端系统：飞牛 fnOS NAS 通过局域网纯 HTTP（无 TLS，可信内网）触发一台 Windows 11 PC 运行 [BetterGI](https://bgi.xyz/)（原神自动化）。配合 WOL + Windows 自动登录实现"远程开机 → 执行 → 休眠"闭环。

- **windows-listener/** — Windows 端：FastAPI + uvicorn 监听 HTTP，守护线程拉起 `BetterGI.exe --startGroups <组名...>`，轮询完成判定，结束后执行休眠/关机等收尾动作。Python 3.11+（用内置 `tomllib`）。
- **nas-app/** — NAS 端：飞牛 FPK Docker 应用（`network_mode: host`），FastAPI 单页 GUI，负责扫描发现、配对、触发、轮询状态、WebSocket 实时日志。Python 3.12 容器。

两端通信：NAS 调 Windows 的 7 个端点（`/health` `/key` `/tasks` `/trigger` `/status` `/abort` + WS `/ws/logs/{job_id}`）；协议契约见 `windows-listener/api/openapi.yaml`。

## 常用命令

```bash
# Windows 端测试（81 个单测；在 windows-listener/ 下）
python -m pytest tests/ -q
python -m pytest tests/test_tasks.py            # 单文件
python -m pytest tests/test_app.py::test_trigger_starts_job_and_returns_id  # 单测

# NAS 端测试（40 个单测；在 nas-app/app/docker/ 下）
python -m pytest tests/ -q

# 本地跑 NAS 应用（容器外开发）
cd nas-app/app/docker/app && uvicorn main:app --host 0.0.0.0 --port 8000

# 本地跑监听器（开发；首启弹 tkinter 密钥窗）
cd windows-listener && python listener.py
python listener.py --show-key          # 再次显示密钥
rm -f .first_run_done                  # 重新触发首启弹窗

# 打包 NAS FPK（需 fnpack CLI，https://developer.fnnas.com/docs/cli/fnpack/）
cd nas-app && ./build.sh               # 或 Windows: ./build.ps1；$env:FNPACK 可指路径
```

注意：测试从各自组件目录运行（两端的 `conftest.py` 都在操作 `sys.path`）。`windows-listener/venv/` 是本地虚拟环境，不要提交。

## 架构

### Windows 端（bgi_trigger 包，三层）

`listener.py` 是唯一主入口（装配依赖 + tkinter 密钥弹窗 + uvicorn），业务全在 `bgi_trigger/` 包内：

```
bgi_trigger/
├── api/app.py            FastAPI 路由（7 端点）。所有依赖经 AppDeps dataclass 注入
├── service/
│   ├── auth.py           AuthState：Bearer 密钥校验 + IP 自动学习白名单（持久化 trusted.json）
│   └── config.py         ListenerConfig：TOML 加载 + 默认值合并 + 首启生成密钥回写
└── core/
    ├── state.py          JobStore：单槽状态机（threading.Lock + Event 并发安全）
    ├── launcher.py       Launcher + _BetterGIExecutor：守护线程拉起 BetterGI → 监控 → 收尾
    ├── execution.py      CompletionMonitor：完成判定 B/C/D；build_command
    ├── log_harvester.py  LogHarvester：后台线程 tail 日志 → deque + asyncio 桥 → WS 推流
    └── tasks.py          TaskRegistry：tasks/*.json 目录热加载（mtime 签名）
```

### NAS 端（扁平布局）

`nas-app/app/docker/app/` 下模块**扁平 import**（`from settings import ...`，与容器 WORKDIR=/app 一致；`conftest.py` 把 app/ 加进 sys.path 来兼容此约定）：

```
main.py             create_app 工厂（scanner/client_factory/history_path 均可注入）
discovery.py        网卡枚举 → CIDR → 并行 TCP 探活 + /health 识别（service=="bgi-trigger"）
listener_client.py  ListenerClient：Windows 端 typed 客户端（ListenerAuthError/ListenerError）
history.py          HistoryStore：jobs.json 按 job_id 去重更新、截断 50 条
settings.py         Settings：config.json 深度合并 DEFAULT_CONFIG
templates/index.html  Jinja2 单页 GUI
```

### 状态机（单槽）

同一时刻只跑一个 BetterGI 实例（避免抢游戏窗口）：

```
idle ──POST /trigger──▶ running ──B/C 命中──▶ completing(grace 30s) ──▶ done
                           │                        │ POST /abort         │
                           └─── 超时(24h 兜底) ──▶ timed_out              └──▶ aborted
```

终态：`done` / `abnormal_exit`（游戏退出）/ `timed_out` / `failed` / `aborted`。**只有 `done` 执行 after_done 收尾动作**（sleep/shutdown/lock/none）。槽位在终态即视为空闲，但 `current` 保留到下次 `start()` 覆盖或 `abort()` 清空。

### 完成判定（每轮轮询按序检查，先到先得：abort > B > C > D）

| 通道 | 判定 | 备注 |
|---|---|---|
| A abort | `/abort` 置位 Event | 守护线程每轮 poll 检查 |
| B game_exited | `psutil` 探 `YuanShen.exe`/`GenshinImpact.exe` 消失 | 启动后前 30s 预热期内抑制（防止游戏还没起来被误判） |
| C log_keyword | 日志关键字命中计数 ≥ `required_matches` | 需配 `log_path` + `log_done_keyword`；默认关闭（留空） |
| D timeout | 24h 硬编码（`MAX_TASK_DURATION_SEC`，不可配置的安全网） | |

**C 通道计数模式**（易踩坑）：BetterGI 每个调度组结束都打 `→ "任务结束"`（带双引号）。`log_done_mode="count"`（默认）时 `required_matches=len(groups)`，只有最后一组结束才触发；`"first"` 则首次命中即触发。关键字**必须带双引号**（TOML 写 `'"任务结束"'`）——无引号的 `任务结束` 会匹配到子任务行导致提前完成。

### 非阻塞启动模型

`/trigger` 调 `deps.launch(job, task)` 立即返回 202 + job_id；实际工作在 daemon 线程：Popen BetterGI → `CompletionMonitor.wait()` 阻塞轮询 → mark_completing → grace 窗口（内循环查 abort 信号）→ finalize → 仅 DONE 时清残留进程 + 执行 after_done。

**启动前检测**：拉起前先探测 BetterGI.exe 与游戏进程是否已在运行（用户手动启动的场景——槽位为空，单槽保护拦不到），任一命中则跳过 Popen，直接交给 CompletionMonitor 接管。

## 关键约定与陷阱（改代码前必读）

1. **`.ps1` 文件必须存为 UTF-8 with BOM**。中文 Windows 的 PowerShell 5.1 无 BOM 时按 GBK 读取，UTF-8 中文字符串会解析失败（尤其破坏 `"$变量"` 插值）。新建/编辑 `.ps1` 后务必确认编码。

2. **`CREATE_NEW_PROCESS_GROUP` 不能删**（`launcher.py`）。BetterGI 必须在新进程组 + 自带控制台窗口中运行，否则在计划任务/SSH 等非交互会话里看不见游戏窗口。

3. **`psutil` 是软依赖**：`execution.py` 的 `make_game_checker` 延迟 `import psutil`，保证纯逻辑测试在无 psutil 环境可导入。别把 import 提到模块顶层。

4. **`config.py` 用手写 TOML 序列化器**（`_dump_toml`/`_toml_value`）：只支持扁平分节（`[section]\nkey = value`），不支持嵌套表。新增嵌套配置需先扩展序列化器。读取用内置 `tomllib`。默认值改动需同步 `config.toml.example`。

5. **依赖注入是硬约定**：两端 `create_app` 一律通过参数/AppDeps 注入依赖（auth/jobs/launch/scanner/client_factory/history_path），**绝不在 app.py 内部构造**。测试靠塞 fake（如 `launch=lambda job, task: None`）。

6. **`/trigger` 只接受白名单 task_id**，永远不接受原始命令行。任务定义在 `tasks/*.json`（可数组可单对象；`*.example` 不加载；坏文件跳过并告警，不拖垮整个 registry）。`groups` 必须与 BetterGI「全自动-调度器」UI 组名逐字一致——BetterGI 命令行只能跑调度器组（`--startGroups`），不能直接跑单个 JS 脚本。

7. **模块级注册表与线程模型**：`launcher.py` 的 `_harvesters`（job_id → LogHarvester）是模块级 dict + lock；WS 端点经 `get_harvester(job_id)` 查找。任务结束后 harvester 保留 5 分钟（`threading.Timer`）供 WS 读完最后日志再清理——别改成立即 stop。`LogHarvester` 用 `run_coroutine_threadsafe` 把同步收割线程桥到 asyncio，`attach_loop()` 必须在 WS handler 里先注册。

8. **日志收割健壮性设定值**（有真实场景依据，勿随意调小）：文件不存在最多等 5 分钟（冷启动+加密盘+长加载链）；glob 模式优先选**文件名含当天日期（YYYYMMDD）**的文件而非 mtime 最新（避免跨日 tail 昨日残留）；从文件末尾 seek 开始，不收割历史。

9. **`/status` 404 日志频率抑制**（`app.py`）：NAS 会持续轮询已归档的旧 job_id（历史截断后查不到），404 只打 INFO 且同一 job_id 5 分钟内不重复。同类"正常时序现象"别升 WARNING。

10. **NAS 端历史时间戳**：触发时取一次 `t0` 写入 `created_at`，终态轮询时用 `prev_fields_fallback=True` 从旧记录补字段——不要在多处重复 `time.time()`（会漂移）。

11. **NAS 容器必须 `network_mode: host`**（扫局域网必需），数据经 `BGI_DATA_DIR=/data` 持久化 `config.json`/`jobs.json`；`Settings.default_path()` 无该环境变量时回退源码旁 `etc/config.json`（开发态）。

12. **Windows 端鉴权豁免**：`/health`（NAS 扫描身份识别，`service` 字段值 `"bgi-trigger"` 不可改）、`/key`（内网配对用）、WS 之外全部要求 `Authorization: Bearer <api_key>`。IP 白名单是审计层非门禁：密钥正确的首个请求自动学习来源 IP。

## 部署脚本（windows-listener/）

| 脚本 | 作用 |
|---|---|
| `install.ps1` | 一键部署（自动 UAC 提权）：建 venv → 装依赖（uv 优先/pip 回退 + 国内镜像）→ 从 `.example` 生成 `config.toml` 与 `tasks/tasks.json` → 注册计划任务 `BGI-Trigger-Listener`（pythonw 无窗口、`/RL HIGHEST`） |
| `enable_autostart.ps1` / `disable_autostart.ps1` | 注册/注销第二个计划任务 `BGI-Trigger-Listener-Console`：可见控制台跑 `start_listener.ps1`，调试用；与上面任务并存 |
| `start_listener.ps1` | 前台启动器（端口检查、日志） |
| `start_and_trigger.ps1` | 开发辅助：启动 + 触发一步完成（内含硬编码样例 key，勿用于生产） |

`config.toml`（gitignore，由 install 生成）支持 `bettergi.dir` 一行简化配置：非空时自动推导 `exe_path`/`config_path`/`log_path`（glob），显式字段优先。

## 测试风格

- 两端都走「依赖注入 + fake + FastAPI TestClient」：HTTP 层不触网，完成判定逻辑与真实进程解耦。
- Windows 端测试文件按模块拆（test_state / test_execution / test_launcher / test_app / test_auth / test_config / test_tasks / test_smoke_wiring）。
- `LogHarvester` 提供 `wait_for_ready()` / `wait_for_lines()` 同步原语供测试规避时序竞争——写相关测试时先用它们，不要 sleep 硬编码。
- `test_smoke_wiring.py` 验证 listener.py 端到端装配；改装配逻辑后跑它。

## 文档地图

| 文档 | 内容 |
|---|---|
| `docs/开发方案.md` | 完整设计文档（中文），架构真相源 |
| `windows-listener/api/openapi.yaml` | 监听器 7 端点协议契约 |
| `windows-listener/README.md` / `nas-app/README.md` / `nas-app/打包说明.md` | 两端部署与打包细节 |
| `docs/测试步骤.md` / `docs/测试注意事项.md` | 联调流程与避坑 |
| `CLAUDE.md` | Claude Code 版指南（与本文内容相近） |
