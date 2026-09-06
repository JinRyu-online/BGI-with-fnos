"""监听器入口模块。

用法：
  pythonw.exe listener.py            正常启动（由计划任务调用）
  python listener.py --show-key      重新弹出密钥窗口

提供八个 HTTP 接口：
  GET  /health     免鉴权，返回服务身份签名
  GET  /key        免鉴权，返回 {api_key, hostname}（供 NAS 自动配对）
  GET  /tasks      鉴权，返回任务清单
  POST /trigger    鉴权，启动任务
  GET  /status     鉴权，查询任务状态
  POST /abort      鉴权，中止当前任务
  POST /stop       鉴权，急停：清理残留进程
  GET  /bgi/groups 鉴权，枚举 BetterGI 调度器已有组名

启动流程：
  1. 加载 config.toml（首启自动生成密钥）与 tasks.json；
  2. 装配鉴权、任务存储、启动器、应用依赖；
  3. 首次启动（无 .first_run_done 标记）或带 --show-key 时，弹窗显示密钥；
  4. uvicorn 启动 HTTP 服务。

密钥弹窗使用 tkinter，运行于主线程（阻塞至用户关闭），随后才启动 uvicorn。
因此「首次启动」会在用户关闭弹窗后才开始服务——首次安装时用户在场，可接受。
后续开机因标记已存在，直接启动服务，不弹窗。
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import tkinter as tk
from pathlib import Path

import uvicorn

from bgi_trigger import __version__ as VERSION
from bgi_trigger.api.app import AppDeps, create_app
from bgi_trigger.core import execution
from bgi_trigger.service.auth import AuthState
from bgi_trigger.service.config import ListenerConfig
from bgi_trigger.core.launcher import Launcher
from bgi_trigger.core.state import JobStore
from bgi_trigger.core.tasks import TaskRegistry
# 所有运行时文件均位于本脚本所在目录。
BASE_DIR = Path(__file__).resolve().parent
# 确保日志目录存在（pythonw 等无控制台场景依赖文件日志）
(BASE_DIR / "log").mkdir(exist_ok=True)
CONFIG_PATH = BASE_DIR / "config.toml"
TRUSTED_PATH = BASE_DIR / "trusted.json"           # 已信任 IP 持久化
FIRST_RUN_MARKER = BASE_DIR / ".first_run_done"    # 首次启动标记


def _resolve_tasks_dir(config: ListenerConfig) -> Path:
    """解析任务清单目录：相对路径相对于 BASE_DIR，绝对路径原样使用。"""
    p = Path(config.tasks.dir)
    return p if p.is_absolute() else BASE_DIR / p


def _make_bgi_groups_reader(config: ListenerConfig):
    """构造 GET /bgi/groups 的默认组名枚举回调（DI 注入 app.py，端点层不碰配置）。

    组名唯一来源是 bettergi.dir：<dir>/User/ScriptGroup/*.json 的文件名 stem
    （BetterGI 一个调度组一个文件，组名即文件名，与 --startGroups 参数逐字一致）。
    注意：不用 config_path 推导——config_path 指向 BetterGI 配置目录，
    与 ScriptGroup 是两处。dir 为空或目录不存在/读取异常 → 返回 []。
    """
    dir_str = (config.bettergi.dir or "").strip()
    if not dir_str:
        return None  # 未配置安装目录：端点将返回空列表

    def _read() -> list[str]:
        group_dir = Path(dir_str).expanduser() / "User" / "ScriptGroup"
        if not group_dir.is_dir():
            return []
        stems = sorted(p.stem for p in group_dir.glob("*.json") if p.is_file())
        return stems

    return _read

log = logging.getLogger("bgi_trigger")


def _show_key_window(api_key: str) -> None:
    """弹出密钥窗口供用户复制到 NAS 应用。无显示环境时退化为打印到 stderr。"""
    try:
        root = tk.Tk()
    except tk.TclError:
        # 无显示环境（无头）：退化为 stderr 输出。
        print(f"[BGI-Trigger] API key (no display): {api_key}", file=sys.stderr)
        return
    root.title("BetterGI Trigger — 密钥")
    # 注意：tkinter 部分 版本不支持 padx/pady 传元组（会报 bad screen distance），
    #       统一用标量值，垂直间距靠 pack(pady=...) 控制。
    tk.Label(root, text="NAS 应用配对时粘贴此密钥：").pack(pady=12)
    entry = tk.Entry(root, width=48, font=("Consolas", 10))
    entry.insert(0, api_key)
    entry.config(state="readonly")
    entry.pack(padx=16, pady=4)

    def _copy():
        """复制密钥到剪贴板。"""
        root.clipboard_clear()
        root.clipboard_append(api_key)

    tk.Button(root, text="复制密钥", command=_copy).pack(pady=4)
    tk.Button(root, text="关闭并启动服务", command=root.destroy).pack(pady=12)
    root.mainloop()


def main() -> int:
    """入口主函数：装配依赖并启动 uvicorn。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            # 文件日志（pythonw.exe 无控制台也能落盘，供排查）
            logging.FileHandler(
                filename=BASE_DIR / "log" / "listener.log",
                encoding="utf-8",
            ),
            # 控制台日志（有终端时，如 python.exe 开发模式）
            logging.StreamHandler(),
        ],
    )

    show_key = "--show-key" in sys.argv
    config = ListenerConfig.load(CONFIG_PATH)
    tasks = TaskRegistry(_resolve_tasks_dir(config))

    auth = AuthState(
        api_key=config.auth.api_key,
        trusted_ips=config.auth.trusted_ips,
        store_path=TRUSTED_PATH,
    )

    # 首次启动或显式要求时，弹窗显示密钥。
    first_run = not FIRST_RUN_MARKER.exists()
    if show_key or first_run:
        _show_key_window(config.auth.api_key)
        if first_run:
            FIRST_RUN_MARKER.touch()

    jobs = JobStore(keep_history=config.execution.keep_history)
    launcher = Launcher(
        bettergi_exe=config.bettergi.exe_path,
        game_processes=config.bettergi.game_processes,
        log_path=config.bettergi.log_path,
        log_done_keyword=config.bettergi.log_done_keyword,
        # ★ log_done_mode="count" → required_matches=组数(默认);
        #   log_done_mode="first" → required_matches=1(旧行为,首次命中即触发)。
        log_done_mode=getattr(config.bettergi, "log_done_mode", "count"),
        grace_seconds=config.execution.grace_seconds,
        jobs=jobs,
        # ★ job 全量日志落盘目录（取代旧版 launcher 对 listener.BASE_DIR 的反向依赖）
        log_save_dir=BASE_DIR / "log",
        # ★ 控制台日志桥：BetterGI 日志每收割一行实时回显到前台终端/文件日志
        line_sink=lambda ln: logging.getLogger("bgi_trigger.bettergi").info("%s", ln),
        # ★ /abort 时是否同时终止游戏进程
        abort_kills_game=config.execution.abort_kills_game,
        # ★ 拉起前脚本（[execution] pre_launch_script，空=禁用）：
        #   仅真正 Popen 分支前执行（handoff 不执行），失败 WARNING 不阻断；
        #   默认 runner 走 cmd.exe(shell=True) + timeout 60 + CREATE_NO_WINDOW
        pre_launch_script=getattr(config.execution, "pre_launch_script", ""),
    )

    deps = AppDeps(
        hostname=socket.gethostname(),
        version=VERSION,
        tasks=tasks,
        auth=auth,
        jobs=jobs,
        launch=launcher,
        log_path=config.bettergi.log_path,
        # ★ /stop 与 /abort 的进程清理：注入 kill_processes（内部已延迟导入 psutil）
        kill_processes=execution.kill_processes,
        # ★ /abort 主动终止本 job 拉起的 BetterGI 子进程
        terminate_current_proc=launcher.terminate_current_proc,
        # ★ /abort 时是否同时终止游戏进程（[execution] abort_kills_game，默认 True）
        abort_kills_game=config.execution.abort_kills_game,
        # ★ 进程名匹配：BetterGI exe basename + game_processes
        bettergi_name=os.path.basename(config.bettergi.exe_path or ""),
        game_processes=list(config.bettergi.game_processes),
        # ★ GET /bgi/groups 的组名枚举（bettergi.dir → User/ScriptGroup/*.json）
        bgi_groups_reader=_make_bgi_groups_reader(config),
    )
    app = create_app(deps)

    log.info("serving on %s:%d", config.server.host, config.server.port)
    # log_level=warning：屏蔽 uvicorn 默认的访问日志噪声（我们自己的日志更精确）。
    uvicorn.run(app, host=config.server.host, port=config.server.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
