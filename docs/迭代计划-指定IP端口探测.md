# 迭代计划：设置页扫描设备 · 指定 IP:端口 快速匹配

> 2026-09-06 v2 · 经 subagent 审批修订（阻塞项 2 条已吸收，建议项 1/2/3/4/5/7/8 已吸收）。
> 范围：NAS 端（后端 + SPA 前端 + 测试 + mock）。Windows 监听器无需改动。

## 0. 问题与现状

飞牛 App 设置页「扫描设备」目前**只能整网段自动扫描**：

| 链路 | 现状 | 证据 |
|---|---|---|
| 前端入口 | `SettingsPage.vue` 仅有「开始扫描」按钮，`api.startScan()` 不传 host | SettingsPage.vue:55–66 |
| 后端扫描 | `POST /api/scan` 可选 `{subnet, port, sync}`，无「单 IP」入参 | main.py:171–175, 712–735 |
| 发现逻辑 | `discovery.auto_discover_and_scan` 扫 COMMON/本地子网，`stop_on_first_hit=True` | discovery.py:132–212 |
| 身份匹配 | `/health` 的 `service == "bgi-trigger"` 才收录 | discovery.py:84–90 |
| 配对 | 扫描结果 → `GET /api/discover-key` → `POST /api/pair` | SettingsPage.vue:94–115 |

**缺口场景**（用户真实痛点）：

1. 容器 `network_mode:host` 扫不到目标网段（多网卡、VLAN、Docker 网桥干扰）；
2. Windows 监听器改了非默认端口（config.toml `port` ≠ 18765），全网段扫默认端口必空；
3. 用户已知 Windows IP，只想秒级确认监听器在不在，不想等整网段扫描。

现有能力里最接近的是 `GET /api/discover-key?ip=&port=`（main.py:765–777），但它直连 `/key` 取密钥，**不做 `service=="bgi-trigger"` 身份匹配**，且不能作为「扫描结果」进入设备列表配对 UI。

## 1. 方案设计

### 1.1 总体决策

**新增轻量单点探测端点 `POST /api/probe`，设置页扫描区增加「指定地址」输入 + 探测按钮。**

不在 `/api/scan` 上塞 `host` 字段的理由（收窄表述）：

- **语义分离**：`scan` = 网段发现，`probe` = 已知地址身份匹配；混在一个端点会让 `_scan_progress` 出现「单 IP 假进度」，也让 scanner 注入契约模糊。
- **避免污染既有契约**：`scanner(subnet, port, progress_cb=None)` 已被 `_default_scanner` 与大量测试固化（main.py:231–240, test_routes.py:55–63）；技术上可在 `api_scan` 内 special-case host 而不改 scanner 签名，但会把「身份探测」硬塞进「网段扫描」路径，后续维护成本更高。
- 单点探测亚秒级返回，不需要子网进度轮询。

探测结果复用现有设备卡 + `discover-key` + `pair` 配对链路，**不新增配对接口**。

### 1.2 后端

#### discovery.py — 纯函数 `probe_host`（可区分失败原因）

```python
def probe_host(
    ip: str,
    port: int,
    probe: Callable[[str, int], bool],
    http_get: Callable[[str, int], dict | None],
) -> dict:
    """探测单个 IP:port，返回结构化结果（始终是 dict，便于 API 层映射文案）。

    成功：{"ok": True, "device": {"ip", "port", "hostname", "version"}}
    失败：{"ok": False, "reason": "connect_failed"|"not_listener"|"error", "detail": str}
      - connect_failed：TCP 探活失败（端口关/主机不可达）
      - not_listener：TCP 通，但 /health 非 bgi-trigger / 非 JSON / 请求异常
      - error：未预期异常（防御分支）
    """
```

实现要点：

- TCP 不通 → `{"ok": False, "reason": "connect_failed", "detail": ""}`，**不调用** http_get；
- TCP 通 → 调 http_get；`health.service == "bgi-trigger"` → 成功 device；否则 / 异常 → `not_listener`；
- 业务逻辑与 `scan_subnet` 单主机分支对齐（discovery.py:77–90），仅返回结构不同。

#### main.py — 端点与注入

模块级请求体（AGENTS.md：`from __future__ import annotations` 下必须模块级定义）：

```python
class ProbeBody(BaseModel):
    ip: str
    port: int | None = None   # None → cfg["scan"]["listener_port"]
```

`create_app(...)` 新增可选注入（**并在 docstring「可注入项」中补一行**，勿沿用已过时的 `scanner(ip, port)` 写法）：

```python
host_prober: Callable[[str, int], dict] | None = None
```

- 默认实现：`lambda ip, port: discovery.probe_host(ip, port, default_probe, default_http_get)`；
- 测试注入 fake，返回与 `probe_host` 同构的 dict，不触网。

`POST /api/probe` 处理流程：

1. **校验 IP**：`ipaddress.IPv4Address(body.ip)`；非法 → 400 `detail="IP 地址格式不正确"`。
2. **解析端口（关键：用 is None，禁用 `or`）**：

   ```python
   port = cfg["scan"]["listener_port"] if body.port is None else body.port
   if not (1 <= port <= 65535):
       raise HTTPException(status_code=400, detail="端口必须在 1–65535")
   ```

   说明：不可写 `body.port or cfg[...]`——`0 or 18765` 会静默变成默认端口，导致「port=0 → 400」验收挂掉。
3. **调用** `host_prober(ip, port)`。
4. **结果映射**（reason 代码 → 中文文案；失败一律 HTTP 200 + `found:false`，仅入参非法 400）：

| prober 返回 | HTTP | body |
|---|---|---|
| `{"ok": True, "device": {...}}` | 200 | `{found: true, device: {...}}` |
| `{"ok": False, "reason": "connect_failed"}` | 200 | `{found: false, reason: "无法连接该地址:端口"}` |
| `{"ok": False, "reason": "not_listener"}` | 200 | `{found: false, reason: "该地址未识别为 BetterGI 监听器"}` |
| `{"ok": False, "reason": "error", ...}` / 其他 | 200 | `{found: false, reason: "探测失败"}` |
| IP/端口非法 | 400 | `{detail: "..."}` |

成功响应示例：

```json
{"found": true, "device": {"ip": "192.168.31.43", "port": 8766, "hostname": "DESKTOP-GAMING", "version": "1.2.0"}}
```

**不做的事**：

- 不把探测地址写入 `config.json`（配对成功后 `api_pair` 本来就会写 `default_target`）；
- 不改动 `ScanBody` / `/api/scan` / scanner 注入签名；
- 不在 probe 里调 `/key`（密钥仍走前端 `discover-key` → pair，保持职责分离）。

### 1.3 前端

#### types.ts

```ts
export interface ProbeAck {
  found: boolean
  device?: DeviceInfo
  reason?: string
}
```

#### useApi.ts / mock/server.ts

- `useApi.ts` 的 type import 列表追加 `ProbeAck`（与现有 ScanAck 等并列）：

```ts
probe(ip: string, port?: number): Promise<ProbeAck> {
  if (mockEnabled) return mockApi.probe(ip, port)
  return request<ProbeAck>('POST', '/api/probe', port === undefined ? { ip } : { ip, port })
},
```

- `mock/server.ts` 的 type import 列表同样追加 `ProbeAck`：

```ts
async probe(ip: string, port?: number): Promise<ProbeAck> {
  await sleep(250)
  if (!ip) throw new MockHttpError(400, 'missing ip')
  // 演示：192.168.31.43 命中，其余未命中
  if (ip === '192.168.31.43') {
    return { found: true, device: { ip, port: port ?? 8766, hostname: 'DESKTOP-GAMING', version: 'v1.2.0' } }
  }
  return { found: false, reason: '无法连接该地址:端口' }
}
```

#### SettingsPage.vue — 扫描区 UI

在「开始扫描」按钮下方增加分隔 + 指定地址区块：

```
[ 开始扫描 ]

或指定地址匹配
[ 输入 IP 或 IP:端口        ] [探测]
hint: 已知 Windows 监听器地址时可跳过全网段扫描；端口可省略，默认使用配置值
```

交互：

1. **输入解析**（前端校验，非法直接 toast，不打后端）：
   - 仅 IP：`192.168.31.43` → port 省略（后端回退 `config.scan.listener_port`）；
   - `IP:port`：`192.168.31.43:8766`；允许前后空白、全角冒号 `：` 归一化为半角；
   - 非法 IP / 端口非整数或不在 1–65535 → toast 格式错误；
   - v1 不接受 `http://ip:port/` 等 URL 形态，toast 引导按 `IP` 或 `IP:端口` 输入；
   - placeholder/hint 中的默认端口从 `configState.config?.scan?.listener_port` 回填，不写死 18765；
2. **探测中**：按钮 spinner，禁用（`probing` ref）；「扫描中」时探测按钮也禁用；
3. **成功**：将 device **合并进 `scan.devices`**（按 `ip:port` 去重）；`scan.show = true` 以便露出设备卡；toast「已发现 …」；用户点「配对」走现有 `pairDevice`；
4. **失败**：toast `found.reason`（后端文案）；**不**清空已有扫描列表；
5. **设备列表 key / pairing 粒度（必须改）**：现 `:key="d.ip"` 与 `pairingIp = dev.ip` 在「同 IP 多端口」会冲突——统一改为 `` `${d.ip}:${d.port}` ``（模板 key、`pairingDevice` 变量、禁用判断）。
6. **进度区空壳治理**：子网进度条/子网行仅在 `scan.subnets.length > 0` 时展示；设备卡区域用 `scan.show || scan.devices.length` 控制。仅探测成功时不会露出空进度条。
7. **与「开始扫描」的关系**：点「开始扫描」会重置 `scan.devices = []` 并以扫描结果覆盖（现有 `startScan` 行为，SettingsPage.vue:61,79）——**新扫描 = 新列表**，探测结果在再次全网段扫描后被覆盖属预期，实施时不要合并两种来源。

样式复用现有 `.field-row` / `.btn` / `.hint`（与 WOL 输入行同构，SettingsPage.vue:250–263）。

### 1.4 依赖注入约定（AGENTS.md #5）

- `create_app(..., host_prober=None)`，**绝不在 api_probe 内部写死 default_probe 网络调用**；
- 测试注入 fake：`host_prober=lambda ip, port: {"ok": True, "device": {...}}` 或失败结构；
- 默认路径用 `discovery.probe_host` + `default_probe` / `default_http_get`（延迟 import httpx）。

## 2. 实施清单

| # | 文件 | 改动 |
|---|---|---|
| 1 | `nas-app/app/docker/app/discovery.py` | 新增 `probe_host`（结构化 ok/device/reason 返回） |
| 2 | `nas-app/app/docker/app/main.py` | 模块级 `ProbeBody`；`create_app` 增加 `host_prober` 注入并补 docstring；`POST /api/probe`（is None 端口解析 + 1–65535 校验 + reason→中文映射） |
| 3 | `nas-app/app/docker/tests/test_discovery.py` | 新增 `probe_host` 用例（见 §3） |
| 4 | `nas-app/app/docker/tests/test_routes.py` | 新增 probe 路由用例（见 §3，含双 reason 区分） |
| 5 | `nas-app/frontend/src/types.ts` | `ProbeAck` |
| 6 | `nas-app/frontend/src/composables/useApi.ts` | import `ProbeAck` + `api.probe()` |
| 7 | `nas-app/frontend/src/mock/server.ts` | import `ProbeAck` + `mockApi.probe()` |
| 8 | `nas-app/frontend/src/pages/SettingsPage.vue` | 指定地址输入 + 探测；设备 key/pairing 改为 `ip:port`；进度区 `v-if` 治理 |

不改：Windows-listener、openapi.yaml（probe 是 NAS 内部端点，不暴露给 NAS→Windows 协议）、`config.json` schema、scanner 注入签名。

## 3. 测试与验收

### 3.1 单元 / 路由（NAS pytest）

`test_discovery.py`：

1. `probe_host` 命中：probe=True + health.service=bgi-trigger → `ok=True`，device 字段齐全；
2. `probe_host` service 不符：`ok=False, reason="not_listener"`；
3. `probe_host` 端口关闭：probe=False → `ok=False, reason="connect_failed"`，且 **http_get 不被调用**。

`test_routes.py`（全部注入 `host_prober`，不触网）：

4. 成功：prober 返回 ok+device → 200 `{found:true, device...}`；
5. connect 失败：prober 返回 `reason=connect_failed` → 200，`reason` **等于**「无法连接该地址:端口」；
6. not_listener：prober 返回 `reason=not_listener` → 200，`reason` **等于**「该地址未识别为 BetterGI 监听器」；
7. 非法 IP（`not-an-ip`）→ 400；
8. 端口 `0` → 400（断言 body.detail 含端口，且 **prober 未被调用**）；
9. 端口 `70000` → 400；
10. 省略 port：`{ip}` → prober 收到 port == 18765（默认 `listener_port`）；
11. 显式 port：`{ip, port:8766}` → prober 收到 8766。

（合计 discovery 3 + routes 8 = **11 例**。）

### 3.2 前端门禁

```bash
cd nas-app/frontend && npm run build   # vue-tsc --noEmit && vite build
cd nas-app/frontend && npm run smoke
```

mock 模式手测：`?mock=1` 设置页 → 输入 `192.168.31.43` 探测 → 设备卡 → 配对；输入其他 IP → toast 未连接；仅探测时无空进度条。

### 3.3 真机验收（联调）

1. Windows listener 默认端口，设置页输入 LAN IP（不带端口）→ 探测成功 → 配对 → 状态卡显示主机；
2. 改 listener 端口后，输入 `IP:新端口` → 探测成功；
3. 不存在的 IP → toast「无法连接该地址:端口」，已有扫描结果不丢；
4. 开着但不是 bgi-trigger 的端口 → toast「该地址未识别为 BetterGI 监听器」；
5. 整网段「开始扫描」回归不受影响（探测结果被新扫描覆盖属预期）。

### 3.4 门禁命令

```bash
cd nas-app/app/docker && python -m pytest tests/ -q
cd nas-app/frontend && npm run build
cd nas-app/frontend && npm run smoke
```

Windows 端本轮无改动，若时间允许跑一次回归即可。

## 4. 风险与边界

| 风险 | 缓解 |
|---|---|
| 直连 IP 的 SSRF/扫内网担忧 | 本系统本就运行在可信内网、且已有 discover-key 代理任意 IP:port；probe 不扩大攻击面。不对外网开放。 |
| 默认端口假设错误 | hint/placeholder 从 config 读 `listener_port` 回填，不写死 18765 |
| 探测与全网段扫描并发 | UI 层扫描中禁用探测按钮；后端 probe 不占用 `_scan_progress`，无共享状态冲突 |
| 同 IP 多端口设备 key 冲突 | 设备卡 key / pairing 标识统一 `ip:port`（§1.3.5） |
| 仅探测时露出空进度条 | 进度区 `v-if="scan.subnets.length"`（§1.3.6） |
| `body.port or default` 吞掉 port=0 | 端口解析用 `is None` + 1–65535 校验；测试用例 8 盯死 |
| prober 返回 None/坏结构导致 API 无法映射文案 | 注入契约改为**始终返回结构化 dict**（ok+reason 代码）；API 只做映射；测试用例 5/6 区分两种 reason |
| 输入 URL / IPv6 | v1 仅 IPv4 + `IP`/`IP:端口`；其余 toast 格式错误 |

## 5. 交付物

- 代码：上表 8 个文件；
- 测试：NAS pytest 全绿（原 83 + 新增 **11 例**）；
- 前端：`npm run build` + `npm run smoke` 通过；
- 本计划文档（docs/迭代计划-指定IP端口探测.md）保留作为迭代记录。
