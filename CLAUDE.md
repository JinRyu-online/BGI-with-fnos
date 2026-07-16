# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A two-component system that lets a fnOS NAS (飞牛 NAS) trigger a Windows 11 PC to run [BetterGI](https://www.bgi.com/) automation (Genshin Impact bot) over LAN HTTP. The Windows PC uses WOL + AUTOLOGON to boot and auto-start a listener service; the NAS runs a web GUI for pairing, scanning, and triggering tasks.

## Commands

### Windows Listener (`windows-listener/`)

```powershell
# Install (admin): create venv, install deps, generate config from .example, register scheduled task
./install.ps1
```

```bash
# Run unit tests (listener: 73 tests, nas-app: 38 tests)
python -m pytest tests/ -q
python -m pytest tests/test_tasks.py  # single file
python -m pytest tests/test_app.py::test_trigger_starts_job_and_returns_id  # single test
```

```bash
# Run NAS app tests
python -m pytest app/docker/tests/ -q
```

```bash
# Run the listener locally (development). First run shows tkinter key popup.
python listener.py
python listener.py --show-key  # re-show key without consuming first-run marker
```

```bash
# Re-trigger first-run key popup (delete marker then run)
rm -f .first_run_done && python listener.py
```

### NAS FPK App (`nas-app/`)

```powershell
# Package into .fpk (requires fnpack CLI from https://developer.fnnas.com/docs/cli/fnpack/)
./build.ps1                     # use fnpack from PATH
$env:FNPACK="C:\path\fnpack.exe"; ./build.ps1  # explicit path
```

```bash
# Run NAS app locally for development (outside Docker)
cd nas-app/app/docker/app
uvicorn main:app --host 0.0.0.0 --port 8000
```

```bash
# Run NAS app tests
python -m pytest app/docker/tests/ -q
```

## Architecture

### Dependency Flow (Windows Listener, 重构为 bgi_trigger 包后)

业务代码统一放在 `bgi_trigger/` 包内,按职责三层拆分;主目录仅保留 `listener.py` 一个主入口。

```
listener.py  (装配所有依赖 + tkinter 密钥弹窗 + uvicorn 启动)
  └── (absolute import) bgi_trigger/
      ├── api/app.py            → FastAPI 七接口(/health /key /tasks /trigger /status /abort + /ws/logs/{job_id})
      ├── service/
      │   ├── auth.py           →  AuthState: api_key 校验 + IP 自动学习白名单
      │   └── config.py         →  ListenerConfig: TOML + 默认值 + 首启生成密钥并回写
      └── core/
          ├── state.py          →  JobStore: 单槽状态机(threading.Lock + Event 保证并发安全)
          ├── launcher.py       →  Launcher + _BetterGIExecutor: daemon 线程拉起 BetterGI + 监控 + grace 窗口
          ├── execution.py      →  CompletionMonitor: 完成判定 B(进程)/C(日志)/D(超时)
          ├── log_harvester.py  →  LogHarvester: 后台线程收割 BetterGI 日志 + WS 实时推送
          └── tasks.py          →  TaskRegistry: 目录热加载 + mtime 签名
```

### Dependency Flow (NAS FPK App)

```
main.py  (FastAPI factory: create_app(config_path, scanner, client_factory, history_path))
  ├── discovery.py      →  list_local_subnets + scan_subnet_parallel (TCP probe → /health verification)
  ├── listener_client.py →  ListenerClient: typed wrapper over Windows 5 endpoints (health/tasks/trigger/status)
  ├── history.py        →  HistoryStore: job history dedup+truncate (jobs.json)
  ├── settings.py       →  Settings: JSON config with defaults merge (config.json)
  └── templates/        →  Jinja2 single-page GUI
```

### State Machine (Single-Slot)

Only one BetterGI instance runs at a time (avoids contesting the game window):

```
idle ──POST /trigger──▶ running ──B/C fires──▶ completing(30s grace) ──▶ done
                           │                         │ POST /abort         │
                           └─── timeout ────▶ timeout                    └──▶ aborted
```

Completion detection (any one fires, first wins):
- **B** — `psutil` reports `YuanShen.exe` / `GenshinImpact.exe` gone (most reliable)
- **C** — BetterGI log contains a configured keyword (default disabled; `log_path`/`log_done_keyword` empty)
- **D** — `timeout_min` expires (default 90 min)

After completion, `grace_seconds` (default 30s) window allows `/abort` to skip the `after_done` action. `after_done` options: `sleep` (hibernate, default), `shutdown`, `lock`, `none`.

### Security Model

- `Authorization: Bearer <api_key>` required on all endpoints except `/health`.
- IP auto-learning: first request with the correct key auto-trusts the source IP (persisted to `trusted.json`).
- `/trigger` only accepts `task_id` values from `tasks.json` — never raw command lines.
- Transport is plain HTTP (trusted LAN). No TLS for MVP.

### Task Hot Reload

`tasks.py` watches the `tasks/` directory by file mtime signature. Any change to a `*.json` file is picked up on the *next* `/tasks` or `/trigger` request — no restart needed. Malformed files/entries are skipped with a warning log, never crash the whole registry. Each file can be a single task object or an array; `*.example` suffix files are ignored.

### Key Non-Obvious Details

- **`install.ps1`** runs as admin (auto-UAC elevation), creates venv, installs deps (uv with pip fallback), generates config from `.example`, registers the scheduled task `BGI-Trigger-Listener` (pythonw, no console, `/RL HIGHEST`).
- **`enable_autostart.ps1` / `disable_autostart.ps1`** register/unregister a second scheduled task `BGI-Trigger-Listener-Console` that runs `start_listener.ps1` in a **visible PowerShell console** (useful for debugging; default `/RL LIMITED`, switch to `HIGHEST` if BetterGI needs elevation). The two tasks coexist.
- **`config.py` uses a hand-rolled TOML serializer** (third-party `tomllib` only reads; writes use a simple `[section]\nkey = value` dumper). Don't add nested-section config without extending `_dump_toml` / `_toml_value`.
- **`psutil` is a soft dependency** — `execution.py` defers `import psutil` so the module imports cleanly for logic-only tests.
- **`BetterGI.exe` is invoked as** `[exe, "--startGroups", *groups]` (scheduler groups only, not one-click/JS scripts). Group names must match BetterGI's UI verbatim.
- **`CREATE_NEW_PROCESS_GROUP` flag** in `launcher.py` — BetterGI must run in its own process group with a console window, otherwise it can't see the game window when launched from a non-interactive session (SSH, scheduled task). This is the fix for the SSH/session-isolation bug.
- **`_BetterGIExecutor`** class in `launcher.py` — refactored out of `Launcher._execute` to avoid deep nesting; encapsulates the full launch→monitor→finalize→after_done flow. **启动前检测**：拉起前先分别探测 `BetterGI.exe` 与游戏进程（`psutil`）是否已在运行，任一命中则跳过 `Popen`，直接把已有实例交给 `CompletionMonitor` 接管监控（覆盖用户手动启动的场景——此时槽位为空，单槽保护拦不到）。
- **`LogHarvester`** in `log_harvester.py` — background thread tails BetterGI log (supports glob patterns for daily logs), bridges to asyncio via `run_coroutine_threadsafe`, feeds the `/ws/logs/{job_id}` WebSocket for real-time log streaming. 启动后若目标文件不存在，每 0.5s 轮询等待，最多等 **5 分钟**（覆盖冷启动 + 慢速加密盘 + 长加载链），超时则放弃并打 `log file never appeared`。glob 模式选文件时优先选文件名含**当天日期**（YYYYMMDD）的文件，而非 mtime 最新，避免跨日误 tail 昨日残留。
- **`create_app` in `app.py` takes its deps via `AppDeps` dataclass** — never construct `AuthState` / `JobStore` / `Launcher` inside `app.py`; they are always injected. Tests pass fakes via `launch=lambda job, task: None`.
- **`create_app` in `nas-app/app/docker/app/main.py`** similarly injects `scanner`, `client_factory`, `history_path`. Real implementations call the LAN; tests pass fakes.
- **Job IDs** are `uuid.uuid4().hex[:12]` (12 hex chars).
- **Windows scheduled task** uses `pythonw.exe` (no console) and requires admin (`/RL HIGHEST` — BetterGI needs elevation).
- **`start_and_trigger.ps1`** is a dev helper: starts the listener and fires a trigger in one step; it hardcodes a sample API key for local testing.
- **`.ps1` files must be UTF-8 with BOM** on Chinese Windows — PowerShell 5.1 reads ANSI (GBK) by default and misparses UTF-8 Chinese strings (breaks `$variable` interpolation inside strings). The BOM forces UTF-8 reading.

## Container / Deployment Notes

- NAS FPK is a Docker app using `network_mode: host` (LAN scanning needs host network). `docker-compose.yaml` mounts `/var/apps/bgi-trigger/shares/bgi-trigger/data` → `/data` for persistence.
- `BGI_DATA_DIR` env var points to persistent storage (`/data` in container, inherited by `Settings.default_path` and `default_history_path`).
- FPK packaging: `fnpack build` produces `bgi-trigger*.fpk`; install via `appcenter-cli install-fpk`.

## File Reference

| Path | Purpose |
|---|---|
| `windows-listener/listener.py` | 唯一主入口(装配 + 密钥弹窗 + uvicorn) |
| `windows-listener/install.ps1` | One-shot deploy script (admin, auto-UAC, registers `BGI-Trigger-Listener` pythonw task) |
| `windows-listener/enable_autostart.ps1` / `disable_autostart.ps1` | Register/unregister visible-console task `BGI-Trigger-Listener-Console` |
| `windows-listener/start_listener.ps1` | Foreground launcher (port check, logs, debug) — run by the `-Console` task |
| `windows-listener/start_and_trigger.ps1` | Dev helper: start + trigger one step |
| `windows-listener/config.toml` | Live config (generated by install.ps1 from `.example`) |
| `windows-listener/config.toml.example` | Config template (documents all fields incl. glob log_path) |
| `windows-listener/tasks/tasks.json` | Live task list (hot-reloaded) |
| `windows-listener/api/openapi.yaml` | API contract reference (7 endpoints) |
| `nas-app/app/docker/app/` | NAS FastAPI app (packaged into .fpk) |
| `nas-app/build.ps1` | Package to .fpk |
| `nas-app/manifest` | fnOS app metadata (platform=x86, network=host) |
| `docs/开发方案.md` | Full design doc (Chinese) — canonical source of truth for architecture |
