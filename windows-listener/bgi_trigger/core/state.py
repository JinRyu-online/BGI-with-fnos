"""任务状态机模块（单槽模型）。

同一时刻最多只有一个任务在跑（避免两个 BetterGI 实例抢桌面/抢游戏窗口）。
生命周期：

    idle --start--> running --mark_completing--> completing --finalize--> done/timeout/failed
                                              | --abort--> aborted

- running        BetterGI 已拉起，正在执行
- completing     B/C 任一完成判定命中，进入反悔窗口（grace_seconds），期间可 abort
- done/timeout/failed/aborted  终态，释放槽位（current -> None），但保留进历史

历史保留最近 keep_history 条，供 /status 查询与 NAS 端展示。
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class JobState(str, Enum):
    """任务状态枚举。"""
    RUNNING = "running"
    COMPLETING = "completing"
    DONE = "done"
    TIMEOUT = "timeout"
    FAILED = "failed"
    ABORTED = "aborted"


# 终态集合：处于这些状态时，槽位视为空闲，可接受新任务。
_TERMINAL = {JobState.DONE, JobState.TIMEOUT, JobState.FAILED, JobState.ABORTED}


class JobBusy(Exception):
    """已有任务在跑，无法启动新任务。"""


@dataclass
class Job:
    """一次任务执行的运行时记录。"""
    id: str
    task_id: str
    groups: list[str]
    state: JobState = JobState.RUNNING
    completion_reason: str = ""   # 完成判定的命中原因：game_exited / log_keyword / timeout / error
    log_lines: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)   # 任务启动时间(Unix time)
    finished_at: float | None = None                       # 进入终态的时间;未完成则为 None
    log_path: str = ""                                     # BetterGI 日志文件路径(空=不收割)

    def to_dict(self) -> dict:
        """序列化为 JSON 友好字典（供 /status 接口返回）。"""
        now = time.time()
        return {
            "id": self.id,
            "task_id": self.task_id,
            "groups": list(self.groups),
            "state": self.state.value,
            "completion_reason": self.completion_reason,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "elapsed": (self.finished_at or now) - self.created_at,
        }


class JobStore:
    """单槽任务存储：维护当前任务 + 历史。

    并发安全:_lock 保护 _current / _history / _abort_event,_start 等字段。
    abort 通过 Event 通知守护线程快速退出,避免新 /trigger 在 grace 窗口
    内覆盖槽位导致并发运行。
    """

    def __init__(self, keep_history: int = 20) -> None:
        self._lock = threading.Lock()
        self._current: Job | None = None
        self._history: list[Job] = []
        self._keep = keep_history
        # abort 信号:被 abort() 置位,守护线程在 grace 窗口检查它;
        # start() 时复位,保证新任务开始时 abort 信号干净。
        self._abort_event = threading.Event()

    @property
    def current(self) -> Job | None:
        """当前任务（可能为终态但尚未 clear）。"""
        with self._lock:
            return self._current

    def abort_requested(self) -> bool:
        """守护线程查询:当前任务是否收到 abort 信号。"""
        return self._abort_event.is_set()

    def is_idle(self) -> bool:
        """槽位是否空闲（无任务或当前任务已进入终态）。"""
        with self._lock:
            return self._current is None or self._current.state in _TERMINAL

    def start(self, task_id: str, groups: list[str]) -> Job:
        """启动新任务。若槽位忙，抛 JobBusy。复位 abort 信号。"""
        with self._lock:
            if self._current is not None and self._current.state not in _TERMINAL:
                raise JobBusy(self._current.id)
            job = Job(id=uuid.uuid4().hex[:12], task_id=task_id, groups=list(groups))
            self._current = job
            self._abort_event.clear()     # ★ 新任务开始前复位 abort 信号
            return job

    def mark_completing(self, reason: str) -> None:
        """标记当前任务进入完成反悔窗口，记录命中原因。仅 running 状态有效。"""
        with self._lock:
            if self._current is None or self._current.state != JobState.RUNNING:
                return
            self._current.completion_reason = reason
            self._current.state = JobState.COMPLETING

    def finalize(self, state: JobState) -> None:
        """将当前任务置为终态并归档进历史（按 keep_history 截断）。自动记录 finished_at。"""
        with self._lock:
            if self._current is None:
                return
            self._current.state = state
            self._current.finished_at = time.time()
            self._history.append(self._current)
            if len(self._history) > self._keep:
                self._history = self._history[-self._keep:]
            # 槽位保持 current 直到下次 start(会覆盖)或 clear_current。
            # abort 后若直接覆盖,守护线程仍在跑 → 由 launcher 通过 abort_requested 自行退出。

    def abort(self) -> None:
        """中止当前任务:置信号 → 标记状态 → 归档 → 清槽位。"""
        with self._lock:
            if self._current is None:
                return
            self._abort_event.set()        # ★ 通知守护线程立即退出
            self._current.state = JobState.ABORTED
            self._current.finished_at = time.time()
            self._history.append(self._current)
            if len(self._history) > self._keep:
                self._history = self._history[-self._keep:]
            self._current = None           # ★ 立即释放槽位,避免 /trigger 等待

    def clear_current(self) -> None:
        """清空当前槽位（历史不受影响）。"""
        with self._lock:
            self._current = None

    def get(self, job_id: str) -> Job | None:
        """按 id 查任务：先查当前，再查历史。找不到返回 None。"""
        with self._lock:
            if self._current and self._current.id == job_id:
                return self._current
            for j in reversed(self._history):
                if j.id == job_id:
                    return j
            return None

    def history(self) -> list[Job]:
        """返回历史任务列表（拷贝）。"""
        with self._lock:
            return list(self._history)
