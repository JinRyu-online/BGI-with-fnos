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


def test_home_page_shows_scan_prompt_when_unpaired(tmp_path):
    client = TestClient(_app(tmp_path))
    r = client.get("/")
    # 未配对时页面提供「扫描」入口（GUI 为 JS 驱动，目标通过 /api/config 异步获取）
    assert "扫描" in r.text


def test_config_endpoint_returns_paired_target(tmp_path):
    # 预置一个已配对的目标，/api/config 应返回之（GUI 据此渲染目标信息）
    from settings import Settings
    s = Settings(tmp_path / "config.json")
    cfg = s.load()
    cfg["default_target"] = {"ip": "192.168.1.100", "port": 8765, "hostname": "DESKTOP-X"}
    cfg["api_key"] = "secret"
    s.save(cfg)

    client = TestClient(_app(tmp_path))
    r = client.get("/api/config")
    assert r.status_code == 200
    assert r.json()["default_target"]["ip"] == "192.168.1.100"
    assert r.json()["default_target"]["hostname"] == "DESKTOP-X"


def test_spa_mount_served_when_built(tmp_path):
    """SPA 构建产物存在时 GET /spa/ 应返回其 index.html（资产引用以 /spa/ 为根）。"""
    client = TestClient(_app(tmp_path))
    r = client.get("/spa/")
    if r.status_code == 404:
        # 产物未构建（纯后端 CI）——跳过断言
        return
    assert r.status_code == 200
    assert "assets/index-" in r.text


def test_home_page_links_to_spa(tmp_path):
    """旧首页提供新版 SPA 入口链接（/spa/）。"""
    client = TestClient(_app(tmp_path))
    r = client.get("/")
    assert r.status_code == 200
    assert 'href="/spa/"' in r.text
