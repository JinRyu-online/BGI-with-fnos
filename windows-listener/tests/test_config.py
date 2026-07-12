import tomllib
from pathlib import Path

from bgi_trigger.service.config import ListenerConfig


def write_toml(path: Path, data: dict) -> None:
    """Helper: minimal TOML writer for test fixtures (flat + [section] only)."""
    lines = []
    for section, kv in data.items():
        lines.append(f"[{section}]")
        for k, v in kv.items():
            if isinstance(v, str):
                lines.append(f'{k} = "{v}"')
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
