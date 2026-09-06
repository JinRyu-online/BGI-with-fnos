# 迭代计划：三处日志/历史 UI 修复 + WOL 唤醒后焦点抢占治理

> 2026-09-06 v2 · 经 subagent 评审修订（6 条必须项全部吸收）。
> BetterGI 焦点机制结论来自上游源码考据（TaskControl.cs / OtherConfig.cs）。
> 分支：feat/scheduled-tasks（追加 commit）。

## 0. 现状与根因

### Bug 1：日志详情页"历史" tab 无选中态
`isActive(path)` 用 `route.path === path` 精确匹配——`/logs/xxx` 不等于 `/history`，五 tab 全灰。

### Bug 2：详情页滑动时顶部摘要卡"错位"
双层 sticky（`.detail-top` top:0 + `.summary-card` top:44px 估算值）不协调：滚动时两层间露缝，深色日志透出（截图即此）。`.detail-top` 还有 `margin: -4px -4px` 负 margin 抵消页面内边距实现全宽。

### Bug 3：日志组件高度被拉长
详情页 `body-height="60vh"`——vh 随 iOS 地址栏/键盘动态重算，惯性滚动时被拉长。状态页 240px 固定值正常。

### Bug 4：WOL 唤醒后 QQ/微信自启抢焦点，BetterGI 卡死
**源码实锤**：BetterGI 有现成开关「游戏失去焦点时候，强制恢复激活游戏窗口」（`OtherConfig.RestoreFocusOnLostEnabled`，**默认关闭**；设置 → 其他设置）。开启后调度器任务失去焦点时无上限循环每秒抢回焦点；关闭时则"暂停+重试 100 次"（≈100 秒后任务失败）——用户日志刷"不是原神，暂停"即此。

## 1. 前端修复（Bug 1/2/3）

### 1.1 Bug 1（App.vue）
```ts
/** tab 直系子页（/tasks/xxx）由 startsWith 前缀自动覆盖；
 *  首段不等于父 tab 的路由在此登记（如 /logs 归属 历史 tab）。 */
const PARENT_TAB: Record<string, string> = { '/logs': '/history' }

function isActive(path: string): boolean {
  if (route.path === path) return true
  if (route.path.startsWith(path + '/')) return true   // tab 直系子页
  const seg2 = route.path.split('/').slice(0, 2).join('/')
  return PARENT_TAB[seg2] === path                     // 登记的跨归属子页
}
```
预期行为：子页中父 tab 高亮；点已激活 tab 不回根（`go()` no-op 维持现状）。

### 1.2 Bug 2（LogDetailPage.vue）
- `.detail-top` + `.summary-card` 合并为单条 sticky 容器 `.detail-header`（`position: sticky; top: 0; z-index: 5`），内部纵向排返回行 + 摘要卡；摘要卡去自身 sticky；
- **水平负 margin 保留**（`margin: 0 -4px`，去上下负值）——抵消 `.page` 左右内边距实现全宽贯通，否则内容从两侧缝穿出（换位置复发）；容器加不透明背景 `var(--bg)`；
- 容器底加 `border-bottom: 1px solid var(--border)` 替代阴影分层。

### 1.3 Bug 3（LogDetailPage.vue）
- `body-height="60vh"` → **`420px` 固定值 + 短屏断点**：`@media (max-height: 700px) { /* iPhone SE 667px */ }` 下传 `300px`（chrome 预算 667-414≈253px，300px 略超出滚动可见属可接受，取 280px 更稳——以实际渲染微调，原则：固定 px，不用动态单位）；
- 60vh 是移动端日志区唯一动态单位源（base.css 的 100vh/dvh 为桌面框与 shell 全屏，不在本范围），替换后消失。

## 2. Bug 4 治理

### 2.1 文档化主方案（A+B，必做）
`windows-listener/README.md` 部署章节新增"无人值守前置配置"：
1. **BetterGI 设置 → 其他设置 → 开启「游戏失去焦点时候，强制恢复激活游戏窗口」**（副作用：开启后人工切出游戏需先暂停任务；无人值守无影响）；
2. **关闭 QQ/微信开机自启**（微信：设置→通用；QQ：设置→登录；或任务管理器→启动应用）；
3. **自动登录确认**（netplwiz）且**无人值守链路不要用 lock 收尾**（锁屏后 WOL 唤醒不解锁，BetterGI 看不见桌面——autologin 只在登录/重启时生效）。
`docs/测试注意事项.md` 加排查指引：日志刷"不是原神，暂停" → 按上述处理。

### 2.2 兜底钩子（C，本轮一并实现）

`[execution] pre_launch_script = ""`：
- **执行时机与范围**：仅真正 Popen 前执行（三分支的 handoff 接管**不执行**）；**pre-launch 之后、Popen 之前补一次 `jobs.abort_requested()` 检查**（脚本执行期间收到 /abort → 直接归档 aborted，不再拉起 BetterGI，消除"abort 空杀后脚本结束仍拉起"的时序洞）。
- **shell 语义（必须项）**：`subprocess.run(script, shell=True)` 在 Windows 走 **cmd.exe**——文档示例必须用 cmd 语法：`taskkill /IM Weixin.exe /F & taskkill /IM QQ.exe /F`；**加 `creationflags=subprocess.CREATE_NO_WINDOW`**（pythonw 下不弹控制台闪窗）；`timeout=60`，超时 kill（**文档注明：超时只杀 cmd 直接子进程，PowerShell 孙进程可能存活——脚本须自行保证短命**）。
- 失败容错：非零退出/超时/异常 → WARNING 日志，**不阻断** BetterGI 拉起（照 after_done 模式）。
- **注入点（必须项）**：`Launcher.__init__` 加 `pre_launch_runner: Callable[[str], None] | None = None`（默认内部包装 `subprocess.run(..., shell=True, timeout=60, creationflags=...)`）——测试注入 fake runner，不 monkeypatch 全局 subprocess（AGENTS 第 5 条 DI 约定）。
- `Execution` dataclass 新字段带默认值排尾部（同 log_done_mode 坑）；config.py DEFAULTS + `_dump_toml` 扁平平键（无嵌套，OK）；config.toml.example 示例用 cmd 语法并注明"避免反斜杠/双引号（TOML 手写序列化器零转义）"。
- **listener.py 装配**：Launcher 构造加传 `pre_launch_runner` 与 config 新字段；**改装配后必跑 `test_smoke_wiring.py`**。

## 3. 实施清单

| # | 文件 | 改动 |
|---|---|---|
| 1 | `nas-app/frontend/src/App.vue` | isActive 前缀 + PARENT_TAB（注释两条覆盖规则） |
| 2 | `nas-app/frontend/src/pages/LogDetailPage.vue` | 单 sticky 容器（水平负 margin 保留）+ body-height 420px/短屏 280px |
| 3 | `windows-listener/bgi_trigger/service/config.py` | `[execution] pre_launch_script` 默认 ""（字段带默认值排尾部） |
| 4 | `windows-listener/bgi_trigger/core/launcher.py` | `pre_launch_runner` 注入 + 执行点（Popen 分支前、handoff 不执行、abort_requested 复查、CREATE_NO_WINDOW、timeout 60、失败 WARNING） |
| 5 | `windows-listener/listener.py` | 装配传参 |
| 6 | `windows-listener/config.toml.example` | cmd 语法示例 + 转义注意 |
| 7 | `windows-listener/README.md` + `docs/测试注意事项.md` | 无人值守前置配置 + 排查指引（含"勿用 lock 收尾"） |
| 8 | `windows-listener/tests/test_launcher_extra.py` + `test_config.py` + `test_smoke_wiring.py` | pre_launch 4 例（空不执行/执行调用 fake runner/失败不阻断/abort 复查跳过）+ DEFAULTS 新字段例 + 装配回归 |

## 4. 测试与验收

- Windows：pytest 全绿（新增用例 + smoke_wiring 回归）。
- 前端：vue-tsc + build:deploy + smoke；手动：① 历史卡进详情 →"历史"tab 金色高亮 ② 详情页上下滑动 → 头部整体钉住、日志不透出 ③ 日志区固定 420px（短屏模拟 300px）不伸缩 ④ 状态页 240px 不变 ⑤ iPhone SE（DevTools 375×667）布局 ⑥ iOS 橡皮筋过滚时 sticky 头不跳。
- 真机功能验收（Bug 4）：开 BetterGI 焦点开关 + 保留微信自启 → 定时触发 → 日志出现"尝试恢复窗口"且任务推进；配 pre_launch_script（cmd 语法）验证执行/超时/无闪窗。

## 5. 风险

| 风险 | 缓解 |
|---|---|
| isActive 前缀误伤 | 前缀仅作用于五 tab 自身路径；跨归属需登记 |
| sticky 容器 iOS 抖 | 单层标准用法 + 真机验证项 |
| pre_launch_script 用户脚本挂起/弹窗 | timeout 60 kill + CREATE_NO_WINDOW；文档注明孙进程注意 |
| BetterGI 焦点开关副作用 | 文档明示"人工切出需先暂停" |
| TOML 序列化零转义 | example 注明避免反斜杠/双引号 |
| 420px 短屏过高 | max-height:700px 断点 280px + 验收 ⑤ |
