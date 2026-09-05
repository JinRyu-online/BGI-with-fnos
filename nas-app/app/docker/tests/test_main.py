from fastapi.testclient import TestClient

from main import create_app


def _app(tmp_path):
    return create_app(config_path=str(tmp_path / "config.json"))


def test_health_returns_ok():
    client = TestClient(_app(__import__("pathlib").Path("/tmp/_unused_")))
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_home_redirects_to_spa(tmp_path):
    """首页 / 直接 307 跳转新版 SPA（旧 Jinja GUI 已移除）。"""
    client = TestClient(_app(tmp_path))
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/spa/"


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
    """跟随跳转后应落在 SPA 页面（/spa/ 返回其 index.html）。"""
    client = TestClient(_app(tmp_path))
    r = client.get("/")  # follow_redirects 默认 True
    if r.status_code == 404:
        # 产物未构建（纯后端 CI）——跳过断言
        return
    assert r.status_code == 200
    assert "assets/index-" in r.text


def test_spa_deep_link_fallback(tmp_path):
    """/spa/tasks 等 history 深链应回退 index.html；assets 缺失仍 404。"""
    client = TestClient(_app(tmp_path))
    r = client.get("/spa/tasks")
    if r.status_code == 404:
        return  # 产物未构建（纯后端 CI）
    assert r.status_code == 200
    assert "assets/index-" in r.text
    # 已知资产文件由 StaticFiles 挂载处理，不受回退影响
    r2 = client.get("/spa/definitely-missing.png")
    assert r2.status_code == 404
    # 真实资产文件可正常命中（favicon.png 由 public/ 复制进产物）
    r3 = client.get("/spa/favicon.png")
    assert r3.status_code == 200
