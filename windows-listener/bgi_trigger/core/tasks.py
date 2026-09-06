"""任务清单模块（唯一真相源，支持目录热加载）。

任务来源可以是单个文件（如 tasks.json），也可以是一个目录（如 tasks.d/）：
- 目录模式：读取目录下所有 *.json，每个文件可以是单个任务对象或任务数组，合并。
- 文件模式：读取该文件，单个对象或数组。

热加载：每次访问 all()/get() 时，基于文件 mtime 签名判断是否有变动，变了才重读。
因此修改任务文件后无需重启监听器，下一次 /tasks 请求即生效。

BetterGI 命令行只能跑调度器组（--startGroups），无法直接指定单个 JS 脚本，
因此任务的核心字段是 groups（组名列表），须与 BetterGI「全自动-调度器」组名一致。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("bgi_trigger.tasks")

# 每任务的默认值（与 config.execution 默认值保持一致，
# 使「无覆盖」的任务行为与全局默认相同）。
DEFAULT_TIMEOUT_MIN = 90
DEFAULT_AFTER_DONE = "sleep"
# 合法的收尾动作枚举。
VALID_AFTER_DONE = {"sleep", "shutdown", "lock", "none"}


class TaskNotFound(Exception):
    """请求的任务 id 不在清单中。"""


@dataclass(frozen=True)
class Task:
    """单个可触发任务。

    id           任务唯一标识，/trigger 时引用
    display_name 在 NAS GUI 上显示的名称
    groups       BetterGI 调度器组名列表（按顺序执行）
    timeout_min  本任务最大执行时长（分钟）
    after_done   本任务收尾动作
    """
    id: str
    display_name: str
    groups: list[str]
    timeout_min: int
    after_done: str

    def to_dict(self) -> dict:
        """序列化为 JSON 友好的字典（供 /tasks 接口返回）。"""
        return {
            "id": self.id,
            "display_name": self.display_name,
            "groups": list(self.groups),
            "timeout_min": self.timeout_min,
            "after_done": self.after_done,
        }


class TaskRegistry:
    """任务清单：从文件或目录加载，按 id 查询，支持 mtime 热加载。"""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._signature: tuple | None = None
        self._by_id: dict[str, Task] = {}
        self._reload()  # 首次构造立即加载

    @classmethod
    def load(cls, path: Path | str) -> "TaskRegistry":
        """向后兼容的类方法入口，等价于 TaskRegistry(path)。"""
        return cls(path)

    # ---- 热加载 ----

    def _current_signature(self) -> tuple:
        """当前来源的签名：目录=各 *.json 的 (文件名, mtime)；文件=(文件名, mtime)。

        签名变化（内容改、增删文件）即触发重读。"""
        if self._path.is_dir():
            files = sorted(self._path.glob("*.json"))
            return tuple((f.name, f.stat().st_mtime) for f in files)
        if self._path.exists():
            p = self._path
            return ((p.name, p.stat().st_mtime),)
        return ()

    def _maybe_reload(self) -> None:
        """签名变了才重读，否则用内存缓存。每次 all()/get() 调用。"""
        sig = self._current_signature()
        if sig != self._signature:
            self._signature = sig
            self._reload()

    def _reload(self) -> None:
        """重新加载全部任务。目录模式下单个文件解析失败则跳过（记日志），不整体失败。"""
        by_id: dict[str, Task] = {}
        for item in self._iter_raw_items():
            try:
                t = self._build(item)
            except ValueError as e:
                log.warning("跳过非法任务定义：%s", e)
                continue
            by_id[t.id] = t
        self._by_id = by_id

    def _iter_raw_items(self):
        """枚举原始任务字典：目录=各 *.json 合并；文件=该文件。支持对象或数组。"""
        if self._path.is_dir():
            for f in sorted(self._path.glob("*.json")):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError) as e:
                    log.warning("跳过无法解析的任务文件 %s：%s", f, e)
                    continue
                yield from _normalize(data)
        elif self._path.exists():
            data = json.loads(self._path.read_text(encoding="utf-8"))
            yield from _normalize(data)

    @staticmethod
    def _build(item: dict) -> Task:
        """校验并构造单个 Task 对象。"""
        if "id" not in item or not item["id"]:
            raise ValueError(f"task missing id: {item!r}")
        groups = item.get("groups")
        if not groups or not isinstance(groups, list):
            raise ValueError(f"task {item.get('id')!r} missing non-empty 'groups'")
        after_done = item.get("after_done", DEFAULT_AFTER_DONE)
        if after_done not in VALID_AFTER_DONE:
            raise ValueError(f"task {item['id']!r} invalid after_done: {after_done!r}")
        return Task(
            id=item["id"],
            display_name=item.get("display_name", item["id"]),
            groups=list(groups),
            timeout_min=int(item.get("timeout_min", DEFAULT_TIMEOUT_MIN)),
            after_done=after_done,
        )

    def get(self, task_id: str) -> Task:
        """按 id 查任务（先热加载检查），找不到抛 TaskNotFound。"""
        self._maybe_reload()
        try:
            return self._by_id[task_id]
        except KeyError:
            raise TaskNotFound(task_id)

    def all(self) -> list[Task]:
        """返回全部任务（先热加载检查，供 /tasks 接口）。"""
        self._maybe_reload()
        return list(self._by_id.values())

    # ---- 写回（NAS GUI 编辑任务用）----

    def _write_path(self) -> Path:
        """写回目标文件。目录模式约定写 00_gui.json（排最前，人工编辑的其他文件可覆盖其任务）；
        文件模式直接写该文件。"""
        if self._path.is_dir():
            return self._path / "00_gui.json"
        return self._path

    def save_all(self, tasks: list[dict]) -> list[Task]:
        """整体替换任务清单并写回磁盘（先全部校验，任一非法则抛 ValueError 不落盘）。

        目录模式只覆盖本模块写出的 00_gui.json——用户手写的其他 *.json 保留。
        加载顺序为文件名序：00_gui.json 先加载，手写文件后加载；同 id 时手写
        文件覆盖 GUI 版本（后写胜出，与 _reload 的 by_id 覆盖顺序一致）。
        返回写回后的完整任务列表（含手写文件里的任务）。
        """
        validated = [self._build(item) for item in tasks]  # 任一非法抛 ValueError
        write_path = self._write_path()
        write_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [t.to_dict() for t in validated]
        write_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._signature = None  # 强制下轮访问重读（含手写文件合并结果）
        self._maybe_reload()
        return list(self._by_id.values())


def _normalize(data):
    """把单个对象或数组统一成迭代器。"""
    if isinstance(data, list):
        yield from data
    else:
        yield data
