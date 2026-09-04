import json

from settings import Settings, DEFAULT_CONFIG


def test_load_returns_defaults_when_no_file(tmp_path):
    path = tmp_path / "config.json"
    s = Settings(path)

    cfg = s.load()

    assert cfg["default_target"] is None
    assert cfg["api_key"] == ""
    assert cfg["scan"]["listener_port"] == 8765
    assert cfg["poll"]["interval_sec"] == 10


def test_save_then_load_round_trip(tmp_path):
    path = tmp_path / "config.json"
    s = Settings(path)

    cfg = s.load()
    cfg["api_key"] = "secret"
    cfg["default_target"] = {"ip": "192.168.1.100", "port": 8765, "hostname": "DESKTOP-X"}
    s.save(cfg)

    reloaded = Settings(path).load()
    assert reloaded["api_key"] == "secret"
    assert reloaded["default_target"]["ip"] == "192.168.1.100"


def test_load_merges_partial_file_with_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"api_key": "k"}), encoding="utf-8")

    cfg = Settings(path).load()

    assert cfg["api_key"] == "k"
    # missing sections still get defaults
    assert cfg["scan"]["listener_port"] == 8765
    assert cfg["poll"]["interval_sec"] == 10


def test_default_config_is_complete():
    # sanity: the shipped defaults cover every key the app reads
    assert set(DEFAULT_CONFIG.keys()) == {"default_target", "api_key", "target_mac", "scan", "poll"}
    assert "listener_port" in DEFAULT_CONFIG["scan"]
    assert "diag_ports" in DEFAULT_CONFIG["scan"]
    assert DEFAULT_CONFIG["target_mac"] == ""
