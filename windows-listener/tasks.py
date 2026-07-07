"""任务清单模块（唯一真相源）。

tasks.json 把「任务 id」映射到 BetterGI 调度器组名及每任务覆盖参数
（超时、收尾动作）。监听器启动时加载并校验；NAS 端通过 /tasks 接口
只读拉取，渲染成触发按钮。

BetterGI 命令行只能跑调度器组（--startGroups），无法直接指定单个
JS 脚本，因此任务清单的核心字段是 groups（组名列表），组名必须与
BetterGI「全自动-调度器」中配置的组名完全一致。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

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
    """任务清单：从 tasks.json 加载，按 id 查询。"""

    def __init__(self, tasks: list[Task]) -> None:
        self._by_id = {t.id: t for t in tasks}

    @classmethod
    def load(cls, path: Path | str) -> "TaskRegistry":
        """从磁盘加载 tasks.json 并逐条校验，任一校验失败抛 ValueError。"""
        path = Path(path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        tasks = [cls._build(item) for item in raw]
        return cls(tasks)

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
        """按 id 查任务，找不到抛 TaskNotFound。"""
        try:
            return self._by_id[task_id]
        except KeyError:
            raise TaskNotFound(task_id)

    def all(self) -> list[Task]:
        """返回全部任务（供 /tasks 接口）。"""
        return list(self._by_id.values())
