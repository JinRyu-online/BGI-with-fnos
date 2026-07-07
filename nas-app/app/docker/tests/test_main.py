from fastapi.testclient import TestClient

from main import create_app


def _app(tmp_path):
    return create_app(config_path=str(tmp_path / "config.json"))


def test_health_returns_ok():
    client = TestClient(_app(__import__("pathlib").Path("/tmp/_unused_")))
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_home_page_renders(tmp_path):
    client = TestClient(_app(tmp_path))
    r = client.get("/")
    assert r.status_code == 200
    assert "BetterGI" in r.text  # 页面标题之类


def test_home_page_shows_no_target_when_unpaired(tmp_path):
    client = TestClient(_app(tmp_path))
    r = client.get("/")
    # 未配对时应提示去扫描设备
    assert "未配对" in r.text or "扫描" in r.text


def test_home_page_shows_target_when_paired(tmp_path):
    # 预置一个已配对的目标
    from settings import Settings
    s = Settings(tmp_path / "config.json")
    cfg = s.load()
    cfg["default_target"] = {"ip": "192.168.1.100", "port": 8765, "hostname": "DESKTOP-X"}
    cfg["api_key"] = "secret"
    s.save(cfg)

    client = TestClient(_app(tmp_path))
    r = client.get("/")
    assert "192.168.1.100" in r.text
    assert "DESKTOP-X" in r.text
