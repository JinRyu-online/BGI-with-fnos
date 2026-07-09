# BetterGI Trigger Listener (Windows 端)

飞牛 NAS 通过 HTTP 触发本机执行 BetterGI 调度器组的监听服务。

## 架构

```
config.py     配置加载（TOML + 默认值 + 首启生成密钥）
tasks.py      任务清单（tasks.json，调度器组名 → 任务）
auth.py       密钥 + IP 自动学习白名单
state.py      单槽任务状态机（idle/running/completing/done/timeout/failed/aborted）
execution.py  完成判定 B（游戏进程）/C（日志关键字）/D（超时）
launcher.py   拉起 BetterGI + 后台监控 + 收尾动作（sleep/shutdown/lock）
app.py        FastAPI 五接口
listener.py   入口：装配依赖 + 密钥弹窗 + uvicorn
```

## 接口

| 方法 | 路径 | 鉴权 | 作用 |
|---|---|---|---|
| GET | `/health` | 否 | `{"service":"bgi-trigger","hostname":...,"version":...}` 供 NAS 扫描识别 |
| GET | `/tasks` | 是 | 返回任务清单 |
| POST | `/trigger` | 是 | `{"task_id":"daily"}` → `{"job_id":...}`（202），忙时 409 |
| GET | `/status?job_id=` | 是 | 任务状态 |
| POST | `/abort` | 是 | 中止当前任务（进入反悔窗口时阻止收尾） |

## 部署

1. 把整个 `windows-listener/` 目录拷到目标机（如 `D:\bgi_trigger\`）。
2. 以管理员身份运行 `install.bat`：
   - 建 venv、装依赖；
   - 从 `.example` 生成 `config.toml` 与 `tasks/tasks.json`；
   - 注册计划任务 `BGI-Trigger-Listener`（登录后自启 + 最高权限 + 失败重启）。
3. 编辑 `config.toml`：填 `bettergi.exe_path`（BetterGI.exe 路径）；`log_path`/`log_done_keyword` 留空则完成判定只用 B+超时。
4. 编辑 `tasks/tasks.json`：`groups` 须与 BetterGI「全自动-调度器」里的组名一致。
5. 首次启动会弹窗显示 API 密钥（复制到 NAS 应用）；之后想再看：`venv\Scripts\pythonw.exe listener.py --show-key`。

## 任务清单与热加载

任务来源由 `config.toml` 的 `[tasks] dir` 指定（默认 `tasks` 目录）。该目录下所有 `*.json` 会被读取并合并，每个文件可以是单个任务对象或任务数组：

```
windows-listener/tasks/
├── tasks.json          实际任务清单（install.bat 从 .example 生成）
└── tasks.json.example
```

**热加载**：修改/增删目录内 `.json` 后，**下一次 `/tasks` 请求即生效，无需重启监听器**。基于文件 mtime 检测变化。非法任务定义会被跳过并记日志（不会让 `/tasks` 整个失败）。

> 也可拆成每任务一个文件（如 `tasks/daily.json`、`tasks/abyss.json`），便于管理。`*.example` 后缀的文件不会被读取。

## 依赖与镜像（国内友好）

`install.bat` 的依赖安装策略：

- **镜像**：默认走清华 PyPI 镜像 `https://pypi.tuna.tsinghua.edu.cn/simple`，国内下载快。
- **包管理器**：优先用 **uv**（Python 版 pnpm，Astral 出品，安装快、并发下载），uv 不可用时自动回退到 pip。
  - uv 先由 `pip` 装入 venv，再由 uv 安装 `requirements.txt`。
  - 镜像通过环境变量 `UV_INDEX_URL` 注入（uv 原生支持）。

切换镜像只需改 `install.bat` 顶部的 `MIRROR` 变量，可选：

| 镜像 | 地址 |
|---|---|
| 清华（默认） | `https://pypi.tuna.tsinghua.edu.cn/simple` |
| 阿里云 | `https://mirrors.aliyun.com/pypi/simple` |
| 腾讯云 | `https://mirrors.cloud.tencent.com/pypi/simple` |

手动装依赖（不走 install.bat）：

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
python -m pytest tests/ -q          # 54 个单测
python listener.py                  # 本地启动（开发）
```

完成判定逻辑用可注入谓词单测覆盖；HTTP 层用 FastAPI TestClient 覆盖；`tests/test_smoke_wiring.py` 用真实 `.example` 文件做端到端装配校验。
