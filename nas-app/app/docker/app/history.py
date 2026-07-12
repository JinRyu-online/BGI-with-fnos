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

    def record(self, entry: dict, *, keep_created_at: bool = False) -> None:
        """记录/更新一条历史。同 job_id 覆盖；按最新在前排序；截断到 keep 条。

        keep_created_at=True: 终态更新保留最早的 created_at(触发时刻)不被覆盖,
        并保留已存在的 display_name / finished_at,让前端能准确计算耗时并显示任务名称。
        """
        items = self._load()
        job_id = entry.get("job_id")
        prev = None
        if job_id is not None:
            for x in items:
                if x.get("job_id") == job_id:
                    prev = x
                    break
            items = [x for x in items if x.get("job_id") != job_id]
        # 合并:entry 新值优先;缺失字段从 prev 补充
        merged = dict(entry)
        if prev is not None:
            if keep_created_at and "created_at" not in merged:
                merged["created_at"] = prev.get("created_at")
            if "finished_at" not in merged:
                merged["finished_at"] = prev.get("finished_at")  # 保留终态时间
            if "display_name" not in merged:
                merged["display_name"] = prev.get("display_name")  # ★ 触发时的显示名必须保留
        items.insert(0, merged)  # 最新在前
        items = items[: self._keep]
        self._save(items)

    def all(self) -> list[dict]:
        """返回全部历史（最新在前，拷贝）。"""
        return list(self._load())


def default_history_path() -> str:
    """默认历史文件路径：BGI_DATA_DIR/jobs.json（容器内由 compose 注入 /data）；
    开发环境回退到源码旁的 var/jobs.json。"""
    base = os.environ.get("BGI_DATA_DIR")
    if base:
        return str(Path(base) / "jobs.json")
    return str(Path(__file__).resolve().parent.parent / "var" / "jobs.json")
