"""GET /bgi/groups 端点测试：组名枚举 / reader 未注入 / 鉴权 / 空目录与读取异常。

组名来源经 deps.bgi_groups_reader 注入（DI 约定，端点层不读 config.toml）；
listener.py 的默认 reader（bettergi.dir → User/ScriptGroup/*.json stem）单独验证。
"""
import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from bgi_trigger.api.app import create_app, AppDeps
from bgi_trigger.service.auth import AuthState
from bgi_trigger.core.state import JobStore
from bgi_trigger.core.tasks import TaskRegistry
from listener import _make_bgi_groups_reader

AUTH = {"Authorization": "Bearer secret"}


def _registry() -> TaskRegistry:
    d = Path(tempfile.mkdtemp())
    (d / "daily.json").write_text(json.dumps({
        "id": "daily", "display_name": "日常一条龙",
        "groups": ["挖矿一条龙"], "timeout_min": 90, "after_done": "sleep",
    }, ensure_ascii=False), encoding="utf-8")
    return TaskRegistry(d)


def _deps(reader=None):
    return AppDeps(
        hostname="DESKTOP-TEST",
        version="1.0.0",
        tasks=_registry(),
        auth=AuthState(api_key="secret", trusted_ips=[]),
        jobs=JobStore(),
        launch=lambda job, task: None,
        bgi_groups_reader=reader,
    )


def test_bgi_groups_reader_returns_sorted_stems():
    """reader 正常注入：返回注入回调给的组名列表。"""
    client = TestClient(create_app(_deps(reader=lambda: ["关闭游戏", "日常一条龙"])))
    r = client.get("/bgi/groups", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"groups": ["关闭游戏", "日常一条龙"]}


def test_bgi_groups_reader_none_returns_empty():
    """reader 未注入（None）：返回 {"groups": []}，不 500。"""
    client = TestClient(create_app(_deps(reader=None)))
    r = client.get("/bgi/groups", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"groups": []}


def test_bgi_groups_requires_auth():
    """与 /tasks 一致：无 Bearer token → 401（不是 /key 那样的豁免端点）。"""
    client = TestClient(create_app(_deps(reader=lambda: ["组"])))
    r = client.get("/bgi/groups")
    assert r.status_code == 401
    r2 = client.get("/bgi/groups", headers={"Authorization": "Bearer wrong"})
    assert r2.status_code == 401


def test_bgi_groups_reader_exception_returns_empty():
    """reader 抛异常：端点兜底返回空列表，不让 500 拖垮调用方。"""
    def _boom():
        raise OSError("disk gone")
    client = TestClient(create_app(_deps(reader=_boom)))
    r = client.get("/bgi/groups", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"groups": []}


def test_default_reader_lists_script_group_stems(tmp_path):
    """listener.py 默认 reader：<dir>/User/ScriptGroup/*.json 的 stem 排序。"""
    cfg_path = tmp_path / "config.toml"
    bgi_dir = tmp_path / "bgi"
    group_dir = bgi_dir / "User" / "ScriptGroup"
    group_dir.mkdir(parents=True)
    (group_dir / "关闭游戏.json").write_text("{}", encoding="utf-8")
    (group_dir / "日常一条龙.json").write_text("{}", encoding="utf-8")
    (group_dir / "ignore.txt").write_text("x", encoding="utf-8")  # 非 json 不计入

    cfg_path.write_text(
        f'[bettergi]\ndir = "{str(bgi_dir).replace(chr(92), "/")}"\n',
        encoding="utf-8",
    )

    from bgi_trigger.service.config import ListenerConfig
    config = ListenerConfig.load(cfg_path)
    reader = _make_bgi_groups_reader(config)
    assert reader is not None
    # stem 排序按 Unicode 码点（"关" U+5173 < "日" U+65E5），与 locale 无关
    assert reader() == sorted(["日常一条龙", "关闭游戏"])


def test_default_reader_empty_or_missing_dir_returns_empty(tmp_path):
    """bettergi.dir 为空 → reader 本身不装配（None）；目录不存在 → 返回 []。"""
    from bgi_trigger.service.config import ListenerConfig

    cfg_path = tmp_path / "empty.toml"
    cfg_path.write_text('[bettergi]\ndir = ""\n', encoding="utf-8")
    config = ListenerConfig.load(cfg_path)
    assert _make_bgi_groups_reader(config) is None

    cfg_path2 = tmp_path / "missing.toml"
    cfg_path2.write_text(
        f'[bettergi]\ndir = "{str(tmp_path / "no-such-dir").replace(chr(92), "/")}"\n',
        encoding="utf-8",
    )
    config2 = ListenerConfig.load(cfg_path2)
    reader2 = _make_bgi_groups_reader(config2)
    assert reader2 is not None
    assert reader2() == []
