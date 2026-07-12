import json
import time
from pathlib import Path

import pytest

from bgi_trigger.core.tasks import Task, TaskRegistry, TaskNotFound


def _task(id="daily", groups=None, **kw):
    return {"id": id, "display_name": kw.get("display_name", id),
            "groups": groups or [id, "关闭游戏"], **{k: v for k, v in kw.items() if k != "display_name"}}


def write(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


# ---------- 单文件模式（向后兼容）----------

def test_load_single_file_array_form(tmp_path):
    path = tmp_path / "tasks.json"
    write(path, [_task("daily", ["日常一条龙", "关闭游戏"])])

    reg = TaskRegistry(path)

    t = reg.get("daily")
    assert t.id == "daily"
    assert t.groups == ["日常一条龙", "关闭游戏"]


def test_load_single_file_object_form(tmp_path):
    path = tmp_path / "tasks.json"
    write(path, _task("daily"))

    reg = TaskRegistry(path)
    assert reg.get("daily").id == "daily"


def test_unknown_id_raises(tmp_path):
    reg = TaskRegistry(tmp_path / "tasks.json")  # 文件不存在 -> 空清单
    with pytest.raises(TaskNotFound):
        reg.get("nope")


def test_per_task_overrides(tmp_path):
    path = tmp_path / "tasks.json"
    write(path, [_task("daily", timeout_min=45, after_done="shutdown")])

    t = TaskRegistry(path).get("daily")
    assert t.timeout_min == 45
    assert t.after_done == "shutdown"


def test_invalid_task_is_skipped_not_raised(tmp_path):
    # 热加载场景下，非法任务定义应被跳过（记日志），不整体失败——
    # 这样编辑文件中途存了非法 JSON 不会让 /tasks 整个挂掉
    path = tmp_path / "tasks.json"
    write(path, [{"id": "bad", "display_name": "bad"}])  # 缺 groups
    reg = TaskRegistry(path)
    assert reg.all() == []  # 非法任务被跳过，清单为空


def test_invalid_task_among_valid_skips_only_invalid(tmp_path):
    d = tmp_path / "tasks.d"
    d.mkdir()
    write(d / "good.json", _task("daily"))
    write(d / "bad.json", {"id": "bad", "display_name": "bad"})  # 缺 groups

    reg = TaskRegistry(d)
    assert {t.id for t in reg.all()} == {"daily"}  # bad 被跳过，good 保留


# ---------- 目录模式 ----------

def test_load_directory_multiple_files(tmp_path):
    d = tmp_path / "tasks.d"
    d.mkdir()
    write(d / "daily.json", _task("daily", ["日常一条龙", "关闭游戏"]))
    write(d / "abyss.json", _task("abyss", ["深渊", "关闭游戏"]))

    reg = TaskRegistry(d)
    ids = {t.id for t in reg.all()}
    assert ids == {"daily", "abyss"}


def test_directory_file_with_array(tmp_path):
    d = tmp_path / "tasks.d"
    d.mkdir()
    # 一个文件里放数组也支持
    write(d / "all.json", [_task("daily"), _task("abyss")])

    reg = TaskRegistry(d)
    assert {t.id for t in reg.all()} == {"daily", "abyss"}


def test_empty_directory_returns_empty(tmp_path):
    d = tmp_path / "tasks.d"
    d.mkdir()
    assert TaskRegistry(d).all() == []


def test_nonexistent_path_returns_empty(tmp_path):
    assert TaskRegistry(tmp_path / "nope").all() == []


# ---------- 热加载 ----------

def test_hot_reload_detects_file_modify(tmp_path):
    path = tmp_path / "tasks.json"
    write(path, [_task("daily")])
    reg = TaskRegistry(path)
    assert {t.id for t in reg.all()} == {"daily"}

    time.sleep(0.01)
    write(path, [_task("daily"), _task("abyss")])

    # 不重建 registry，all() 自动感知变化
    assert {t.id for t in reg.all()} == {"daily", "abyss"}


def test_hot_reload_detects_file_added_in_dir(tmp_path):
    d = tmp_path / "tasks.d"
    d.mkdir()
    write(d / "daily.json", _task("daily"))
    reg = TaskRegistry(d)
    assert {t.id for t in reg.all()} == {"daily"}

    time.sleep(0.01)
    write(d / "abyss.json", _task("abyss"))

    assert {t.id for t in reg.all()} == {"daily", "abyss"}


def test_hot_reload_detects_file_deleted_in_dir(tmp_path):
    d = tmp_path / "tasks.d"
    d.mkdir()
    write(d / "daily.json", _task("daily"))
    write(d / "abyss.json", _task("abyss"))
    reg = TaskRegistry(d)
    assert {t.id for t in reg.all()} == {"daily", "abyss"}

    time.sleep(0.01)
    (d / "abyss.json").unlink()

    assert {t.id for t in reg.all()} == {"daily"}


def test_hot_reload_reflects_get(tmp_path):
    path = tmp_path / "tasks.json"
    write(path, [_task("daily")])
    reg = TaskRegistry(path)

    time.sleep(0.01)
    write(path, [_task("wood")])  # daily 被替换为 wood

    with pytest.raises(TaskNotFound):
        reg.get("daily")
    assert reg.get("wood").id == "wood"


def test_load_classmethod_still_works(tmp_path):
    # 向后兼容：旧 API TaskRegistry.load(path) 仍可用
    path = tmp_path / "tasks.json"
    write(path, [_task("daily")])
    reg = TaskRegistry.load(path)
    assert reg.get("daily").id == "daily"
