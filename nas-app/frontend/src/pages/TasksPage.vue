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
import GIcon from '../components/GIcon.vue'
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
</script>

<template>
  <div>
    <template v-if="loading">
      <Skeleton height="120px" />
      <Skeleton height="120px" />
      <Skeleton height="120px" />
    </template>
    <template v-else>
      <div class="tools-row">
        <span class="tools-hint">{{ tasks.length ? '长按任务卡可编辑' : '' }}</span>
        <button class="btn btn-secondary btn-tool" @click="openNew"><GIcon name="check" :size="13" /> 新建任务</button>
      </div>
      <div v-if="error" class="card"><div class="empty-hint">{{ error }}<br>请确认已配对设备后重试</div></div>
      <div v-else-if="!tasks.length" class="card">
        <div class="empty-hint">暂无任务<br>请先在「设置」页扫描并配对 Windows 主机，或点右上"新建任务"</div>
      </div>
      <template v-else>
        <div v-for="t in tasks" :key="t.id" class="task-wrap">
          <TaskCard :task="t" @triggered="onTriggered" />
          <div class="task-tools">
            <button class="tool-btn" @click="openEdit(t)">编辑</button>
            <span class="tool-sep">·</span>
            <button class="tool-btn danger" @click="remove(t)">删除</button>
          </div>
        </div>
      </template>

      <!-- 任务编辑弹层 -->
      <div v-if="editing" class="overlay" @click.self="editing = false">
        <div class="sheet">
          <div class="sheet-grip"></div>
          <div class="sheet-title">{{ isNew ? '新建任务' : '编辑任务' }}</div>
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
          <div class="sheet-actions">
            <button class="btn btn-secondary" @click="editing = false">取消</button>
            <button class="btn btn-primary" :disabled="saving" @click="save">
              <span v-if="saving" class="spinner"></span>保存
            </button>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.tools-row { display: flex; align-items: center; justify-content: flex-end; gap: var(--space-2); margin-bottom: var(--space-2); }
.tools-hint { flex: 1; font-size: var(--font-xs); color: var(--text-3); }
.btn-tool { min-height: 36px; font-size: var(--font-sm); padding: 0 14px; }
.task-wrap { position: relative; }
.task-tools {
  position: absolute; top: 10px; right: 12px;
  display: flex; align-items: center; gap: 6px;
  z-index: 2;
}
.tool-btn {
  border: none; background: var(--surface-2); color: var(--text-2);
  font-size: var(--font-xs); padding: 3px 10px; border-radius: var(--radius-full);
}
.tool-btn.danger { color: var(--danger); }
.tool-sep { color: var(--text-3); font-size: var(--font-xs); }

/* 底部弹层（同 SchedulesPage 风格） */
.overlay {
  position: absolute; inset: 0; z-index: 30;
  background: rgba(59, 74, 90, .4);
  display: flex; align-items: flex-end;
}
.sheet {
  width: 100%; background: var(--surface);
  border-radius: var(--radius-lg) var(--radius-lg) 0 0;
  padding: 6px var(--space-4) 0;
  max-height: 82dvh;
  display: flex; flex-direction: column;
  overflow-y: auto;
  animation: sheetUp .22s ease-out;
}
@keyframes sheetUp { from { transform: translateY(40%); opacity: .5; } to { transform: none; opacity: 1; } }
.sheet-grip {
  width: 40px; height: 4px; border-radius: 2px;
  background: var(--border-strong); margin: 6px auto var(--space-2);
  flex-shrink: 0;
}
.sheet-title { font-size: var(--font-lg); font-weight: 700; margin-bottom: var(--space-3); flex-shrink: 0; }
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
.sheet-actions {
  position: sticky; bottom: 0;
  display: flex; gap: var(--space-3);
  margin: var(--space-2) calc(-1 * var(--space-4)) 0;
  padding: var(--space-3) var(--space-4) calc(var(--space-3) + env(safe-area-inset-bottom));
  background: var(--surface);
  border-top: 1px solid var(--border);
  flex-shrink: 0;
}
.sheet-actions .btn { flex: 1; }
</style>
