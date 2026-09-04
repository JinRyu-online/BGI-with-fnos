# BetterGI Trigger · 移动端 GUI 设计规范（Wave 2 前端实现依据）

> **本规范基于 `docs/原型/bgi-prototype.html`，视觉细节以原型为准。**
> Wave 2 前端工程师将按本规范实现 Vue 3 + TypeScript 版本。原型为单文件零依赖 mock，所有交互均已可点按演示；本文档把原型中隐含的规则显式化为工程约束。

---

## 0. 技术栈与总体约束（Vue 3 + TS）

- Vue 3 `<script setup>` + TypeScript，组件按第 4 节清单拆分。
- 所有主题 token 以 CSS 自定义属性形式放在全局样式 `:root`，**与原型的 `:root` 一字不差**（见第 1 节）；组件内禁止出现硬编码色值/字号/间距。
- 所有状态文案、颜色、动画标记**收敛为一个 TS 常量文件**（如 `src/constants/jobState.ts`），禁止在组件里散落 if/else 映射（见第 2 节）。
- 原型中的 mock 流转逻辑（触发 → running → completing → done / 中止分支）在真实实现中替换为 API + WebSocket，但 **UI 状态机出口保持唯一**：任何状态变化统一走一个 `renderStatus()` 等价的响应式计算（Pinia store 单一 state）。
- Toast 替代一切 `alert()`；确认类操作一律用 ConfirmDialog。

---

## 1. 设计 Token（CSS 变量，与原型 `:root` 完全一致）

### 1.1 完整代码块（复制即用）

```css
:root {
  /* ===== 品牌色 ===== */
  --brand: #5b8def;
  --brand-pressed: #3a6fd8;
  --brand-weak: #eef3fe;

  /* ===== 中性色 ===== */
  --bg: #f4f6f9;
  --surface: #ffffff;
  --surface-2: #fafbfc;
  --border: #e5e7eb;
  --border-strong: #d7dbe0;
  --text-1: #1e293b;
  --text-2: #64748b;
  --text-3: #94a3b8;

  /* ===== 语义色 ===== */
  --danger: #dc2626;
  --danger-pressed: #b91c1c;
  --danger-weak: #fee2e2;
  --warning: #d97706;
  --warning-weak: #fef3c7;
  --success: #16a34a;
  --success-weak: #dcfce7;
  --info: #2563eb;
  --info-weak: #dbeafe;

  /* ===== 任务状态色（7 后端态 + idle 前端占位态）===== */
  --state-idle: #94a3b8;            /* 灰 */
  --state-idle-weak: #eef0f3;
  --state-idle-text: #64748b;
  --state-running: #f59e0b;         /* 黄（呼吸） */
  --state-running-weak: #fef3c7;
  --state-running-text: #b45309;
  --state-completing: #3b82f6;      /* 蓝（呼吸） */
  --state-completing-weak: #dbeafe;
  --state-completing-text: #1d4ed8;
  --state-done: #22c55e;            /* 绿 */
  --state-done-weak: #dcfce7;
  --state-done-text: #15803d;
  --state-failed: #ef4444;          /* 红 */
  --state-failed-weak: #fee2e2;
  --state-failed-text: #b91c1c;
  --state-timed-out: #f97316;       /* 橙 */
  --state-timed-out-weak: #ffedd5;
  --state-timed-out-text: #c2410c;
  --state-aborted: #b4534b;         /* 灰红 */
  --state-aborted-weak: #f6e7e5;
  --state-aborted-text: #9a443d;
  --state-abnormal-exit: #ef4444;   /* 红 */
  --state-abnormal-exit-weak: #fee2e2;
  --state-abnormal-exit-text: #b91c1c;

  /* 呼吸光晕（与状态色配套的 rgba，仅 running/completing 使用） */
  --glow-running: rgba(245, 158, 11, .45);
  --glow-completing: rgba(59, 130, 246, .45);

  /* ===== 日志面板（深色）===== */
  --log-bg: #0b1220;
  --log-head-bg: #0f172a;
  --log-border: #1e293b;
  --log-text: #cbd5e1;
  --log-text-dim: #64748b;
  --log-keyword: #fbbf24;   /* “任务结束”关键字 黄 */
  --log-error: #f87171;     /* 错误行 红 */
  --log-sys: #22d3ee;       /* 系统消息 青 */

  /* ===== 间距刻度（4px 基准）===== */
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 20px;
  --space-6: 24px;

  /* ===== 圆角 ===== */
  --radius-sm: 8px;
  --radius-md: 12px;
  --radius-lg: 16px;
  --radius-full: 999px;

  /* ===== 字号阶梯 ===== */
  --font-xs: 12px;
  --font-sm: 13px;
  --font-base: 14px;
  --font-md: 15px;
  --font-lg: 17px;
  --font-xl: 20px;
  --font-2xl: 24px;

  /* ===== 字体栈（系统字体）===== */
  --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
               "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei",
               "Noto Sans CJK SC", sans-serif;
  --font-mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas,
               "Liberation Mono", monospace;

  /* ===== 布局尺寸 ===== */
  --shell-max: 480px;        /* app-shell 最大宽度 */
  --header-height: 52px;     /* 顶栏高度 */
  --tabbar-height: 64px;     /* 底部 Tab 高度（不含安全区） */
  --logpanel-height: 240px;  /* 日志面板滚动区高度 */

  /* ===== 阴影 ===== */
  --shadow-card: 0 1px 3px rgba(15, 23, 42, .06), 0 1px 2px rgba(15, 23, 42, .04);
  --shadow-float: 0 8px 24px rgba(15, 23, 42, .16);
}
```

### 1.2 token 使用约定

| 类别 | 约定 |
|---|---|
| 状态色三件套 | 每个状态有 `-weak`（徽章底）、`-text`（徽章字）、主色（圆点/描边）三个 token，必须成对使用，禁止拿主色直接当大面积背景 |
| 日志配色 | 仅允许在日志面板内使用 `--log-*`；日志行分类（关键字/错误/系统）见第 2 节映射 |
| 间距 | 只允许使用 `--space-*` 刻度，不出现 6px/10px 等任意值 |
| 深浅色 | 本期仅浅色主题；日志面板恒为深色（不受主题影响） |

---

## 2. 状态映射表（单一来源）

### 2.1 任务状态映射

后端状态机枚举来源：`windows-listener/bgi_trigger/core/state.py` 的 `JobState`（7 态）；`idle` 为前端"无任务"占位态，不来自后端。

| state 值 | 中文文案 | 圆点色 token | 徽章底/字 token | 呼吸动画 |
|---|---|---|---|---|
| `idle`（前端） | 空闲 | `--state-idle` | `--state-idle-weak` / `--state-idle-text` | 否 |
| `running` | 运行中 | `--state-running` | `--state-running-weak` / `--state-running-text` | **是** |
| `completing` | 收尾中 | `--state-completing` | `--state-completing-weak` / `--state-completing-text` | **是** |
| `done` | 已完成 | `--state-done` | `--state-done-weak` / `--state-done-text` | 否 |
| `abnormal_exit` | 游戏异常退出 | `--state-abnormal-exit` | `--state-abnormal-exit-weak` / `--state-abnormal-exit-text` | 否 |
| `timed_out` | 超时 | `--state-timed-out` | `--state-timed-out-weak` / `--state-timed-out-text` | 否 |
| `failed` | 失败 | `--state-failed` | `--state-failed-weak` / `--state-failed-text` | 否 |
| `aborted` | 已中止 | `--state-aborted` | `--state-aborted-weak` / `--state-aborted-text` | 否 |

终态集合：`done / abnormal_exit / timed_out / failed / aborted`（与后端 `_TERMINAL` 一致）。
非终态集合（可中止、计时走秒、日志面板活跃）：`running / completing`，外加前端瞬时态 `triggering`（触发请求进行中，文案"触发中…"，无对应后端态）。

**Vue 实现必须收敛为一个 TS 常量文件**（建议 `src/constants/jobState.ts`），结构对齐原型中的 `STATE_MAP`：

```ts
// src/constants/jobState.ts —— 状态映射单一来源，组件内禁止重复定义
export interface JobStateMeta {
  zh: string;            // 中文文案
  dotClass: string;      // StatusDot class
  badgeClass: string;    // 徽章 class（b-<state>）
  breathe: boolean;      // 圆点是否呼吸动画（仅 running/completing）
  terminal: boolean;     // 是否终态
}
export const JOB_STATE_MAP: Record<string, JobStateMeta> = {
  idle:          { zh: '空闲',         dotClass: 's-idle',       badgeClass: 'b-idle',           breathe: false, terminal: false },
  running:       { zh: '运行中',       dotClass: 's-running',    badgeClass: 'b-running',        breathe: true,  terminal: false },
  completing:    { zh: '收尾中',       dotClass: 's-completing', badgeClass: 'b-completing',     breathe: true,  terminal: false },
  done:          { zh: '已完成',       dotClass: 's-done',       badgeClass: 'b-done',           breathe: false, terminal: true  },
  abnormal_exit: { zh: '游戏异常退出', dotClass: 's-abnormal',   badgeClass: 'b-abnormal_exit',  breathe: false, terminal: true  },
  timed_out:     { zh: '超时',         dotClass: 's-timed-out',  badgeClass: 'b-timed_out',      breathe: false, terminal: true  },
  failed:        { zh: '失败',         dotClass: 's-failed',     badgeClass: 'b-failed',         breathe: false, terminal: true  },
  aborted:       { zh: '已中止',       dotClass: 's-aborted',    badgeClass: 'b-aborted',        breathe: false, terminal: true  },
};
export const TERMINAL_STATES = Object.keys(JOB_STATE_MAP).filter(k => JOB_STATE_MAP[k].terminal);
```

### 2.2 日志行分类映射

| 分类 | class | 颜色 token | 命中规则（与原型一致） |
|---|---|---|---|
| 完成关键字 | `kw` | `--log-keyword` | 行内含 `任务结束`（原型额外加 `★` 前缀加粗） |
| 错误行 | `err` | `--log-error` | `/⚠|失败|异常|错误|error|闪退|fatal/i` |
| 系统消息 | `sys` | `--log-sys` | `[系统]` 前缀 |
| 普通行 | （无） | `--log-text` | 其余 |

### 2.3 WS 连接状态映射（日志面板头部）

| ws 状态 | 文案 | 圆点样式 |
|---|---|---|
| `connecting` | 连接中… | `--state-running` + 1s 闪烁动画 |
| `connected` | 实时同步中 | `--state-done` |
| `error` | 连接错误 / 未配对 | `--log-error` |
| `closed` | 已结束 | `--state-idle` |

---

## 3. 布局规格

### 3.1 App Shell

- 移动端：占满视口，`width: 100%; max-width: var(--shell-max)`（480px），`height: 100dvh`，`display: flex; flex-direction: column`，`overflow: hidden`。
- 桌面端（≥700px）：居中显示并套手机框外观——`height: 844px; max-height: calc(100vh - 48px); border-radius: 32px;` 大阴影 + 径向渐变页面底，明确传达"这是手机应用"。
- 内部结构自上而下：Header（52px，固定）→ Pages（`flex: 1`，每页独立滚动）→ TabBar（64px + 安全区，固定）。

### 3.2 底部 Tab 导航

- 高度：`--tabbar-height` 64px（不含安全区）；`padding-bottom: env(safe-area-inset-bottom)`。
- 4 个 Tab：状态 📡 / 任务 ⚡ / 历史 📜 / 设置 ⚙️（图标 + 文字，文字 11px）。
- 激活态：`--brand` 色 + 600 字重；非激活 `--text-2`。
- 背景 `rgba(255,255,255,.94)` + `backdrop-filter: blur(10px)`。
- 页面内容底部 padding 必须预留 `calc(var(--tabbar-height) + env(safe-area-inset-bottom) + 24px)`，防止最后一项被 Tab 遮挡。

### 3.3 日志面板

- 滚动区高度固定 `--logpanel-height`（240px）；头部/底部条 `--log-head-bg`。
- 字体 `--font-mono` 12px / line-height 1.6；`white-space: pre-wrap; word-break: break-all`。
- 行缓冲上限 **400 行**（超出移除最旧行），右上角角标实时显示"n 行"。
- 页面切换时不销毁日志面板状态（Vue 中日志缓冲放 store，不放组件局部 ref）。

### 3.4 触控目标

- 所有可点元素最小 **44×44px**（`.btn` `min-height: 44px`；Tab 项 `min-height: 44px`）。
- 例外：过滤 chip 视觉高度 36px，用 `::after { inset: -4px }` 扩大命中区至 44px。
- 行内小按钮（如设备卡"配对"）最小高度 40px 且不得与相邻按钮间距小于 8px。

---

## 4. 组件清单与行为规范

### 4.1 StatusDot（状态圆点）

- 尺寸 20px 圆形，`background` 取状态主色 token；通过 CSS 变量 `--dot` / `--glow` 注入。
- 呼吸动画仅 `running`（黄）与 `completing`（蓝）启用：`breathe` class，1.6s ease-in-out，光晕扩散 + 轻微缩放（`@keyframes breathe`，见原型）；其余状态 `animation: none`。
- 状态切换时 `background` 有 0.3s 过渡。

### 4.2 LogPanel（实时日志面板）

- 结构：头部（WS 状态点 + 状态文案 + job id + 行数角标）→ 滚动体 → 悬浮"↓ 回到底部"按钮。
- **自动滚动规则**：新行追加后，若用户视口距底部 ≤ 40px，`scrollTop = scrollHeight` 跟随到底；距底部 > 40px 判定为用户上滑阅读，**暂停跟随**并显示"↓ 回到底部"悬浮 pill；点击该按钮或手动滚回距底 ≤ 40px 时恢复跟随、隐藏按钮。
- 行追加走 `logAppend()`：限 400 行缓冲、更新角标、按 2.2 节规则着色。
- WS 生命周期对齐原型：新 job 开始 → `connecting`；建立 → `connected`；终态/断开 → `closed`；异常/未配对 → `error`。
- **WS 重连提示**：断开后自动重连采用指数退避（建议 1s 起、上限 30s）；每次重连尝试在日志面板内追加一条 `sys` 行（如 `[系统] 连接断开，3s 后重试…`），头部状态点切回 `connecting`；连续失败 ≥ 5 次切 `error` 并弹 Toast"日志连接失败，请检查网络"。

### 4.3 TaskCard（任务卡）

- 内容：显示名（17px/700）+ 右侧触发按钮；第二行分组链 pill（`日常一条龙 → 关闭游戏`，末段高亮蓝）；第三行属性 pill（`⏱ 90 分钟` 蓝、收尾动作 `💤 休眠` 绿）。
- 分组链 pill：普通段灰底、末段 `--info-weak` 蓝底加粗，箭头 `--text-3`。
- 属性 pill 数据来自任务配置：`timeout_min` 分钟、`after_done ∈ {sleep, shutdown, lock, none}` 对应 `💤 休眠 / ⏻ 关机 / 🔒 锁屏 / ⏸ 不操作`。

### 4.4 TriggerButton（触发按钮，三态）

| 态 | 视觉 | 交互 |
|---|---|---|
| 默认 | `btn-primary` 蓝，文案 `▶ 执行` | 可点，`active` 缩放 0.97 |
| loading | 蓝 + 白色 spinner，文案 `触发中` | `disabled`，点击无效 |
| 运行中禁用 | `btn-secondary` 灰，文案 `运行中，不可触发` | `disabled` |

- 触发即互斥：点击后**立即**进入 loading 并**全局禁用所有任务卡触发按钮**（单活 job 模型）；存在非终态 job（含 triggering）时任何卡片都不得出现可点的触发按钮。
- loading 持续到触发请求返回（原型 1.5s）；成功后跳状态页并转 `running`，失败恢复默认态并弹错误 Toast。

### 4.5 HistoryCard（历史记录卡）

- 内容：状态徽章（7 态全覆盖，见 2.1）+ 任务名 + 用时（等宽字体）；次行 `开始 MM-DD HH:mm · 结束 …`。
- 进行中记录 `end` 显示 `—`、用时显示 `进行中…`；顶部过滤 chip：全部 / 进行中（`running|completing`）/ 已完成（终态）。
- 徽章配色必须取自 2.1 映射，禁止新配色。

### 4.6 Toast（全局提示）

- 替代一切 `alert()`；容器固定在 Header 下方居中，多条纵向堆叠，`pointer-events: none`。
- 类型 `success / error / info`，前置 8px 圆点分别为 `--state-done` / `--log-error` / `--brand`。
- 动画：顶部滑入 0.25s → 停留 2.2s → 淡出上移 0.3s 后移除。
- 单条最大宽度 86%，超出折行；深色底 `rgba(17,24,39,.95)` + 白字。

### 4.7 Skeleton（骨架屏）

- 页面切换时显示 300ms 假 loading：灰色微光条块（`shimmer` 动画，1.2s 循环），块高模拟真实卡片布局（状态页 92/110/300px 等）。
- 内容与骨架互斥显示；切换到已访问页同样播放（保持一致节奏，避免闪变）。
- Vue 中用 `<Suspense>` 或页面级 `loading` ref 实现，块数与高度对齐原型 `[data-sk]` 结构。

### 4.8 ConfirmDialog（确认弹窗）

- 触发场景：强制清理（状态页 + 设置页两处入口）、取消配对；Promise 化 API `showConfirm(title, body, okText): Promise<boolean>`。
- 结构：蒙层（`rgba(15,23,42,.45)`）+ 居中卡片（max-width 320px，`popIn` 缩放入场）；底部"取消"（灰）+ 危险操作按钮（`btn-danger` 红）。
- 点蒙层 = 取消；确认按钮文案随场景变化（如"强制清理"）。
- 危险区列表项（设置页）：白底红字、顶部 `--danger-weak` 分隔线，主标题 + 灰色小字说明，整行可点，最小高 48px。

### 4.9 其他页面区块

- **状态页主操作区**：`中止`（`btn-secondary`，仅 running/completing 可用，其余禁用置灰；completing 反悔窗口时叠加 `btn-attention` 红色脉冲光环）+ `强制清理`（`btn-danger` 红，恒可点，点击弹确认）。
- **设置页配对卡**：已配对显示主机名 + `ip:port · 版本`（等宽小字）+ 绿色"已配对"徽章；未配对灰徽章 + 引导文案。顶栏右侧同步显示设备 chip（未配对时灰）。
- **扫描区**：开始按钮 → spinner 态 → 进度条（`--brand`，8px 圆角，width 过渡 0.3s）+ 子网逐行"等待 → 扫描中…（蓝）→ 发现 n 台（绿）/ 无设备（灰）"→ 完成后出现设备卡与"配对"按钮。
- **WOL 区**：MAC 输入框（等宽字体，focus 蓝描边 + 弱蓝光圈）+ 唤醒按钮；格式 `/^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$/` 校验，失败弹错误 Toast。

---

## 5. 交互规范

### 5.1 按钮反馈

- 所有 `.btn`：`active` 态 `transform: scale(.97)` + 深色按压色（primary→`--brand-pressed`，danger→`--danger-pressed`）；`disabled` `opacity: .45` 且取消缩放。
- 异步按钮触发时文字替换为 spinner + 进行动词（如"扫描中…"），期间 `disabled`。
- 中止按钮在 completing 反悔窗口用 `btn-attention` 脉冲吸引注意（这是"可反悔"的核心提示）。

### 5.2 触控目标

- 见 3.4 节：可点元素 ≥44×44px；chip 用热区外扩方案；相邻可点元素间距 ≥8px。

### 5.3 滚动暂停逻辑（日志面板）

- 判定阈值 40px；见 4.2 节完整规则。`scroll` 监听直接读 `scrollHeight - scrollTop - clientHeight`，不节流（成本低）。

### 5.4 计时与状态流转（对齐原型 mock 节奏）

- running/completing 期间每 500ms 刷新"已运行 mm:ss"；进入终态冻结为"总用时 mm:ss"。
- 真实实现中：进入 running 的时刻以 job `created_at`（Unix 秒）为基准，页面刷新后恢复计时**不得归零**（沿用现有 GUI `resumeActiveJob` 的做法）。
- `done` 弹 Toast"任务完成，将休眠"；`aborted` 弹"任务已中止"。

### 5.5 WS 重连提示

- 见 4.2 节末段：指数退避重连 + 面板内 `sys` 行提示 + 头部状态点联动；任务终态后 WS 正常关闭（`closed`），不触发重连。

### 5.6 页面导航

- Tab 切换：内容淡入上移 0.18s；切页重置该页滚动位置到顶部；每页独立滚动互不影响。

---

## 6. 验收清单（Wave 2 自检用）

- [ ] 390px 宽视口无横向滚动；触控目标 ≥44px。
- [ ] 全部颜色/字号/间距来自 `:root` token，无硬编码。
- [ ] 7 个后端状态徽章 + `idle` 占位态视觉与原型一致（截图比对）。
- [ ] 状态映射只存在于 `src/constants/jobState.ts` 一处。
- [ ] 日志面板自动滚动/上滑暂停/回到底部行为与原型一致。
- [ ] 触发按钮三态互斥逻辑与原型一致（含全局禁用）。
- [ ] Toast / ConfirmDialog / Skeleton 覆盖所有原 alert / confirm 场景。
- [ ] 离线可用：不引入任何 CDN/外链资源。

---

*规范版本：Wave 1 · 2026-09-04 · 基于原型 `docs/原型/bgi-prototype.html`（如原型后续调整，以原型最新视觉为准并回写本文件）。*
