from discovery import list_local_subnets, scan_subnet, scan_subnet_parallel


def test_list_local_subnets_from_interfaces():
    # 模拟 psutil.net_if_addrs() 的返回结构
    fake_addrs = {
        "lo": [("AF_INET", "127.0.0.1", "255.0.0.0", None, None)],
        "eth0": [("AF_INET", "192.168.1.5", "255.255.255.0", None, None)],
        "docker0": [("AF_INET", "172.17.0.1", "255.255.0.0", None, None)],
    }

    subnets = list_local_subnets(fake_addrs)

    # 排除 loopback；保留 eth0 与 docker0 的网段
    assert "192.168.1.0/24" in subnets
    assert "172.17.0.0/16" in subnets
    assert not any(s.startswith("127.") for s in subnets)


def test_scan_subnet_finds_matching_listener():
    # /30 含 2 个可用主机：.1 与 .2
    probe_calls = []
    def probe(ip, port):
        probe_calls.append(ip)
        return ip == "192.168.1.2"  # 只有 .2 端口开放

    def http_get(ip, port):
        if ip == "192.168.1.2":
            return {"service": "bgi-trigger", "hostname": "DESKTOP-A", "version": "1.0.0"}
        return None

    devices = scan_subnet("192.168.1.0/30", 8765, probe=probe, http_get=http_get)

    assert len(devices) == 1
    assert devices[0]["ip"] == "192.168.1.2"
    assert devices[0]["port"] == 8765
    assert devices[0]["hostname"] == "DESKTOP-A"


def test_scan_subnet_ignores_non_matching_service():
    def probe(ip, port):
        return True  # 全开

    def http_get(ip, port):
        return {"service": "something-else", "hostname": "x"}  # 不是我们的服务

    devices = scan_subnet("192.168.1.0/30", 8765, probe=probe, http_get=http_get)
    assert devices == []


def test_scan_subnet_skips_closed_ports():
    def probe(ip, port):
        return False  # 全关

    def http_get(ip, port):
        raise AssertionError("不应在端口关闭时调用 http_get")

    devices = scan_subnet("192.168.1.0/30", 8765, probe=probe, http_get=http_get)
    assert devices == []


def test_scan_subnet_parallel_finds_matching_listener():
    def probe(ip, port):
        return ip == "192.168.1.2"

    def http_get(ip, port):
        return {"service": "bgi-trigger", "hostname": "DESKTOP-A", "version": "1.0.0"}

    devices = scan_subnet_parallel("192.168.1.0/30", 8765, probe=probe, http_get=http_get)
    assert len(devices) == 1
    assert devices[0]["ip"] == "192.168.1.2"
    assert devices[0]["hostname"] == "DESKTOP-A"
