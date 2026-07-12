"""局域网设备发现模块。

三部分：
- list_local_subnets：枚举本机网卡，推导出局域网 CIDR（排除 loopback）。
- scan_subnet / scan_subnet_parallel：对子网每个 IP 并行 TCP 探活 + /health 识别。
- auto_discover_and_scan：自动发现子网 + 扫描；无果则回退扫 COMMON_SUBNETS。

为便于单元测试，网卡枚举、TCP 探活、HTTP 请求均以可调用对象注入。
默认实现用 psutil（纯 Python，无需 C 编译）+ socket + httpx。
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Callable

log = logging.getLogger("bgi_trigger.discovery")


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


def auto_discover_and_scan(
    port: int = 8765,
    subnet: str | None = None,
    common_fallback: bool = True,
    stop_on_first_hit: bool = True,
    addrs: dict | None = None,
    probe: Callable[[str, int], bool] | None = None,
    http_get: Callable[[str, int], dict | None] | None = None,
    max_workers: int = 128,
    on_subnet_done: Callable[[str, list[dict]], None] | None = None,
) -> list[dict]:
    """自动发现子网 + 扫描；若仍无设备，回退扫描常见子网。

    扫描顺序（优先命中概率高 + 扫描量小的网段）：
      1. 用户通过 subnet 显式指定的子网（仅此一个，无 fallback）
      2. COMMON_SUBNETS（家用常见网段，每条 ≤ 254 IP）
      3. 本地 /24 子网（prefix ≥ 24，跳过 docker 网桥 /16 等）
      4. （可选）本地超大网段作为最后兜底

    on_subnet_done(cidr, found_devices): 每完成一个子网回调,供前端进度条。
    stop_on_first_hit: 发现任一设备后立即停止,不再扫剩余网段。
    """
    import ipaddress
    _probe = probe or default_probe
    _http = http_get or default_http_get
    seen: dict[str, dict] = {}   # ip -> device dict，自然去重

    def _collect(cidr: str) -> list[dict]:
        devs = scan_subnet_parallel(cidr, port, _probe, _http, max_workers)
        for d in devs:
            seen[d["ip"]] = d
        if devs:
            log.info("scan hit %s -> %s", cidr, [d["ip"] for d in devs])
        if on_subnet_done:
            on_subnet_done(cidr, devs)
        return devs

    # 1. 用户指定的子网
    if subnet:
        log.info("scan: explicit subnet %s", subnet)
        _collect(subnet)
        return list(seen.values())

    # 分类本地子网
    raw_addrs = addrs or _safe_net_if_addrs()
    local_small: list[str] = []   # /24 或更小
    local_large: list[str] = []   # prefix < 24，如 docker /16
    for s in list_local_subnets(raw_addrs):
        try:
            net = ipaddress.ip_network(s, strict=False)
            if net.prefixlen >= 24:
                local_small.append(s)
            else:
                local_large.append(s)
                log.info("scan: defer large local subnet %s", s)
        except ValueError:
            pass

    # 2. 优先扫 COMMON
    log.info("scan: plan COMMON(%d) + local /24(%d) + local large(%d)",
             len(COMMON_SUBNETS), len(local_small), len(local_large))
    for cidr in COMMON_SUBNETS:
        _collect(cidr)
        if stop_on_first_hit and seen:
            return list(seen.values())

    # 3. 补扫本地 /24
    for s in local_small:
        _collect(s)
        if stop_on_first_hit and seen:
            return list(seen.values())

    # 4. 全没命中时回退扫本地大网段
    if common_fallback and not seen:
        for s in local_large:
            _collect(s)
            if stop_on_first_hit and seen:
                return list(seen.values())

    log.info("scan: done, found %d device(s)", len(seen))
    return list(seen.values())


def _safe_net_if_addrs() -> dict:
    """安全调用 psutil.net_if_addrs()；失败时返回空字典。"""
    try:
        import psutil
        return psutil.net_if_addrs()
    except Exception:
        return {}


# 常见目标子网（CIDR）。当宿主机自动发现的子网为空、或用户未指定子网且
# 自动发现未命中任何设备时，作为 fallback 扫描这些网段。
# 绝大多数家用/办公局域网都落在这些网段内。
COMMON_SUBNETS: list[str] = [
    "192.168.31.0/24",   # 中国常见路由器默认网段
    "192.168.1.0/24",    # TP-Link / 华为等默认
    "192.168.0.0/24",    # 水星 / D-Link 等默认
    "192.168.2.0/24",
    "192.168.3.0/24",
    "192.168.50.0/24",   # 小米路由器默认
    "192.168.100.0/24",  # 部分光猫/运营商网关
    "192.168.0.0/23",    # 512 口子网（合并 0.0 和 1.0）
    "10.0.0.0/24",
    "172.16.0.0/24",
]

# 标记：上次 fallback 实际扫到的子网，便于后续缓存/快速扫描。
_last_fallback_hit: set[str] = set()


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
