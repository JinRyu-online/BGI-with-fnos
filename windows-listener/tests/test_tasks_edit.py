"""任务编辑（PUT /tasks）+ TaskRegistry.save_all 测试。"""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from bgi_trigger.api.app import create_app, AppDeps
from bgi_trigger.service.auth import AuthState
from bgi_trigger.core.state import JobStore
from bgi_trigger.core.tasks import TaskRegistry

AUTH = {"Authorization": "Bearer secret"}


def _deps(tasks_dir: Path, launch=None) -> AppDeps:
    return AppDeps(
        hostname="DESKTOP-TEST",
        version="1.0.0",
        tasks=TaskRegistry(tasks_dir),
        auth=AuthState(api_key="secret", trusted_ips=[]),
        jobs=JobStore(),
        launch=launch or (lambda job, task: None),
    )


def _write_task(d: Path, task: dict) -> None:
    (d / "daily.json").write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")


def _task(d: Path) -> dict:
    return {"id": "daily", "display_name": "日常一条龙",
            "groups": ["日常一条龙", "关闭游戏"], "timeout_min": 90, "after_done": "sleep"}


def test_save_all_writes_file_and_hot_reloads(tmp_path):
    d = tmp_path / "tasks"
    d.mkdir()
    _write_task(d, _task(d))
    reg = TaskRegistry(d)
    assert len(reg.all()) == 1

    reg.save_all([
        {"id": "mining", "display_name": "挖矿一条龙", "groups": ["采矿"], "timeout_min": 60, "after_done": "shutdown"},
    ])
    # 写回了 00_gui.json
    gui_file = d / "00_gui.json"
    assert gui_file.exists()
    data = json.loads(gui_file.read_text(encoding="utf-8"))
    assert data[0]["id"] == "mining"
    # 热加载生效：GUI 清单生效，且手写 daily.json 仍保留（目录模式只覆盖 00_gui.json）
    ids = sorted(t.id for t in reg.all())
    assert ids == ["daily", "mining"]


def test_save_all_directory_merges_handwritten(tmp_path):
    """目录模式：手写文件保留；同 id 时手写文件覆盖 GUI 版本（后加载胜出）。"""
    d = tmp_path / "tasks"
    d.mkdir()
    _write_task(d, _task(d))
    reg = TaskRegistry(d)
    reg.save_all([
        {"id": "mining", "display_name": "GUI挖矿", "groups": ["采矿"], "timeout_min": 60, "after_done": "none"},
        {"id": "daily", "display_name": "GUI日常", "groups": ["x"], "timeout_min": 30, "after_done": "lock"},
    ])
    by_id = {t.id: t for t in reg.all()}
    # 手写 daily.json 里的 daily 覆盖 GUI 版本
    assert by_id["daily"].display_name == "日常一条龙"
    assert by_id["mining"].display_name == "GUI挖矿"


def test_save_all_invalid_rejects_and_keeps_old(tmp_path):
    d = tmp_path / "tasks"
    d.mkdir()
    _write_task(d, _task(d))
    reg = TaskRegistry(d)
    before = [t.to_dict() for t in reg.all()]
    try:
        reg.save_all([{"id": "bad", "groups": []}])  # groups 空 → ValueError
        assert False, "should raise"
    except ValueError:
        pass
    # 原文件未被破坏
    assert [t.to_dict() for t in reg.all()] == before


def test_put_tasks_endpoint_roundtrip(tmp_path):
    d = tmp_path / "tasks"
    d.mkdir()
    _write_task(d, _task(d))
    client = TestClient(create_app(_deps(d)))

    r = client.put("/tasks", headers=AUTH, json={"tasks": [
        {"id": "mining", "display_name": "挖矿", "groups": ["采矿"], "timeout_min": 45, "after_done": "sleep"},
    ]})
    assert r.status_code == 200, r.text
    # 返回合并结果（含手写 daily.json 保留的任务）
    assert sorted(t["id"] for t in r.json()) == ["daily", "mining"]
    # GET 反映新清单
    assert sorted(t["id"] for t in client.get("/tasks", headers=AUTH).json()) == ["daily", "mining"]


def test_put_tasks_invalid_returns_400(tmp_path):
    d = tmp_path / "tasks"
    d.mkdir()
    _write_task(d, _task(d))
    client = TestClient(create_app(_deps(d)))
    r = client.put("/tasks", headers=AUTH, json={"tasks": [
        {"id": "bad", "groups": []},
    ]})
    assert r.status_code == 400


def test_put_tasks_requires_auth(tmp_path):
    d = tmp_path / "tasks"
    d.mkdir()
    _write_task(d, _task(d))
    client = TestClient(create_app(_deps(d)))
    r = client.put("/tasks", json={"tasks": []})
    assert r.status_code == 401


def test_put_tasks_file_mode_rewrites_source(tmp_path):
    """文件模式（单 tasks.json）：直接重写该文件。"""
    f = tmp_path / "tasks.json"
    f.write_text(json.dumps([_task(tmp_path)], ensure_ascii=False), encoding="utf-8")
    reg = TaskRegistry(f)
    reg.save_all([{"id": "solo", "display_name": "s", "groups": ["g"], "timeout_min": 10, "after_done": "lock"}])
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data[0]["id"] == "solo"
