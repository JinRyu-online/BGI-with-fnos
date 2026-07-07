"""NAS 端任务历史持久化。

记录每次触发任务的状态快照（job_id、task_id、state、时间戳等），
供 GUI「运行历史」面板展示。同 job_id 重复记录视为更新。
持久化到 TRIM_PKGVAR/jobs.json，重启后保留。
"""
from __future__ import annotations

import json
import os
from pathlib import Path


class HistoryStore:
    """任务历史存储：按 job_id 去重更新，保留最近 keep 条，最新在前。"""

    def __init__(self, path: Path | str, keep: int = 50) -> None:
        self._path = Path(path)
        self._keep = keep

    def _load(self) -> list[dict]:
        if not self._path.exists():
            return []
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self, items: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def record(self, entry: dict) -> None:
        """记录/更新一条历史。同 job_id 覆盖；按最新在前排序；截断到 keep 条。"""
        items = self._load()
        job_id = entry.get("job_id")
        # 去重：移除同 job_id 的旧记录
        if job_id is not None:
            items = [x for x in items if x.get("job_id") != job_id]
        items.insert(0, entry)  # 最新在前
        items = items[: self._keep]
        self._save(items)

    def all(self) -> list[dict]:
        """返回全部历史（最新在前，拷贝）。"""
        return list(self._load())


def default_history_path() -> str:
    """默认历史文件路径：TRIM_PKGVAR/jobs.json，开发回退到源码旁 var/。"""
    base = os.environ.get("TRIM_PKGVAR")
    if base:
        return str(Path(base) / "jobs.json")
    return str(Path(__file__).resolve().parent.parent / "var" / "jobs.json")
