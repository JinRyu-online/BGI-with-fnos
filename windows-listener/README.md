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
   - 从 `.example` 生成 `config.toml` / `tasks.json`；
   - 注册计划任务 `BGI-Trigger-Listener`（登录后自启 + 最高权限 + 失败重启）。
3. 编辑 `config.toml`：填 `bettergi.exe_path`（BetterGI.exe 路径）；`log_path`/`log_done_keyword` 留空则完成判定只用 B+超时。
4. 编辑 `tasks.json`：`groups` 须与 BetterGI「全自动-调度器」里的组名一致。
5. 首次启动会弹窗显示 API 密钥（复制到 NAS 应用）；之后想再看：`venv\Scripts\pythonw.exe listener.py --show-key`。

## 开发

```bash
python -m pytest tests/ -q          # 54 个单测
python listener.py                  # 本地启动（开发）
```

完成判定逻辑用可注入谓词单测覆盖；HTTP 层用 FastAPI TestClient 覆盖；`tests/test_smoke_wiring.py` 用真实 `.example` 文件做端到端装配校验。
