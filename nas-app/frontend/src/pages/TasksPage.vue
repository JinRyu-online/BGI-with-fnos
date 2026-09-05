<script setup lang="ts">
/**
 * 任务页 /tasks：TaskCard 列表；触发成功跳状态页（轮询+WS 已在 useJob 启动）。
 * 任务编辑：右上"编辑"进入管理模式 → 底部弹层表单（名称/组链/超时/收尾动作）
 * → 整体 PUT /api/tasks（Windows 写回 tasks 文件，热加载生效）。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import TaskCard from '../components/TaskCard.vue'
import Skeleton from '../components/Skeleton.vue'
import { api } from '../composables/useApi'
import { toast } from '../composables/useToast'
import { showConfirm } from '../composables/useConfirm'
import { AFTER_DONE_LABEL } from '../constants'
import { TASK_AFTER_DONE_OPTIONS, type BgiTask } from '../types'

const router = useRouter()
const tasks = ref<BgiTask[]>([])
const loading = ref(true)
const error = ref('')

const editing = ref(false) // 编辑弹层
const saving = ref(false)
const groupsText = ref('') // 组链编辑用逗号/换行分隔文本
const form = reactive({
  id: '',
  display_name: '',
  timeout_min: 90,
  after_done: 'sleep',
})
const editingId = ref('') // ''=新建
const isNew = computed(() => !editingId.value)

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    tasks.value = await api.getTasks()
  } catch (e) {
    error.value = `加载任务失败：${(e as Error).message}`
  } finally {
    // 骨架屏至少停留 300ms（照原型节奏）
    setTimeout(() => { loading.value = false }, 300)
  }
}

function onTriggered(): void {
  void router.push('/')
}

function openNew(): void {
  editingId.value = ''
  Object.assign(form, { display_name: '', timeout_min: 90, after_done: 'sleep' })
  groupsText.value = ''
  editing.value = true
}

function openEdit(t: BgiTask): void {
  editingId.value = t.id
  Object.assign(form, { display_name: t.display_name, timeout_min: t.timeout_min, after_done: t.after_done })
  groupsText.value = t.groups.join('、')
  editing.value = true
}

function parseGroups(): string[] {
  return groupsText.value
    .split(/[、,，\n]/)
    .map(g => g.trim())
    .filter(Boolean)
}

async function save(): Promise<void> {
  if (!form.display_name.trim()) { toast('请填写任务名称', 'error'); return }
  const groups = parseGroups()
  if (!groups.length) { toast('至少填写一个调度组名', 'error'); return }
  const item: BgiTask = {
    id: isNew.value ? `gui-${Date.now()}` : editingId.value,
    display_name: form.display_name.trim(),
    groups,
    timeout_min: Math.max(1, Math.min(1440, Math.floor(form.timeout_min) || 90)),
    after_done: form.after_done,
  }
  const next = [...tasks.value.filter(t => t.id !== item.id), item]
  saving.value = true
  try {
    tasks.value = await api.putTasks(next)
    editing.value = false
    toast('已保存（Windows 端热加载生效）', 'success')
  } catch (e) {
    toast(`保存失败：${(e as Error).message}`, 'error')
  } finally {
    saving.value = false
  }
}

async function remove(t: BgiTask): Promise<void> {
  const ok = await showConfirm('删除任务', `删除「${t.display_name}」？将同时从 Windows 端 tasks 文件移除。`, '删除')
  if (!ok) return
  const next = tasks.value.filter(x => x.id !== t.id)
  try {
    tasks.value = await api.putTasks(next)
    toast('已删除', 'info')
  } catch (e) {
    toast(`删除失败：${(e as Error).message}`, 'error')
  }
}

onMounted(load)

/* ---- 卡片右上角竖三点菜单（照微信/抖音卡片交互）---- */
const menuFor = ref('') // 打开菜单的任务 id（'' = 全部关闭）
const menuPos = ref({ top: 0, right: 0 }) // 菜单弹出位置（贴按钮下方，右对齐）

function toggleMenu(id: string, ev?: Event): void {
  if (menuFor.value === id) { closeMenu(); return }
  // 以按钮锚定菜单位置（fixed 坐标，随滚动会偏——弹层短暂存在可接受；
  // 点遮罩即关，滚动场景用户不会停留）
  const btn = (ev?.currentTarget as HTMLElement | undefined) ?? (ev?.target as HTMLElement)
  const r = btn.getBoundingClientRect()
  menuPos.value = { top: r.bottom + 6, right: window.innerWidth - r.right }
  menuFor.value = id
}
function closeMenu(): void {
  menuFor.value = ''
}
function menuEdit(t: BgiTask): void {
  closeMenu()
  openEdit(t)
}
function menuRemove(t: BgiTask): void {
  closeMenu()
  void remove(t)
}

</script>

<template>
  <div @click.capture="menuFor && closeMenu()">
    <template v-if="loading">
      <Skeleton height="120px" />
      <Skeleton height="120px" />
      <Skeleton height="120px" />
    </template>
    <template v-else>
      <div class="tools-row">
        <span class="tools-hint">共 {{ tasks.length }} 个任务 · 点卡片右上 ⋮ 可编辑</span>
        <button class="btn btn-secondary btn-tool" @click="openNew"><span class="plus">＋</span> 新建任务</button>
      </div>
      <div v-if="error" class="card"><div class="empty-hint">{{ error }}<br>请确认已配对设备后重试</div></div>
      <div v-else-if="!tasks.length" class="card">
        <div class="empty-hint">暂无任务<br>请先在「设置」页扫描并配对 Windows 主机，或点上方"新建任务"</div>
      </div>
      <template v-else>
        <div v-for="t in tasks" :key="t.id" class="task-wrap">
          <TaskCard :task="t" @triggered="onTriggered" />
          <div class="kebab-wrap" @click.stop>
            <button class="kebab-btn" aria-label="任务操作" @click="toggleMenu(t.id, $event)">
              <span></span><span></span><span></span>
            </button>
            <Teleport to="body">
              <template v-if="menuFor === t.id">
                <div class="menu-mask" @click="closeMenu"></div>
                <div class="menu-pop" :style="{ top: menuPos.top + 'px', right: menuPos.right + 'px' }">
                  <button class="menu-item" @click="menuEdit(t)">编辑任务</button>
                  <button class="menu-item danger" @click="menuRemove(t)">删除任务</button>
                </div>
              </template>
            </Teleport>
          </div>
        </div>
      </template>

      <!-- 任务编辑弹层：Teleport 到 body + fixed（与定时页同款，iOS 按钮不消失） -->
      <Teleport to="body">
        <div v-if="editing" class="overlay" @click.self="editing = false">
          <div class="sheet">
            <div class="sheet-grip"></div>
            <div class="sheet-head">
              <div class="sheet-title">{{ isNew ? '新建任务' : '编辑任务' }}</div>
              <button class="sheet-close" aria-label="关闭" @click="editing = false">✕</button>
            </div>
            <div class="sheet-body">
              <div class="field">
                <label>任务名称</label>
                <input v-model="form.display_name" type="text" placeholder="如：挖矿一条龙" maxlength="30">
              </div>
              <div class="field">
                <label>BetterGI 调度组（顿号/逗号分隔，按顺序执行；须与「全自动-调度器」组名逐字一致）</label>
                <textarea
                  v-model="groupsText" rows="3"
                  placeholder="如：日常一条龙、采矿、领取奖励、关闭游戏"
                ></textarea>
              </div>
              <div class="field">
                <label>超时上限（分钟，1-1440）</label>
                <input v-model.number="form.timeout_min" type="number" min="1" max="1440">
              </div>
              <div class="field">
                <label>完成后动作</label>
                <div class="after-picker">
                  <button
                    v-for="opt in TASK_AFTER_DONE_OPTIONS" :key="opt"
                    type="button" class="after-chip" :class="{ on: form.after_done === opt }"
                    @click="form.after_done = opt"
                  >{{ AFTER_DONE_LABEL[opt] || opt }}</button>
                </div>
              </div>
            </div>
            <div class="sheet-actions">
              <button class="btn btn-secondary" @click="editing = false">取消</button>
              <button class="btn btn-primary" :disabled="saving" @click="save">
                <span v-if="saving" class="spinner"></span>保存
              </button>
            </div>
          </div>
        </div>
      </Teleport>
    </template>
  </div>
</template>

<style scoped>
.tools-row { display: flex; align-items: center; justify-content: flex-end; gap: var(--space-2); margin-bottom: var(--space-2); }
.tools-hint { flex: 1; font-size: var(--font-xs); color: var(--text-3); }
.btn-tool { min-height: 36px; font-size: var(--font-sm); padding: 0 14px; }
.plus { font-size: 16px; font-weight: 700; line-height: 1; margin-right: 2px; }
.task-wrap { position: relative; }

/* 竖三点菜单按钮（卡片右上角） */
.kebab-wrap { position: absolute; top: 8px; right: 8px; z-index: 5; }
.kebab-btn {
  width: 30px; height: 30px; border: none; border-radius: 50%;
  background: var(--surface-2);
  display: inline-flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 3px; transition: background .15s, transform .06s;
}
.kebab-btn:active { transform: scale(.92); background: var(--brand-weak); }
.kebab-btn span {
  width: 3.5px; height: 3.5px; border-radius: 50%;
  background: var(--text-2); display: block;
}
/* 弹出菜单：Teleport 到 body，fixed 定位贴按钮下方右对齐 */
.menu-mask { position: fixed; inset: 0; z-index: 90; }
.menu-pop {
  position: fixed; z-index: 91;
  min-width: 140px;
  background: var(--surface);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-float);
  border: 1px solid var(--border);
  overflow: hidden;
  animation: menuIn .12s ease-out;
}
@keyframes menuIn { from { opacity: 0; transform: scale(.95); } to { opacity: 1; transform: none; } }
.menu-item {
  width: 100%; min-height: 44px;
  border: none; background: var(--surface); text-align: left;
  padding: 0 16px; font-size: var(--font-base); color: var(--text-1);
}
.menu-item + .menu-item { border-top: 1px solid var(--border); }
.menu-item:active { background: var(--surface-2); }
.menu-item.danger { color: var(--danger); }

/* 底部弹层（Teleport + fixed，与 SchedulesPage 同款） */
.overlay {
  position: fixed; inset: 0; z-index: 100;
  background: rgba(59, 74, 90, .45);
  display: flex; align-items: flex-end;
  justify-content: center;
  animation: fadeIn .15s ease-out;
}
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
.sheet {
  width: 100%; max-width: var(--shell-max);
  background: var(--surface);
  border-radius: var(--radius-lg) var(--radius-lg) 0 0;
  padding: 6px var(--space-4) 0;
  max-height: 82dvh;
  display: flex; flex-direction: column;
  animation: sheetUp .22s ease-out;
}
@keyframes sheetUp { from { transform: translateY(40%); opacity: .5; } to { transform: none; opacity: 1; } }
.sheet-grip {
  width: 40px; height: 4px; border-radius: 2px;
  background: var(--border-strong); margin: 6px auto var(--space-1);
  flex-shrink: 0;
}
.sheet-head { display: flex; align-items: center; gap: var(--space-2); padding: var(--space-1) 0 var(--space-2); flex-shrink: 0; }
.sheet-title { flex: 1; font-size: var(--font-lg); font-weight: 700; }
.sheet-close {
  flex-shrink: 0;
  width: 28px; height: 28px; border: none; border-radius: 50%;
  background: var(--surface-2); color: var(--text-3);
  font-size: 12px; line-height: 1;
  display: inline-flex; align-items: center; justify-content: center;
}
.sheet-close:active { background: var(--danger-weak); color: var(--danger); }
.sheet-body { overflow-y: auto; -webkit-overflow-scrolling: touch; min-height: 0; }
.sheet-actions {
  flex-shrink: 0;
  display: flex; gap: var(--space-3);
  margin: var(--space-2) calc(-1 * var(--space-4)) 0;
  padding: var(--space-3) var(--space-4) calc(var(--space-3) + env(safe-area-inset-bottom));
  background: var(--surface);
  border-top: 1px solid var(--border);
}
.sheet-actions .btn { flex: 1; }
.field { margin-bottom: var(--space-3); flex-shrink: 0; }
.field > label { display: block; font-size: var(--font-sm); color: var(--text-2); margin-bottom: 5px; line-height: 1.5; }
.field input, .field textarea {
  width: 100%; min-height: 44px;
  border: 1px solid var(--border-strong); border-radius: var(--radius-md);
  padding: 10px 12px; font-size: var(--font-base);
  background: var(--surface); color: var(--text-1); outline: none;
  font-family: inherit;
}
.field textarea { resize: vertical; min-height: 72px; }
.field input:focus, .field textarea:focus { border-color: var(--brand); box-shadow: 0 0 0 3px var(--brand-weak); }
.after-picker { display: flex; gap: 6px; flex-wrap: wrap; }
.after-chip {
  min-height: 38px; padding: 0 16px;
  border: 1px solid var(--border); border-radius: var(--radius-full);
  background: var(--surface-2); color: var(--text-2); font-size: var(--font-sm);
}
.after-chip.on { background: var(--brand-weak); border-color: var(--brand); color: var(--brand-strong); font-weight: 600; }
</style>
