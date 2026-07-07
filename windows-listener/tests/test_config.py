import tomllib
from pathlib import Path

from config import ListenerConfig


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
