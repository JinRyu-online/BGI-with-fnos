import json

import pytest

from tasks import Task, TaskRegistry, TaskNotFound


def _task(id="daily", groups=None, **kw):
    return {"id": id, "display_name": kw.get("display_name", id),
            "groups": groups or [id, "关闭游戏"], **{k: v for k, v in kw.items() if k != "display_name"}}


def test_load_tasks_and_lookup_by_id(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps([_task("daily", ["日常一条龙", "关闭游戏"])]), encoding="utf-8")

    reg = TaskRegistry.load(path)

    t = reg.get("daily")
    assert isinstance(t, Task)
    assert t.id == "daily"
    assert t.display_name == "daily"
    assert t.groups == ["日常一条龙", "关闭游戏"]
    assert t.timeout_min == 90          # default from config defaults
    assert t.after_done == "sleep"      # default


def test_unknown_id_raises(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps([_task("daily")]), encoding="utf-8")
    reg = TaskRegistry.load(path)

    with pytest.raises(TaskNotFound):
        reg.get("nope")


def test_all_returns_every_task(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps([_task("daily"), _task("abyss", ["深渊", "关闭游戏"])]), encoding="utf-8")

    reg = TaskRegistry.load(path)

    ids = {t.id for t in reg.all()}
    assert ids == {"daily", "abyss"}


def test_per_task_overrides(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps([
        _task("daily", timeout_min=45, after_done="shutdown")
    ]), encoding="utf-8")

    reg = TaskRegistry.load(path)
    t = reg.get("daily")

    assert t.timeout_min == 45
    assert t.after_done == "shutdown"


def test_invalid_task_missing_groups_rejected(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps([{"id": "bad", "display_name": "bad"}]), encoding="utf-8")

    with pytest.raises(ValueError):
        TaskRegistry.load(path)
