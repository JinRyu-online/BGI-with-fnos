# 迭代计划：WOL MAC 地址持久化与自动复用

> 2026-09-06 v2 · 经 subagent 评审修订（3 条必须项 + 建议项吸收）。
> 分支：feat/scheduled-tasks（追加 commit）。

## 0. 现状调查结论（先回答用户的问题）

**后端早已持久化、定时任务早已复用——真正的缺口在前端"同步时机"**：

| 链路 | 现状 | 证据 |
|---|---|---|
| 配置存储 | `config.json` 的 `target_mac`（settings.py:24，随容器 /data 持久化） | 已有 |
| 手动 WOL（`POST /api/wol`） | body.mac 为空时回退读 `cfg["target_mac"]`（main.py:974） | 已有 |
| **定时任务执行链** | `_execute_schedule` 唤醒**只用** `cfg["target_mac"]`（main.py:489），无 MAC 直接报"未配置 MAC，无法唤醒" | 已有复用 |
| 设置页展示 | 进设置页把 `target_mac` 回填输入框（SettingsPage.vue:178） | 已有 |
| **缺口** | 设置页输入 MAC 点"唤醒"后**只发包不保存**——`api_wol` 不写 `target_mac`，本次输入的新 MAC 丢失；下次定时任务仍用旧值（或空） | **需修** |

用户每换一次 MAC 就必须手动找地方改 `target_mac`——而 UI 上根本没有保存 target_mac 的入口。

## 1. 方案设计

### 1.1 后端（main.py `api_wol`）

- 新增内部辅助 `_normalize_mac(mac: str) -> str`：复用 `wol._parse_mac` 的 cleaned 逻辑，输出 `AA-BB-CC-DD-EE-FF` 大写连字符格式；非法返回 None（防御）。
- 发送成功后：
  1. `normalized = _normalize_mac(mac)`（发送前已过 `_parse_mac` 校验，此处不会 None；仍防御跳过保存）；
  2. **比较与落盘均用规范化值**：`normalized != normalized(cfg.get("target_mac"))` 时才写 `cfg["target_mac"] = normalized; settings.save(cfg)`（等价格式——大小写/冒号/裸 12 位——不判为变化，省写盘）；
  3. **响应 `mac` 字段返回规范化值**（⚠ 现有测试 `test_wol_route_sends_with_body_mac` 精确断言 `{"sent": True, "mac": "AA:BB:CC:DD:EE:FF"}`、`test_wol_route_falls_back_to_config_mac` 断言小写原样——**两个既有断言必须更新**，列入实施清单）；
  4. 响应加 `"saved": bool`。
- **body 新增可选 `save: bool = True` 逃生门**（评审建议采纳，约 3 行）：`save=False` 时只发包不落盘（临时唤醒别的机器场景）；UI v1 不暴露该字段。
- **发送失败（400/502）不落盘**。

### 1.2 并发风险修正（评审必须项 1）

`cfg["schedules"]`（定时任务定义）与 `target_mac` **同在 config.json**——api_wol 的 load→save 整文件覆盖窗口内，用户恰好编辑/增删定时任务会丢任务。风险面比 v1 表述的更大。
**本轮处置**：给 `Settings` 加模块级 `threading.Lock`，并提供 `update(mutator: Callable[[dict], None]) -> dict` 辅助（内部 load→mutator→save 全程持锁）；`api_wol` 的保存与 `api_pair`/`api_unpair`/`api_schedules_put` 一并迁移到 `settings.update(...)`（同进程内消除 load-modify-save 竞态；调度线程不写 config.json，无需跨进程）。
恢复路径说明：若打错 MAC 覆盖好值，重输旧 MAC 唤醒一次即恢复（写明在计划与汇报）。

### 1.3 前端（SettingsPage.vue）

- `sendWol` 成功后：`await loadConfig()` 回填；**回填竞态防护（评审必须项）**——仅当输入框当前为空或等于本次发送值时才回填 `macInput`（用户正在打新值不覆盖）。
- toast：**直接用响应 `r.mac`（后端规范值）**，`saved===true` 时文案追加"已记住该 MAC"；前端 `normalizeMac` 保留用于展示旧调用（不再承担规范化职责）。
- 已知不一致标注：前端 `MAC_RE` 只认 `:`/`-` 分隔（后端容错四种），用户粘贴 `.` 或裸 12 位会被前端挡——本轮不改校验（避免误导输入），仅在汇报与代码注释标注。

## 2. 实施清单

| # | 文件 | 改动 |
|---|---|---|
| 1 | `nas-app/app/docker/app/settings.py` | `Settings` 加模块级 `threading.Lock` + `update(mutator)` 原子辅助 |
| 2 | `nas-app/app/docker/app/main.py` | `api_wol`：规范化/比较/落盘/saved/save 字段；`_normalize_mac` 辅助；`api_pair`/`api_unpair`/`api_schedules_put` 迁移 `settings.update` |
| 3 | `nas-app/frontend/src/pages/SettingsPage.vue` | sendWol 成功后条件回填 + toast 用规范值 + "已记住"提示 |
| 4 | `nas-app/app/docker/tests/test_wol.py` | **更新 2 个既有断言** + 新增 6 例：① 成功后 target_mac 落盘为规范大写连字符 ② 等价格式（裸 12 位/冒号/小写）→ saved=false 不写 ③ 400 不落盘 ④ 502 不落盘 ⑤ 保存不丢 schedules/default_target ⑥ save=false 不落盘 |
| 5 | `nas-app/app/docker/tests/` 其他 | api_pair/unpair/schedules_put 迁移 update 后回归既有测试（如有 mock settings 的用例需适配） |

## 3. 测试与验收

- 门禁：NAS pytest 全绿（含新增 6 例）+ Windows pytest 回归 + 前端 vue-tsc/build:deploy/smoke。
- 手动：输入冒号小写 MAC 唤醒 → toast 显示 `AA-BB-...` 规范值 + "已记住该 MAC" → 刷新页面输入框为规范值 → config.json 中 schedules 完好 → 定时任务执行链读到新 MAC（schedules_state/日志验证）。

## 4. 风险

| 风险 | 缓解 |
|---|---|
| 打错 MAC 覆盖好值 → 定时任务唤醒失败 | 恢复路径：重输旧 MAC 再唤醒一次即覆盖回；唤醒结果在 schedules_state 可见（wake_failed） |
| Settings 加锁的锁粒度 | 模块级锁仅串行化 config.json 读写，历史/日志/调度状态各自独立文件不受影响 |
| 既有测试断言破坏 | 已列入实施清单 #4 显式更新 |
