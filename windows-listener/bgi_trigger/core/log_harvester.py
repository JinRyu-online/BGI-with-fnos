"""BetterGI 文件日志的实时收割器(同步 + 异步双接口)。

设计:
  - LogHarvester 为每个 job 在 Launcher 启动时实例化,持有一个后台线程按行读取
    BetterGI 的日志文件;新行追加到固定长度 deque(最近 N 条),并通知等待者。
  - WebSocket 处理器读 deque 取历史 + asyncio.Event 等新行;任一条件触发时推送。
  - 文件读取使用轮询 + seek,不依赖任何平台特有的文件变更通知(APIs cross-platform)。

线程安全:
  - 所有共享数据受 threading.Lock 保护。
  - 异步接口用 `asyncio.run_coroutine_threadsafe` 把同步收割线程的新行推送到
    运行在 uvicorn asyncio event loop 的 WebSocket 消费者。
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Deque

log = logging.getLogger("bgi_trigger.log_harvester")

# 每个 job 日志 deque 保留最大条数(客户端只展示最近几条,但这里留余量)
_DEFAULT_MAX_LINES = 50
_POLL_INTERVAL_SEC = 0.5


class LogHarvester:
    """单个 job 的日志收割器。

    用法:
        h = LogHarvester(job_id="abc", log_path="C:/path/to.log", max_lines=50)
        h.start()                       # 启动后台收割线程
        recent = h.recent(n=3)          # 取最近 n 条
        await h.wait_new(timeout=30)    # 异步等新行到达
        h.stop()                        # 任务完成后停止收割

    save_path (★ 新参数,可选): 收割到的日志行同步写入的全量文件路径。
        用于事后排查：把 WS 收錄到的所有行都落盘保留,文件名通常为
        {bettergi日志目录}/{job_id}.log。None 表示不落盘(默认)。
    """

    def __init__(
        self,
        job_id: str,
        log_path: str | os.PathLike,
        max_lines: int = _DEFAULT_MAX_LINES,
        poll_interval: float = _POLL_INTERVAL_SEC,
        save_path: str | os.PathLike | None = None,
    ) -> None:
        """
        log_path 支持两种形态:
          1) 具体路径: "C:/path/to.log"
          2) glob 模式(推荐,按天自动发现): "C:/Software/BetterGI/log/better-genshin-impact*.log"
             内部自动取匹配结果中 mtime 最新的文件。
        """
        self.job_id = job_id
        self._glob_pattern: str | None = None
        p = Path(log_path)
        # ★ 路径含通配符 → 改为 glob 匹配
        if any(ch in str(p) for ch in "*?["):
            self._glob_pattern = str(log_path)
            matches = sorted(self._glob().parent.glob(self._glob().name),
                             key=lambda f: f.stat().st_mtime, reverse=True)
            self._path = matches[0] if matches else p
            if len(matches) > 1:
                log.info("harvester for job %s: %d matches, chose newest %s",
                         self.job_id, len(matches), self._path)
        else:
            self._path = p
        self._max = max_lines
        self._poll = poll_interval

        self._lock = threading.Lock()
        self._buf: Deque[str] = deque(maxlen=max_lines)   # 最近 N 行原始文本
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._ready_evt = threading.Event()   # ★ 新增:进入主收割后置位,供测试同步

        # ★ 异步桥:把同步收割线程的"新行到达"通知迁移到 asyncio event loop
        self._loop: asyncio.AbstractEventLoop | None = None
        self._async_new = asyncio.Event()     # run_coroutine_threadsafe 触发
        self._finished: bool = False          # 任务是否已结束

        # ★ 新:全量日志落盘
        self._save_path: Path | None = Path(save_path) if save_path else None
        self._save_file = None                # 懶加载：首次有数据时打开

    def _glob(self) -> Path:
        return Path(self._glob_pattern)

    # ---------- 同步接口(Launcher 线程调用) ----------

    def start(self) -> None:
        """启动后台守护线程,立即返回。重复调用幂等。"""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_evt.clear()
            self._thread = threading.Thread(target=self._run, daemon=True, name=f"log-harvest-{self.job_id}")
            self._thread.start()
            log.info("harvester started for job %s → %s", self.job_id, self._path)

    def stop(self) -> None:
        """停止收割线程。阻塞至线程退出(超时 2s)。"""
        self._stop_evt.set()
        t = self._thread
        if t is not None:
            t.join(timeout=2.0)
            if t.is_alive():
                log.warning("harvester thread for job %s did not exit in time", self.job_id)
        self._thread = None

    def recent(self, n: int = 3) -> list[str]:
        """取最近 n 条原始日志行。"""
        with self._lock:
            return list(self._buf)[-n:]

    def wait_for_lines(self, min_count: int = 1, timeout: float = 5.0) -> list[str]:
        """阻塞直到收割到至少 min_count 行（同步，阻塞调用线程）。

        用于测试/集成场景，避免 sleep 硬编码时序：
        每隔 self._poll 秒检查缓冲条数，超时返回当前已收割的行。
        """
        deadline = time.monotonic() + timeout
        while True:
            buf = self.recent(min_count)
            if len(buf) >= min_count:
                return buf
            if time.monotonic() >= deadline:
                return buf
            time.sleep(self._poll)

    def check_keyword(self, keyword: str) -> tuple[bool, str]:
        """在 recent 缓冲里做 keyword 子串匹配（从新往旧）。

        返回 (是否命中, 匹配行原文)。keyword 为空时返回 (False, "")。
        复用 self._buf(受 threading.Lock 保护),不再 open 文件。
        """
        if not keyword:
            return False, ""
        with self._lock:
            for line in reversed(self._buf):   # 从新往旧找,命中即返回
                if keyword in line:
                    return True, line
        return False, ""

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """注册 asyncio event loop,以便 __aiter__ 能 bridge 线程事件。"""
        self._loop = loop

    def mark_finished(self) -> None:
        """标记任务已完成:停止唤醒等待者,让 WS 最后一次推 last=True。"""
        self._finished = True
        self._fire_async()

    def wait_for_ready(self, timeout: float = 5.0) -> bool:
        """阻塞等到后台收割线程进入主循环（seek 完成,开始首轮 poll）。
        用于测试/集成场景同步,避免启动时序竞争：
        测试写入前 wait_for_ready() → harvester 正在 sleep 等下一 poll →
        写入的数据必然被下一 poll 收割到。"""
        return self._ready_evt.wait(timeout=timeout)

    def _append_to_savefile(self, lines: list[str]) -> None:
        """把收割到的行同步写到 save_path（懶加载文件句柄,append 模式）。"""
        if self._save_path is None or not lines:
            return
        try:
            if self._save_file is None:
                # ★ 首次有数据：打开文件（append + line buffered），顺便建目录
                self._save_path.parent.mkdir(parents=True, exist_ok=True)
                self._save_file = open(self._save_path, "a", encoding="utf-8",
                                       buffering=1)   # line buffered
                log.info("harvester for job %s: logging to %s",
                         self.job_id, self._save_path)
            for ln in lines:
                self._save_file.write(ln + "\n")
        except Exception:
            log.warning("harvester for job %s: failed to write savefile %s",
                        self.job_id, self._save_path, exc_info=True)

    def _run(self) -> None:
        """后台线程入口:轮询文件 → 读取新行 → 更新 buffer → 通知等待者 → 落盘。"""
        # 等待日志文件首次出现(BetterGI 启动后可能需几百毫秒产生)
        deadline = time.monotonic() + 10.0
        while not self._stop_evt.is_set() and time.monotonic() < deadline:
            if self._path.exists():
                break
            time.sleep(self._poll)

        if not self._path.exists():
            log.warning("log file never appeared for job %s: %s", self.job_id, self._path)
            return

        try:
            with open(self._path, "r", encoding="utf-8", errors="replace") as f:
                # ★ 从文件末尾开始(不收割启动前的历史)
                f.seek(0, os.SEEK_END)
                # ★ 进入主循环：置位 ready（写入位置 = harvester 正在 sleep 等下一 poll）
                self._ready_evt.set()

                # 行级收割:保留上一个 chunk 末尾未换行的残行,避免跨块切断
                pending = ""
                while not self._stop_evt.is_set():
                    chunk = f.read(64 * 1024)
                    if chunk:
                        chunk = pending + chunk
                        lines = chunk.splitlines()
                        # 最后一段若无换行符,属于未完成的行,留到下个 chunk
                        if lines and not chunk.endswith("\n"):
                            pending = lines.pop()
                        else:
                            pending = ""
                        new_lines = [ln for ln in lines if ln.strip()]
                        if new_lines:
                            with self._lock:
                                self._buf.extend(new_lines)
                            self._fire_async()
                            # ★ 新:收割到的行同步落盘
                            self._append_to_savefile(new_lines)
                    time.sleep(self._poll)   # ★ 在 if 外:无新数据时也 sleep,避免 busy-spin
        except Exception:
            log.exception("harvester read error for job %s", self._job_id)
        finally:
            # ★ 收割线程退出时关闭落盘文件
            if self._save_file is not None:
                try:
                    self._save_file.close()
                except Exception:
                    pass

    def _fire_async(self) -> None:
        """线程安全:把"新行到达"桥接到 asyncio。"""
        if self._loop is None:
            return
        try:
            # run_coroutine_threadsafe:从同步线程安全地 set asyncio.Event
            asyncio.run_coroutine_threadsafe(self._async_new_set(), self._loop)
        except RuntimeError:
            pass  # event loop 已关闭

    async def _async_new_set(self) -> None:
        self._async_new.set()
        self._async_new.clear()

    # ---------- 异步接口(WS handler 调用) ----------

    async def wait_new(self, timeout: float | None = None) -> bool:
        """等待新行到达或超时。返回 True=有新数据,False=超时或停止。"""
        if self._stop_evt.is_set() and len(asyncio.all_tasks()) > 0:
            return bool(self._buf)
        try:
            await asyncio.wait_for(self._async_new.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    @property
    def finished(self) -> bool:
        return getattr(self, "_finished", False)
