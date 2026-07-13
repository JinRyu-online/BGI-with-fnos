# BetterGI Trigger Listener (Windows 端)

飞牛 NAS 通过 HTTP 触发本机执行 BetterGI 调度器组的监听服务。

## 架构

```
service/
  config.py      配置加载（TOML + 默认值 + 首启生成密钥）
  auth.py        密钥 + IP 自动学习白名单
core/
  state.py       单槽任务状态机（idle/running/completing/done/timeout/failed/aborted）
  execution.py   完成判定 B（游戏进程）/C（日志关键字）/D（超时）
  launcher.py    Launcher + _BetterGIExecutor: daemon 线程拉起 BetterGI + 监控 + grace 窗口
  log_harvester.py LogHarvester: 后台线程收割日志 + WS 实时推送（支持 glob 按天发现）
  tasks.py       任务清单（tasks.json，调度器组名 → 任务，热加载）
api/
  app.py         FastAPI 七接口（含 /ws/logs WebSocket）
listener.py     入口：装配依赖 + 密钥弹窗 + uvicorn
```

## 接口

| 方法 | 路径 | 鉴权 | 作用 |
|---|---|---|---|
| GET | `/health` | 否 | `{"service":"bgi-trigger","hostname":...,"version":...}` 供 NAS 扫描识别 |
| GET | `/key` | 否 | 返回 `{api_key, hostname}` 供 NAS 自动配对 |
| GET | `/tasks` | 是 | 返回任务清单 |
| POST | `/trigger` | 是 | `{"task_id":"daily"}` → `{"job_id":...}`（202），忙时 409 |
| GET | `/status?job_id=` | 是 | 任务状态 |
| POST | `/abort` | 是 | 中止当前任务（进入反悔窗口时阻止收尾） |
| WS | `/ws/logs/{job_id}` | 否 | 实时推送 BetterGI 日志（WebSocket） |

## 部署

1. 把整个 `windows-listener/` 目录拷到目标机（如 `D:\bgi_trigger\`）。
2. 以管理员身份运行 `install.ps1`：
   - 建 venv、装依赖；
   - 从 `.example` 生成 `config.toml` 与 `tasks/tasks.json`；
   - 注册计划任务 `BGI-Trigger-Listener`（登录后自启 + 最高权限，`pythonw.exe` 无控制台窗口）。
3. 编辑 `config.toml`：填 `bettergi.exe_path`（BetterGI.exe 路径）；`log_path`/`log_done_keyword` 留空则完成判定只用 B+超时。
4. 编辑 `tasks/tasks.json`：`groups` 须与 BetterGI「全自动-调度器」里的组名一致。
5. 首次启动会弹窗显示 API 密钥（复制到 NAS 应用）；之后想再看：`python listener.py --show-key`。

### 开机自启

两种二选一（也可并存）：

| 方式 | 任务名 | 窗口 | 权限 | 适用 |
|---|---|---|---|---|
| `install.ps1`（管理员） | `BGI-Trigger-Listener` | 无（pythonw） | `/RL HIGHEST` | 日常使用（后台静默） |
| `enable_autostart.ps1` | `BGI-Trigger-Listener-Console` | 有（PowerShell 控制台可见） | 默认 `/RL LIMITED`，需管理员时改脚本 `$RL="HIGHEST"` | 调试/排查 |

> **为什么有两个？** `install.ps1` 用 `pythonw.exe` 在后台静默运行（无窗口）。`enable_autostart.ps1` 用 `start_listener.ps1` 带一个可见控制台窗口，方便排查启动问题（端口占用、日志）。`-Console` 任务默认 `/RL LIMITED` 不弹 UAC；如发现 BetterGI 无法启动/聚焦游戏，把 `enable_autostart.ps1` 里的 `$RL` 改成 `"HIGHEST"` 并重跑。

## 任务清单与热加载

任务来源由 `config.toml` 的 `[tasks] dir` 指定（默认 `tasks` 目录）。该目录下所有 `*.json` 会被读取并合并，每个文件可以是单个任务对象或任务数组：

```
windows-listener/tasks/
├── tasks.json          实际任务清单（install.ps1 从 .example 生成）
└── tasks.json.example
```

**热加载**：修改/增删目录内 `.json` 后，**下一次 `/tasks` 请求即生效，无需重启监听器**。基于文件 mtime 检测变化。非法任务定义会被跳过并记日志（不会让 `/tasks` 整个失败）。

> 也可拆成每任务一个文件（如 `tasks/daily.json`、`tasks/abyss.json`），便于管理。`*.example` 后缀的文件不会被读取。

### tasks.json 字段说明（带注释样例）

> ⚠️ 实际 `tasks.json` 必须是**纯 JSON**（加载器不解析注释）。下面的 `//` 注释仅用于文档说明各字段含义，编辑时请去掉注释、保留 JSON 本身。

```jsonc
[
  {
    // 任务唯一标识。/trigger {"task_id":"daily"} 通过它引用此任务。同一目录内不可重复
    "id": "daily",

    // 在 NAS GUI / Apifox 上显示的名称。省略时默认取 id
    "display_name": "日常一条龙",

    // BetterGI 调度器组名列表，按顺序执行。必须与 BetterGI「全自动-调度器」里的组名逐字一致，
    // 否则触发后 BetterGI 找不到组会失败。建议末尾放「关闭游戏」组，让游戏退出以触发完成判定 B
    "groups": ["日常一条龙", "关闭游戏"],

    // 本任务最大执行时长（分钟）。超时则标记 state=timeout 并尝试清理残留进程
    "timeout_min": 90,

    // 任务完成后的收尾动作。可选值：
    //   sleep     休眠（默认，可被 WOL 唤醒；需 Windows 已 powercfg /hibernate on）
    //   shutdown  关机
    //   lock      锁屏（不关机，保留登录态）
    //   none      不动作
    "after_done": "sleep"
  },
  {
    "id": "abyss",
    "display_name": "深渊刷本",
    "groups": ["深渊", "关闭游戏"],
    "timeout_min": 60,
    "after_done": "sleep"
  }
]
```

字段速查：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | 是 | 任务唯一标识，`/trigger` 引用 |
| `display_name` | string | 否 | 显示名称，缺省取 `id` |
| `groups` | string[] | 是 | BetterGI 调度器组名，须与 BetterGI 一致 |
| `timeout_min` | int | 否 | 最大执行时长（分钟），缺省 90 |
| `after_done` | string | 否 | 收尾动作，缺省 `sleep` |

## 依赖与镜像（国内友好）

`install.ps1` 的依赖安装策略：

- **镜像**：默认走清华 PyPI 镜像 `https://pypi.tuna.tsinghua.edu.cn/simple`，国内下载快。
- **包管理器**：优先用 **uv**（Python 版 pnpm，Astral 出品，安装快、并发下载），uv 不可用时自动回退到 pip。
  - uv 先由 `pip` 装入 venv，再由 uv 安装 `requirements.txt`。
  - 镜像通过环境变量 `UV_INDEX_URL` 注入（uv 原生支持）。

切换镜像只需改 `install.ps1` 顶部的 `$MIRROR` 变量，可选：

| 镜像 | 地址 |
|---|---|
| 清华（默认） | `https://pypi.tuna.tsinghua.edu.cn/simple` |
| 阿里云 | `https://mirrors.aliyun.com/pypi/simple` |
| 腾讯云 | `https://mirrors.cloud.tencent.com/pypi/simple` |

手动装依赖（不走 install.ps1）：

```bash
# 用 uv（推荐，快）
python -m venv venv
venv\Scripts\pip install uv -i https://pypi.tuna.tsinghua.edu.cn/simple
set UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
venv\Scripts\python -m uv pip install -r requirements.txt

# 或用 pip + 镜像
venv\Scripts\pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 开发

```bash
python -m pytest tests/ -q          # 69 个单测
python listener.py                  # 本地启动（开发，有控制台窗口）
pythonw.exe listener.py             # 无控制台窗口（模拟计划任务环境）
python listener.py --show-key       # 重新弹出密钥窗口
```

关键测试覆盖：完成判定逻辑（可注入谓词）、HTTP 层（FastAPI TestClient）、真实配置装配（`tests/test_smoke_wiring.py`）。测试总数 69 个。

### 已知陷阱

- **`.ps1` 文件必须保存为 UTF-8 with BOM**（PowerShell 5.1 在无 BOM 时按系统 ANSI/GBK 读取，UTF-8 中文会被误解析成 `$变量` 插值语法，脚本直接解析失败）。VS Code 右下角可切换「Save with Encoding → UTF-8 with BOM」。
- **`log_path` 支持 glob**：填如 `C:/Software/BetterGI/log/better-genshin-impact*.log` 会自动按 mtime 取最新一份。
