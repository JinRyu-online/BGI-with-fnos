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
    assert r.json() == {"sent": True, "mac": "AA:BB:CC:DD:EE:FF"}
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
