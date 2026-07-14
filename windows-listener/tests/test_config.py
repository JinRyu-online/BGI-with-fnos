import tomllib
from pathlib import Path

from bgi_trigger.service.config import ListenerConfig


def write_toml(path: Path, data: dict) -> None:
    """Helper: minimal TOML writer for test fixtures (flat + [section] only)."""
    import json
    lines = []
    for section, kv in data.items():
        lines.append(f"[{section}]")
        for k, v in kv.items():
            if isinstance(v, str):
                # 用 json.dumps 处理转义（Windows 路径的 \ 等）
                lines.append(f"{k} = {json.dumps(v)}")
            elif isinstance(v, list):
                lines.append(f"{k} = []")  # empty list for fixtures
            else:
                lines.append(f"{k} = {v}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def test_load_applies_defaults_for_missing_fields(tmp_path):
    cfg_path = tmp_path / "config.toml"
    write_toml(cfg_path, {"server": {"host": "0.0.0.0", "port": 9000}})

    config = ListenerConfig.load(cfg_path)

    assert config.server.host == "0.0.0.0"
    assert config.server.port == 9000
    # defaults applied
    assert config.execution.default_timeout_min == 90
    assert config.execution.default_after_done == "sleep"
    assert config.execution.grace_seconds == 30
    assert config.bettergi.game_processes == ["YuanShen.exe", "GenshinImpact.exe"]
    # api_key absent -> generated on first run (covered in detail by the next test)
    assert config.auth.api_key != ""
    # tasks 配置默认值
    assert config.tasks.dir == "tasks"


def test_first_run_generates_and_persists_api_key(tmp_path):
    cfg_path = tmp_path / "config.toml"
    write_toml(cfg_path, {"server": {"host": "0.0.0.0", "port": 8765}})

    config = ListenerConfig.load(cfg_path)

    # key was empty -> generated
    assert config.auth.api_key != ""
    assert len(config.auth.api_key) >= 32  # 32+ hex chars

    # persisted to disk
    reloaded_raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    assert reloaded_raw["auth"]["api_key"] == config.auth.api_key

    # reloading does NOT regenerate
    config2 = ListenerConfig.load(cfg_path)
    assert config2.auth.api_key == config.auth.api_key


def test_first_run_preserves_existing_comments(tmp_path):
    # 配置文件带中文注释 + [auth] api_key 为空；首启生成密钥应保留注释
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        "# 我的配置\n"
        "[server]\n"
        "host = \"0.0.0.0\"\n"
        "port = 8765\n"
        "\n"
        "[auth]\n"
        "# 密钥留空则自动生成\n"
        "api_key = \"\"\n"
        "trusted_ips = []\n",
        encoding="utf-8",
    )

    config = ListenerConfig.load(cfg_path)

    text = cfg_path.read_text(encoding="utf-8")
    # 注释保留
    assert "# 我的配置" in text
    assert "# 密钥留空则自动生成" in text
    # 新密钥已写入，空值被替换
    assert config.auth.api_key in text
    assert 'api_key = ""' not in text


def test_first_run_when_file_missing_writes_bare_config(tmp_path):
    # 文件不存在时首启：生成密钥并写出裸配置（无注释，但功能完整）
    cfg_path = tmp_path / "config.toml"

    config = ListenerConfig.load(cfg_path)

    assert config.auth.api_key != ""
    reloaded_raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    assert reloaded_raw["auth"]["api_key"] == config.auth.api_key


# ── 配置简化：dir 推导 ──────────────────────────────────────────────────────

def test_dir_derives_paths_when_fields_unset(tmp_path):
    """dir 非空 + 未显式字段 → exe_path/config_path/log_path 自动推导。"""
    # 建一个假目录让 exists 检查通过
    base = tmp_path / "BetterGI"
    (base / "log").mkdir(parents=True)
    cfg_path = tmp_path / "config.toml"
    write_toml(cfg_path, {
        "bettergi": {"dir": str(base), "log_done_keyword": "完成"},
    })

    config = ListenerConfig.load(cfg_path)

    assert config.bettergi.dir == str(base)
    assert config.bettergi.exe_path == str(base / "BetterGI.exe")
    assert config.bettergi.config_path == str(base / "config")
    assert config.bettergi.log_path == str(base / "log" / "better-genshin-impact*.log")
    # 关键字不推导，保留显式值
    assert config.bettergi.log_done_keyword == "完成"


def test_dir_does_not_override_explicit_fields(tmp_path):
    """dir 与显式字段并存 → 显式字段优先。"""
    base = tmp_path / "BetterGI"
    (base / "log").mkdir(parents=True)
    cfg_path = tmp_path / "config.toml"
    write_toml(cfg_path, {
        "bettergi": {
            "dir": str(base),
            "exe_path": "D:/Other/BetterGI.exe",  # 显式覆盖
            "log_done_keyword": "完成",
        },
    })

    config = ListenerConfig.load(cfg_path)

    # exe_path 显式配置了，不被覆盖
    assert config.bettergi.exe_path == "D:/Other/BetterGI.exe"
    # config_path / log_path 没写 → 仍从 dir 推导
    assert config.bettergi.config_path == str(base / "config")
    assert config.bettergi.log_path == str(base / "log" / "better-genshin-impact*.log")


def test_dir_empty_falls_back_to_explicit_fields(tmp_path):
    """dir 为空 → 完全不参与，回退旧行为。"""
    cfg_path = tmp_path / "config.toml"
    write_toml(cfg_path, {
        "bettergi": {
            "dir": "",
            "exe_path": "D:/BGI/BetterGI.exe",
            "log_done_keyword": "完成",
        },
    })

    config = ListenerConfig.load(cfg_path)

    assert config.bettergi.exe_path == "D:/BGI/BetterGI.exe"
    # config_path 未配置 → 默认空（与旧行为一致，不硬推）
    assert config.bettergi.config_path == ""


def test_dir_missing_does_not_block_startup(tmp_path, caplog):
    """dir 不存在 → 不阻塞启动，但打 WARNING。"""
    cfg_path = tmp_path / "config.toml"
    write_toml(cfg_path, {
        "bettergi": {"dir": str(tmp_path / "不存在")},
    })

    import logging
    with caplog.at_level(logging.WARNING):
        config = ListenerConfig.load(cfg_path)

    # 推导仍然进行（运行时才失败）
    assert "不存在" in config.bettergi.exe_path
    # 打了 warning
    assert any("不存在" in rec.getMessage() and rec.levelname == "WARNING"
               for rec in caplog.records)


def test_dir_relative_path_is_resolved_to_absolute(tmp_path, monkeypatch):
    """dir 是相对路径 → resolve 成绝对路径。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "BGI" / "log").mkdir(parents=True)
    cfg_path = tmp_path / "config.toml"
    write_toml(cfg_path, {"bettergi": {"dir": "BGI"}})

    config = ListenerConfig.load(cfg_path)

    # 推导后变成绝对路径
    assert Path(config.bettergi.exe_path).is_absolute()
    assert Path(config.bettergi.exe_path).name == "BetterGI.exe"

