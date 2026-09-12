"""任务编辑（PUT /tasks）+ TaskRegistry.save_all 测试。"""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from bgi_trigger.api.app import create_app, AppDeps
from bgi_trigger.service.auth import AuthState
from bgi_trigger.core.state import JobStore
from bgi_trigger.core.tasks import TaskRegistry, GUI_FILENAME

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
    # 写回了 99_gui.json（GUI_FILENAME，加载顺序上强制最后）
    gui_file = d / GUI_FILENAME
    assert gui_file.exists()
    data = json.loads(gui_file.read_text(encoding="utf-8"))
    assert data[0]["id"] == "mining"
    # 热加载生效：GUI 清单生效，且手写 daily.json 仍保留（目录模式只覆盖 GUI 文件）
    ids = sorted(t.id for t in reg.all())
    assert ids == ["daily", "mining"]


def test_save_all_directory_merges_handwritten(tmp_path):
    """目录模式：手写文件保留；同 id 时 GUI 版本（99_gui.json 最后加载）胜出。"""
    d = tmp_path / "tasks"
    d.mkdir()
    _write_task(d, _task(d))
    reg = TaskRegistry(d)
    reg.save_all([
        {"id": "mining", "display_name": "GUI挖矿", "groups": ["采矿"], "timeout_min": 60, "after_done": "none"},
        {"id": "daily", "display_name": "GUI日常", "groups": ["x"], "timeout_min": 30, "after_done": "lock"},
    ])
    by_id = {t.id: t for t in reg.all()}
    # GUI 版本的 daily 覆盖手写 daily.json 里的 daily（GUI 文件强制最后加载）
    assert by_id["daily"].display_name == "GUI日常"
    assert by_id["daily"].timeout_min == 30
    assert by_id["mining"].display_name == "GUI挖矿"


def test_gui_wins_over_filename_sorting_after_gui(tmp_path):
    """回归：手写文件名排序在 GUI 文件之后（如 zz_daily.json）时 GUI 版本仍胜出。

    对应真实 bug 形态：码点序中字母开头文件名排在数字开头（00_gui.json）
    之后，旧实现靠文件名排序导致 GUI 编辑被同 id 手写文件完全遮蔽。"""
    d = tmp_path / "tasks"
    d.mkdir()
    (d / "zz_daily.json").write_text(
        json.dumps(_task(d), ensure_ascii=False), encoding="utf-8")
    reg = TaskRegistry(d)
    reg.save_all([
        {"id": "daily", "display_name": "GUI日常", "groups": ["x"],
         "timeout_min": 30, "after_done": "lock"},
    ])
    t = reg.get("daily")
    assert t.display_name == "GUI日常"
    assert t.timeout_min == 30
    # 手写文件仍在磁盘上（GUI 只覆盖自己的文件）
    assert (d / "zz_daily.json").exists()


def test_save_all_deletes_legacy_00_gui(tmp_path):
    """save_all 写回时删除遗留的旧 00_gui.json（一次性迁移，防旧任务复活）。"""
    d = tmp_path / "tasks"
    d.mkdir()
    legacy = {"id": "old", "display_name": "旧GUI任务", "groups": ["g"],
              "timeout_min": 60, "after_done": "sleep"}
    (d / "00_gui.json").write_text(
        json.dumps([legacy], ensure_ascii=False), encoding="utf-8")
    reg = TaskRegistry(d)
    assert "old" in {t.id for t in reg.all()}

    reg.save_all([
        {"id": "new", "display_name": "n", "groups": ["g"],
         "timeout_min": 10, "after_done": "none"},
    ])
    assert not (d / "00_gui.json").exists()
    assert (d / GUI_FILENAME).exists()
    # 旧文件里的任务不再出现（防止已被 GUI 删除的任务借旧文件复活）
    assert {t.id for t in reg.all()} == {"new"}


def test_build_timeout_min_falsy_or_invalid_normalized_to_zero():
    """_build 容错归一：None/负数/非法字符串 → 0（0 = 不限，24h 兜底）。

    原实现 int(None) 抛 TypeError 而 _reload 只捕获 ValueError，会把整个
    registry 构造炸掉。"""
    build = TaskRegistry._build
    assert build({"id": "t", "groups": ["g"], "timeout_min": 0}).timeout_min == 0
    assert build({"id": "t", "groups": ["g"], "timeout_min": None}).timeout_min == 0
    assert build({"id": "t", "groups": ["g"], "timeout_min": -5}).timeout_min == 0
    assert build({"id": "t", "groups": ["g"], "timeout_min": "abc"}).timeout_min == 0
    assert build({"id": "t", "groups": ["g"]}).timeout_min == 90  # 缺省不变
    assert build({"id": "t", "groups": ["g"], "timeout_min": 45}).timeout_min == 45
    # 负数字符串同样归一为 0 而不是抛错
    assert build({"id": "t", "groups": ["g"], "timeout_min": "-3"}).timeout_min == 0


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
