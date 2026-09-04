"""Wake-on-LAN 魔术包发送（纯标准库实现）。

魔术包格式：6 字节 0xFF 前缀 + 目标 MAC 重复 16 次（102 字节），
通过 UDP 广播发送到指定地址与端口（默认 255.255.255.255:9）。
"""
from __future__ import annotations

import socket


def _parse_mac(mac: str) -> bytes:
    """解析 MAC 地址字符串为 6 字节。

    支持 - : . 分隔与无分隔格式（大小写不敏感），如：
      "AA-BB-CC-DD-EE-FF" / "AA:BB:CC:DD:EE:FF" / "AA.BB.CC.DD.EE.FF" / "AABBCCDDEEFF"
    格式非法（无法还原出 6 字节或含非十六进制字符）抛 ValueError。
    """
    if not isinstance(mac, str):
        raise ValueError(f"invalid MAC address: {mac!r}")
    cleaned = mac.strip().replace("-", "").replace(":", "").replace(".", "")
    if len(cleaned) != 12:
        raise ValueError(f"invalid MAC address: {mac!r}")
    try:
        raw = bytes.fromhex(cleaned)
    except ValueError:
        raise ValueError(f"invalid MAC address: {mac!r}") from None
    if len(raw) != 6:
        raise ValueError(f"invalid MAC address: {mac!r}")
    return raw


def send_magic_packet(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> None:
    """向 broadcast:port 发送针对 mac 的 Wake-on-LAN 魔术包。

    MAC 格式非法抛 ValueError；网络发送失败抛 OSError（由调用方映射为 502）。
    """
    mac_bytes = _parse_mac(mac)
    payload = b"\xff" * 6 + mac_bytes * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(payload, (broadcast, port))
