"""Wake-on-LAN 魔术包单元测试与 /api/wol 路由测试。"""
import socket
import json
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from main import create_app
from wol import send_magic_packet, _parse_mac


# ---------------- wol.py 单元 ----------------

def test_payload_format_mock_sendto():
    """payload = 6×0xFF + 16×MAC；UDP 发往 255.255.255.255:9。"""
    mac = "AA-BB-CC-DD-EE-FF"
    with mock.patch("socket.socket") as sock_cls:
        sock = sock_cls.return_value.__enter__.return_value
        send_magic_packet(mac)

        sock_cls.assert_called_once_with(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto.assert_called_once()
        args, kwargs = sock.sendto.call_args
        payload, addr = args
        mac_bytes = bytes.fromhex("AABBCCDDEEFF")
        assert payload == b"\xff" * 6 + mac_bytes * 16
        assert len(payload) == 102
        assert addr == ("255.255.255.255", 9)
        # 开启广播权限
        sock.setsockopt.assert_called_once_with(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)


@pytest.mark.parametrize("mac_str", [
    "AA-BB-CC-DD-EE-FF",
    "AA:BB:CC:DD:EE:FF",
    "AA.BB.CC.DD.EE.FF",
    "AABBCCDDEEFF",
    "aa-bb-cc-dd-ee-ff",     # 小写
])
def test_parse_mac_accepts_separators(mac_str):
    assert _parse_mac(mac_str) == bytes.fromhex("AABBCCDDEEFF")


@pytest.mark.parametrize("bad", [
    "AA-BB-CC-DD-EE",        # 缺字节
    "AA:BB:CC:DD:EE:FF:00",  # 多字节
    "ZZ-BB-CC-DD-EE-FF",     # 非十六进制
    "",
    "AA BB CC DD EE FF",     # 空格分隔不支持
])
def test_parse_mac_rejects_invalid(bad):
    with pytest.raises(ValueError):
        _parse_mac(bad)


def test_send_magic_packet_custom_broadcast():
    with mock.patch("socket.socket") as sock_cls:
        sock = sock_cls.return_value.__enter__.return_value
        send_magic_packet("AABBCCDDEEFF", broadcast="192.168.1.255", port=7)
        _, addr = sock.sendto.call_args[0]
        assert addr == ("192.168.1.255", 7)


# ---------------- /api/wol 路由 ----------------

def _app_with_mac(tmp_path, target_mac=""):
    cfg = {"default_target": None, "api_key": "k", "target_mac": target_mac}
    (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return create_app(
        config_path=str(tmp_path / "config.json"),
        history_path=str(tmp_path / "jobs.json"),
        reconcile_interval=0,
    )


def test_wol_route_sends_with_body_mac(tmp_path):
    calls = {}

    def fake_send(mac, broadcast="255.255.255.255", port=9):
        calls["mac"] = mac

    import main as main_mod
    app = _app_with_mac(tmp_path)
    with mock.patch("wol.send_magic_packet", side_effect=fake_send):
        # 路由内部 from wol import send_magic_packet —— patch 源模块属性
        with TestClient(app) as c:
            r = c.post("/api/wol", json={"mac": "AA:BB:CC:DD:EE:FF"})
    assert r.status_code == 200
    # mac 返回后端规范化值（大写连字符）；config target_mac 原为空 → 本次已保存
    assert r.json() == {"sent": True, "mac": "AA-BB-CC-DD-EE-FF", "saved": True}
    assert calls["mac"] == "AA:BB:CC:DD:EE:FF"


def test_wol_route_falls_back_to_config_mac(tmp_path):
    app = _app_with_mac(tmp_path, target_mac="aa-bb-cc-dd-ee-ff")
    sent = []

    def fake_send(mac, broadcast="255.255.255.255", port=9):
        sent.append(mac)

    with mock.patch("wol.send_magic_packet", side_effect=fake_send):
        with TestClient(app) as c:
            r = c.post("/api/wol", json={})
    assert r.status_code == 200
    assert r.json()["sent"] is True
    assert sent == ["aa-bb-cc-dd-ee-ff"]
    # 响应 mac 为规范化值；与配置等价（仅大小写差异）→ 不判为变化，saved=False
    assert r.json()["mac"] == "AA-BB-CC-DD-EE-FF"
    assert r.json()["saved"] is False


def test_wol_route_no_mac_anywhere_400(tmp_path):
    app = _app_with_mac(tmp_path, target_mac="")
    with TestClient(app) as c:
        r = c.post("/api/wol", json={})
        assert r.status_code == 400
        assert "MAC" in r.json()["detail"]


def test_wol_route_invalid_mac_400(tmp_path):
    app = _app_with_mac(tmp_path)
    with TestClient(app) as c:
        r = c.post("/api/wol", json={"mac": "not-a-mac"})
    assert r.status_code == 400


def test_wol_route_socket_error_502(tmp_path):
    import wol as wol_mod

    app = _app_with_mac(tmp_path, target_mac="AABBCCDDEEFF")
    with mock.patch.object(wol_mod, "send_magic_packet",
                           side_effect=OSError("network unreachable")):
        with TestClient(app) as c:
            r = c.post("/api/wol")
    assert r.status_code == 502


# ---------------- MAC 持久化（save 语义） ----------------

def _read_cfg(tmp_path):
    return json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))


def test_wol_route_persists_normalized_mac(tmp_path):
    """① 成功后 target_mac 落盘为规范大写连字符格式。"""
    app = _app_with_mac(tmp_path, target_mac="")
    with mock.patch("wol.send_magic_packet"):
        with TestClient(app) as c:
            r = c.post("/api/wol", json={"mac": "aa:bb:cc:dd:ee:ff"})
    assert r.status_code == 200
    assert r.json()["saved"] is True
    assert _read_cfg(tmp_path)["target_mac"] == "AA-BB-CC-DD-EE-FF"


@pytest.mark.parametrize("mac", [
    "aabbccddeeff",          # 裸 12 位
    "AA:BB:CC:DD:EE:FF",     # 冒号
    "aa-bb-cc-dd-ee-ff",     # 小写
])
def test_wol_route_equivalent_format_no_save(tmp_path, mac):
    """② 等价格式（裸 12 位/冒号/小写）不判为变化 → saved=False 不写盘。"""
    app = _app_with_mac(tmp_path, target_mac="AA-BB-CC-DD-EE-FF")
    with mock.patch("wol.send_magic_packet"):
        with TestClient(app) as c:
            r = c.post("/api/wol", json={"mac": mac})
    assert r.status_code == 200
    assert r.json() == {"sent": True, "mac": "AA-BB-CC-DD-EE-FF", "saved": False}
    assert _read_cfg(tmp_path)["target_mac"] == "AA-BB-CC-DD-EE-FF"


def test_wol_route_400_does_not_persist(tmp_path):
    """③ 非法 MAC（400）不落盘。"""
    app = _app_with_mac(tmp_path, target_mac="AA-BB-CC-DD-EE-FF")
    with TestClient(app) as c:
        r = c.post("/api/wol", json={"mac": "not-a-mac"})
    assert r.status_code == 400
    assert _read_cfg(tmp_path)["target_mac"] == "AA-BB-CC-DD-EE-FF"


def test_wol_route_502_does_not_persist(tmp_path):
    """④ 发送失败（502）不落盘。"""
    import wol as wol_mod

    app = _app_with_mac(tmp_path, target_mac="")
    with mock.patch.object(wol_mod, "send_magic_packet",
                           side_effect=OSError("network unreachable")):
        with TestClient(app) as c:
            r = c.post("/api/wol", json={"mac": "AA-BB-CC-DD-EE-FF"})
    assert r.status_code == 502
    assert _read_cfg(tmp_path)["target_mac"] == ""


def test_wol_route_save_preserves_schedules_and_default_target(tmp_path):
    """⑤ 保存 MAC 不丢同文件内的 schedules / default_target（update 原子更新语义）。"""
    cfg = {
        "default_target": {"ip": "192.168.1.50", "port": 8765, "hostname": "win"},
        "api_key": "k",
        "target_mac": "",
        "schedules": [{"id": "s1", "enabled": True, "name": "晨间挖矿", "time": "07:30",
                       "weekdays": [1, 2], "task_id": "mining", "wake": True}],
    }
    (tmp_path / "config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    app = create_app(
        config_path=str(tmp_path / "config.json"),
        history_path=str(tmp_path / "jobs.json"),
        reconcile_interval=0,
    )
    with mock.patch("wol.send_magic_packet"):
        with TestClient(app) as c:
            r = c.post("/api/wol", json={"mac": "AA-BB-CC-DD-EE-FF"})
    assert r.status_code == 200
    after = _read_cfg(tmp_path)
    assert after["target_mac"] == "AA-BB-CC-DD-EE-FF"
    assert after["default_target"] == cfg["default_target"]
    assert after["api_key"] == "k"
    assert after["schedules"] == cfg["schedules"]


def test_wol_route_save_false_does_not_persist(tmp_path):
    """⑥ save=False 只发包不落盘（临时唤醒别的机器场景）。"""
    app = _app_with_mac(tmp_path, target_mac="AA-BB-CC-DD-EE-FF")
    with mock.patch("wol.send_magic_packet"):
        with TestClient(app) as c:
            r = c.post("/api/wol", json={"mac": "11-22-33-44-55-66", "save": False})
    assert r.status_code == 200
    assert r.json() == {"sent": True, "mac": "11-22-33-44-55-66", "saved": False}
    assert _read_cfg(tmp_path)["target_mac"] == "AA-BB-CC-DD-EE-FF"
