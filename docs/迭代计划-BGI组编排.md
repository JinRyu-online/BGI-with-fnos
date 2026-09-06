# 迭代计划：iOS 遗留两处 UI 修复 + 任务配置编排化（拉取 BetterGI 调度组）

> 2026-09-06 v2 · 经 subagent 架构评审修订（5 条必须项已全部吸收）。
> 分支：feat/scheduled-tasks（追加 commit）。

## 0. 背景与新发现（问题 3 的核心情报）

**BetterGI 调度组真实存储格式**（读源码 ScriptGroup.cs / OneDragonFlowViewModel.cs / ScriptService.cs 确认）：

- 路径：`<BetterGI安装目录>\User\ScriptGroup\<组名>.json`（一个组一个文件，UTF-8 无 BOM）
- 结构：
```json
{
  "Index": 0,
  "Name": "挖矿一条龙",
  "Config": { "PathingConfig": {}, "ShellConfig": {}, "EnableShellConfig": false },
  "Projects": [ { "Index": 0, "Name": "硫晶石采集", "FolderName": "矿石采集",
                  "Type": "Pathing", "Status": "Enabled", "Schedule": "Daily", "RunNum": 1 } ]
}
```
- **我们系统的 `groups` 参数即 `<组名>.json` 的文件名集合**：`BetterGI.exe --startGroups 挖矿一条龙 关闭游戏`。
- 结论：Windows 端直接读 `User\ScriptGroup\*.json` 文件名枚举"用户电脑上已有的调度组"——零风险、不需要 BetterGI 配合。

## 1. 问题 1：iOS 历史页"刷新"按钮换行 → 单行横滑 + 钉右

`.chip-row` 改单行横向滑动容器（`flex-wrap: nowrap`），刷新按钮 `position: sticky; right: 0`（老 iOS 加 `-webkit-sticky`）钉在可视区右缘，保留 `margin-left:auto`。隐藏滚动条（`scrollbar-width:none` + `::-webkit-scrollbar{display:none}`）。

**评审坑 A（必须）**：`overflow-x:auto` 会强制 `overflow-y:auto`，`.chip::after { inset:-4px }` 触控热区会被裁剪并可能纵向滚。处理：容器 `padding: 4px 0; margin: -4px 0` 补偿，热区不再越界。
**评审坑 B（必须）**：渐变提示遮罩（如加）必须 `pointer-events: none`。第一版不加渐变遮罩（可滑距离小，收益低），规避此项。

**验收**：390px 视口刷新按钮与筛选 chip 同行、贴右缘；chip-row 内无纵向滚动条/热区裁切；无按钮内文字换行。

## 2. 问题 2：抽屉标题居中 → grid 三槽

`sheet-head` 改 `display:grid; grid-template-columns: 44px 1fr 44px; align-items:center`：
- **左槽**：仅占位（编辑/新建抽屉关闭走右上 ✕——维持现状，评审消除矛盾选定此项；选择类抽屉左槽空占位）
- **中槽**：标题 `text-align:center; min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis`（防 1fr 被 320px 视口长标题撑破）
- **右槽**：编辑/新建抽屉 = ✕（关闭）；时间抽屉 = ✓（确认，金色 `.sheet-ok`）；任务选择抽屉 = ✕
- 按钮命中区 44px：按钮本体 28px 圆 + `::after inset:-8px` 外扩热区（项目"触控 ≥44px"既有约定）
- **SchedulesPage.vue 与 TasksPage.vue 两份 scoped 副本都要改**（弹层样式两处独立，漏一处即发散）

**验收**：截图同视口标题严格居中（左右槽等宽 44px）；按钮命中区 ≥44px。

## 3. 问题 3：任务编辑"调度组"编排化

### 3.1 Windows 端（新增）

- `AppDeps` 新增字段 `bgi_groups_reader: Callable[[], list[str]] | None = None`（**DI 注入，不在端点层读 config.toml**——遵守 AGENTS.md 第 5 条依赖注入硬约定）。
- `listener.py` 装配处实现默认 reader：从 `ListenerConfig`（config.py 已解析的 `bettergi.dir`）拼 `<dir>/User/ScriptGroup`，`Path(dir).glob("*.json")` 取 stem 排序返回；`bettergi.dir` 为空或目录不存在 → 返回 `[]`。
  - **不使用 `config_path` 推导**（评审指正：config_path 指向 BetterGI 配置目录，与 ScriptGroup 是两处；组目录唯一来源是 `bettergi.dir`）。如需自定义位置，后续新增 `bettergi.script_group_dir`（本轮不做，YAGNI）。
- `bgi_trigger/api/app.py`：`GET /bgi/groups`，**经 authenticate 鉴权依赖**（不是 /key 那样的豁免端点），返回 `{"groups": [...]}`；deps.bgi_groups_reader 为 None → 返回 `{"groups": []}`。
- `windows-listener/api/openapi.yaml` 补端点；**AGENTS.md / README 的"7 个端点"计数同步改 8**。
- 测试 `test_bgi_groups.py`：tmp 造组 JSON → 组名列表；reader 返回 None 注入 → 空列表；401 未鉴权；`bettergi.dir` 空 → 空。

### 3.2 NAS 端（代理）

- `listener_client.py`：`bgi_groups() -> list[str]`（GET /bgi/groups，取 `["groups"]`）。
- `main.py`：`GET /api/bgi-groups` 代理。**404（旧版 listener 未升级）→ 返回 `{"groups": []}` 而非 502**（旧版兼容：前端自然走 fallback）；401→401；其他 ListenerError→502；未配对→400。

### 3.3 前端（任务编辑抽屉组选择双轨）

- 打开编辑器时并行拉 `GET /api/bgi-groups`：
  - **非空** → 组勾选列表（checkbox chip 多选）；**勾选顺序即执行顺序**；勾选行上方"已选顺序"pill 行展示 `① ② ③`；任务存量 groups 中的组默认预勾选。
  - **空/代理 404/502** → 原手写 textarea（占位提示"未获取到 BetterGI 调度组，可手动输入组名"）。注意：TasksPage 打开编辑器前提是 getTasks() 成功（Windows 在线），fallback 实际触发条件是"端点失败/返回空"。
- **存量任务含已删组（评审重点）**：预勾选时对 groups 中不在接口返回列表里的名字，**追加显示为"未知组：<名>"chip 且保持勾选**，保存原样带回——绝不静默丢弃。
- 保存仍走 `PUT /api/tasks`（groups 数组结构不变）；`--startGroups` 语义不变。
- 交互参考：iOS 提醒事项多选清单 + 微信权限选择列表（行右对勾）；第一版不做拖拽排序。

### 3.4 测试与验收

- Windows：`test_bgi_groups.py` 4 例（见 3.1）+ 两端全量 pytest 回归。
- NAS：`/api/bgi-groups` 3 例（正常透传 / 未配对 400 / 旧版 404 → groups:[]）。
- 前端：`npm run build:deploy`（产物刷进 static/spa/，**NAS 上才可见**）+ smoke；手动验收枚举：
  1. 在线非空 → 勾选列表 + 顺序 pill 正确
  2. 返回空 → textarea fallback
  3. 代理 502 / 旧版 404 → textarea fallback
  4. 编辑含已删组的存量任务 → "未知组" chip 保留、保存不丢
  5. 勾选顺序 = 执行顺序显示正确
- 门禁：提交前两端 pytest 全绿。

## 4. 实施顺序（单 commit 三块）

1. UI 修复（问题 1/2，含两页 scoped 副本同步）
2. Windows /bgi/groups + NAS 代理 + 测试
3. 任务编辑抽屉组选择双轨 UI + build:deploy + 全量回归
