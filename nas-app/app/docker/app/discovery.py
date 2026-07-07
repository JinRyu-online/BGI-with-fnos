"""局域网设备发现模块。

两部分：
- list_local_subnets：枚举本机网卡，推导出局域网 CIDR（排除 loopback）。
- scan_subnet：对某子网的每个 IP 做 TCP 探活，通的再请求 /health，
  校验 service == "bgi-trigger" 才认定为 BetterGI 监听器。

为便于单元测试，网卡枚举、TCP 探活、HTTP 请求均以可调用对象注入。
默认实现用 psutil（纯 Python，无需 C 编译）+ socket + httpx。
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Callable


def _extract(addr):
    """从 psutil snic 对象或普通元组中取出 (address, netmask)。"""
    if hasattr(addr, "address"):
        return addr.address, getattr(addr, "netmask", None)
    # 元组/列表：(family, address, netmask, ...)
    if len(addr) >= 3:
        return addr[1], addr[2]
    return None, None


def list_local_subnets(addrs: dict) -> list[str]:
    """从网卡地址表推导非 loopback 的 IPv4 网段 CIDR 列表。

    addrs 形如 psutil.net_if_addrs() 的返回：{网卡名: [addr, ...]}。
    每个 addr 可以是 psutil snic 对象或 (family, address, netmask, ...) 元组。
    """
    subnets: list[str] = []
    for ifname, addr_list in addrs.items():
        for addr in addr_list:
            ip, netmask = _extract(addr)
            if not ip or not netmask:
                continue
            if ":" in ip:  # 跳过 IPv6
                continue
            if ip.startswith("127."):  # 跳过 loopback
                continue
            try:
                net = ipaddress.ip_network(f"{ip}/{netmask}", strict=False)
            except ValueError:
                continue
            cidr = str(net)
            if cidr not in subnets:
                subnets.append(cidr)
    return subnets


def _iter_hosts(subnet: str):
    """枚举子网中的可用主机 IP（排除网络地址与广播地址）。"""
    net = ipaddress.ip_network(subnet, strict=False)
    # net.hosts() 已排除网络地址与广播地址；/31 /32 特殊，hosts 处理得当。
    return [str(h) for h in net.hosts()]


def scan_subnet(
    subnet: str,
    port: int,
    probe: Callable[[str, int], bool],
    http_get: Callable[[str, int], dict | None],
) -> list[dict]:
    """扫描子网，返回识别为 BetterGI 监听器的设备列表。

    probe(ip, port)   返回 True 表示 TCP 端口开放；
    http_get(ip, port) 返回 /health 的 dict（或 None/异常）。
    仅当 health.service == "bgi-trigger" 才收录。
    """
    devices: list[dict] = []
    for ip in _iter_hosts(subnet):
        if not probe(ip, port):
            continue
        try:
            health = http_get(ip, port)
        except Exception:
            continue
        if health and health.get("service") == "bgi-trigger":
            devices.append({
                "ip": ip,
                "port": port,
                "hostname": health.get("hostname", ""),
                "version": health.get("version", ""),
            })
    return devices


def scan_subnet_parallel(
    subnet: str,
    port: int,
    probe: Callable[[str, int], bool],
    http_get: Callable[[str, int], dict | None],
    max_workers: int = 128,
) -> list[dict]:
    """并行版 scan_subnet：用线程池并发探活，/24 量级可在数秒内完成。

    逻辑与 scan_subnet 一致，仅并发执行；结果按 IP 顺序返回。
    生产环境（默认扫描器）使用此函数，避免同步扫描耗时过长。
    """
    from concurrent.futures import ThreadPoolExecutor

    def _check(ip: str) -> dict | None:
        if not probe(ip, port):
            return None
        try:
            health = http_get(ip, port)
        except Exception:
            return None
        if health and health.get("service") == "bgi-trigger":
            return {
                "ip": ip, "port": port,
                "hostname": health.get("hostname", ""),
                "version": health.get("version", ""),
            }
        return None

    hosts = _iter_hosts(subnet)
    out: list[dict] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for r in ex.map(_check, hosts):
            if r:
                out.append(r)
    return out


def default_probe(ip: str, port: int, timeout: float = 0.5) -> bool:
    """默认 TCP 探活：尝试 connect，成功即端口开放。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect((ip, port))
        return True
    except OSError:
        return False


def default_http_get(ip: str, port: int, timeout: float = 1.5) -> dict | None:
    """默认 /health 请求（用 httpx）。失败返回 None。"""
    import httpx  # 延迟导入，便于无 httpx 环境下导入本模块做纯逻辑测试
    try:
        r = httpx.get(f"http://{ip}:{port}/health", timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception:
        return None
    return None
