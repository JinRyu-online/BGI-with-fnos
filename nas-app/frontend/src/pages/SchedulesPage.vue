<script setup lang="ts">
/**
 * 定时任务页 /schedules：
 * 列表卡（名称/时间/星期 chips/启停开关/下次触发/上次结果）→ 新增编辑弹层 → 删除确认。
 * 后端算好 next_fire_at；编辑走整体 PUT（config.schedules 语义）。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import Skeleton from '../components/Skeleton.vue'
import GIcon from '../components/GIcon.vue'
import { api } from '../composables/useApi'
import { loadConfig } from '../composables/useConfig'
import { toast } from '../composables/useToast'
import { showConfirm } from '../composables/useConfirm'
import { WEEKDAY_LABELS, scheduleResultMeta } from '../constants'
import type { BgiTask, ScheduleItem } from '../types'

const schedules = ref<ScheduleItem[]>([])
const tasks = ref<BgiTask[]>([])
const loading = ref(true)

const editing = ref(false) // 弹层开关
const editingIndex = ref(-1) // -1=新建，>=0=编辑（弹层标题用）
const saving = ref(false)
const runningId = ref('') // 手动执行 spinner
const timePicker = ref(false)
const taskPicker = ref(false)
const tpHour = ref(12)
const tpMinute = ref(0)

function taskNameOf(id: string): string {
  return tasks.value.find(t => t.id === id)?.display_name ?? ''
}

function openTimePicker(): void {
  const [h, m] = form.time.split(':').map(Number)
  tpHour.value = Number.isFinite(h) ? h : 12
  tpMinute.value = Number.isFinite(m) ? Math.round(m / 5) * 5 % 60 : 0
  timePicker.value = true
}

function applyTime(): void {
  form.time = `${String(tpHour.value).padStart(2, '0')}:${String(tpMinute.value).padStart(2, '0')}`
  timePicker.value = false
}

function pickTask(t: BgiTask): void {
  form.task_id = t.id
  taskPicker.value = false
}

/** 空白编辑表单（新增与编辑共用） */
const form = reactive({
  id: '',
  enabled: true,
  name: '',
  time: '12:00',
  weekdays: [] as number[],
  task_id: '',
  wake: true,
  wake_timeout_sec: 300,
  skip_if_busy: true,
})

const knownTasks = computed(() => tasks.value.map(t => t.id))
const nameErr = computed(() => (form.task_id && !knownTasks.value.includes(form.task_id) && tasks.value.length)
  ? 'Windows 端暂无此任务（可离线保存，触发时会失败）' : '')

function fmtNext(ts: number | null): string {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  const week = WEEKDAY_LABELS[(d.getDay() + 6) % 7]
  const hhmm = d.toTimeString().slice(0, 5)
  return `${week} ${hhmm}`
}

function fmtLast(ts: number | null): string {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  return `${d.getMonth() + 1}/${d.getDate()} ${d.toTimeString().slice(0, 5)}`
}

async function refresh(): Promise<void> {
  try {
    const [scheds, ts] = await Promise.all([api.getSchedules(), api.getTasks().catch(() => [] as BgiTask[])])
    schedules.value = scheds
    tasks.value = ts
  } catch (e) {
    toast(`加载定时任务失败：${(e as Error).message}`, 'error')
  } finally {
    loading.value = false
  }
}

function openNew(): void {
  Object.assign(form, {
    id: `s-${Date.now()}`,
    enabled: true, name: '', time: '12:00',
    weekdays: [], task_id: tasks.value[0]?.id ?? '',
    wake: true, wake_timeout_sec: 300, skip_if_busy: true,
  })
  editingIndex.value = -1
  editing.value = true
}

function openEdit(s: ScheduleItem): void {
  Object.assign(form, {
    id: s.id, enabled: s.enabled, name: s.name, time: s.time,
    weekdays: [...s.weekdays], task_id: s.task_id,
    wake: s.wake, wake_timeout_sec: s.wake_timeout_sec, skip_if_busy: s.skip_if_busy,
  })
  editingIndex.value = schedules.value.findIndex(x => x.id === s.id)
  editing.value = true
}

function toggleWeekday(d: number): void {
  const i = form.weekdays.indexOf(d)
  if (i >= 0) form.weekdays.splice(i, 1)
  else form.weekdays.push(d)
}

async function save(): Promise<void> {
  if (!form.name.trim()) { toast('请填写名称', 'error'); return }
  if (!/^\d{1,2}:\d{2}$/.test(form.time)) { toast('时间格式应为 HH:MM', 'error'); return }
  const item: ScheduleItem = {
    id: form.id, enabled: form.enabled, name: form.name.trim(), time: form.time,
    weekdays: [...form.weekdays].sort(), task_id: form.task_id,
    wake: form.wake, wake_timeout_sec: form.wake_timeout_sec, skip_if_busy: form.skip_if_busy,
    next_fire_at: null, last_fired_at: null, last_result: null, last_error: null,
  }
  const next = [...schedules.value.filter(s => s.id !== item.id), item]
  saving.value = true
  try {
    schedules.value = await api.putSchedules(next)
    editing.value = false
    toast('已保存', 'success')
  } catch (e) {
    toast(`保存失败：${(e as Error).message}`, 'error')
  } finally {
    saving.value = false
  }
}

async function toggleEnabled(s: ScheduleItem): Promise<void> {
  const next = schedules.value.map(x => x.id === s.id ? { ...x, enabled: !x.enabled } : x)
  try {
    schedules.value = await api.putSchedules(next)
  } catch (e) {
    toast(`操作失败：${(e as Error).message}`, 'error')
  }
}

async function remove(s: ScheduleItem): Promise<void> {
  const ok = await showConfirm('删除定时任务', `删除「${s.name}」？该任务将不再自动执行。`, '删除')
  if (!ok) return
  const next = schedules.value.filter(x => x.id !== s.id)
  try {
    schedules.value = await api.putSchedules(next)
    toast('已删除', 'info')
  } catch (e) {
    toast(`删除失败：${(e as Error).message}`, 'error')
  }
}

async function runNow(s: ScheduleItem): Promise<void> {
  if (runningId.value) return
  runningId.value = s.id
  try {
    await api.runSchedule(s.id)
    toast(`已派发「${s.name}」`, 'success')
    // 稍后刷新结果（worker 异步）
    setTimeout(refresh, 1500)
  } catch (e) {
    toast(`派发失败：${(e as Error).message}`, 'error')
  } finally {
    runningId.value = ''
  }
}

onMounted(async () => {
  if (!useConfigReady()) await loadConfig().catch(() => undefined)
  await refresh()
})

function useConfigReady(): boolean {
  return false // 首次进入总是 load 一次配置（保持与其他页一致的最小行为）
}
</script>

<template>
  <div>
    <template v-if="loading">
      <Skeleton height="84px" />
      <Skeleton height="84px" />
    </template>
    <template v-else>
      <div v-if="!schedules.length" class="card">
        <div class="empty-hint">还没有定时任务<br>点下方按钮新建（如：周一/周四 12:00 挖矿一条龙）</div>
      </div>

      <div v-for="s in schedules" :key="s.id" class="card sched-card">
        <div class="sc-head">
          <div class="sc-name" :class="{ off: !s.enabled }">{{ s.name }}</div>
          <label class="sc-switch" :title="s.enabled ? '点击停用' : '点击启用'">
            <input type="checkbox" :checked="s.enabled" @change="toggleEnabled(s)">
            <span class="slider"></span>
          </label>
        </div>
        <div class="sc-meta">
          <span class="sc-time"><GIcon name="hourglass" :size="13" /> {{ s.time }}</span>
          <span class="sc-days">
            <button
              v-for="(w, i) in WEEKDAY_LABELS" :key="w"
              class="day-chip" :class="{ on: !s.weekdays.length || s.weekdays.includes(i), fixed: !s.weekdays.length }"
              disabled
            >{{ w }}</button>
          </span>
        </div>
        <div class="sc-info">
          <div>下次：{{ fmtNext(s.next_fire_at) }}</div>
          <div class="sc-last">
            上次：{{ fmtLast(s.last_fired_at) }}
            <span v-if="scheduleResultMeta(s.last_result)" class="badge" :class="scheduleResultMeta(s.last_result)!.cls">
              {{ scheduleResultMeta(s.last_result)!.zh }}
            </span>
          </div>
        </div>
        <div v-if="s.last_error" class="sc-err">{{ s.last_error }}</div>
        <div class="sc-actions">
          <button class="btn btn-secondary btn-sm" @click="openEdit(s)">编辑</button>
          <button class="btn btn-secondary btn-sm" :disabled="!!runningId" @click="runNow(s)">
            <span v-if="runningId === s.id" class="spinner spinner-dark"></span><GIcon v-else name="play" :size="13" /> 立即执行
          </button>
          <button class="btn btn-danger-outline btn-sm" @click="remove(s)">删除</button>
        </div>
      </div>

      <button class="btn btn-primary btn-block" @click="openNew"><GIcon name="check" :size="14" /> 新建定时任务</button>

      <!-- 编辑弹层 -->
      <div v-if="editing" class="overlay" @click.self="editing = false">
        <div class="sheet">
          <div class="sheet-grip"></div>
          <div class="sheet-title">{{ editingIndex >= 0 ? '编辑定时任务' : '新建定时任务' }}</div>

          <div class="field">
            <label>名称</label>
            <input v-model="form.name" type="text" placeholder="如：挖矿一条龙" maxlength="20">
          </div>
          <div class="field">
            <label>触发时间</label>
            <button type="button" class="select-sim" @click="openTimePicker">
              <span class="ss-value">{{ form.time }}</span>
              <GIcon name="hourglass" :size="14" />
            </button>
          </div>
          <div class="field">
            <label>重复（不选 = 每天）</label>
            <div class="day-picker">
              <button
                v-for="(w, i) in WEEKDAY_LABELS" :key="w"
                type="button" class="day-chip pick" :class="{ on: form.weekdays.includes(i) }"
                @click="toggleWeekday(i)"
              >{{ w }}</button>
            </div>
          </div>
          <div class="field">
            <label>执行任务</label>
            <button type="button" class="select-sim" @click="taskPicker = true">
              <span class="ss-value" :class="{ dim: !taskNameOf(form.task_id) }">{{ taskNameOf(form.task_id) || '选择任务' }}</span>
              <span class="ss-arrow">›</span>
            </button>
            <div v-if="nameErr" class="warn">{{ nameErr }}</div>
          </div>
          <div class="field row">
            <label class="check"><input v-model="form.wake" type="checkbox"> 先 WOL 唤醒（PC 常开可关）</label>
          </div>
          <div class="field row">
            <label class="check"><input v-model="form.skip_if_busy" type="checkbox"> Windows 忙时跳过本次</label>
          </div>

          <!-- sticky 底部操作条：iOS 键盘/小屏下按钮固定可见，绝不随内容滚没 -->
          <div class="sheet-actions">
            <button class="btn btn-secondary" @click="editing = false">取消</button>
            <button class="btn btn-primary" :disabled="saving" @click="save">
              <span v-if="saving" class="spinner"></span>保存
            </button>
          </div>
        </div>
      </div>

      <!-- 时间选择（自绘滚轮式面板，替代原生 time input 的系统样式差异） -->
      <div v-if="timePicker" class="overlay center" @click.self="timePicker = false">
        <div class="picker-card">
          <div class="picker-title">触发时间</div>
          <div class="tp-row">
            <select v-model.number="tpHour" class="tp-select">
              <option v-for="h in 24" :key="h - 1" :value="h - 1">{{ String(h - 1).padStart(2, '0') }} 时</option>
            </select>
            <span class="tp-colon">:</span>
            <select v-model.number="tpMinute" class="tp-select">
              <option v-for="m in 12" :key="(m - 1) * 5" :value="(m - 1) * 5">{{ String((m - 1) * 5).padStart(2, '0') }} 分</option>
            </select>
          </div>
          <div class="sheet-actions">
            <button class="btn btn-secondary" @click="timePicker = false">取消</button>
            <button class="btn btn-primary" @click="applyTime">确定</button>
          </div>
        </div>
      </div>

      <!-- 任务选择（底部列表弹层，替代原生 select 的系统样式差异） -->
      <div v-if="taskPicker" class="overlay" @click.self="taskPicker = false">
        <div class="sheet">
          <div class="sheet-grip"></div>
          <div class="sheet-title">选择执行任务</div>
          <div class="task-list">
            <button
              v-for="t in tasks" :key="t.id"
              type="button" class="task-opt" :class="{ sel: t.id === form.task_id }"
              @click="pickTask(t)"
            >
              <span class="to-name">{{ t.display_name }}</span>
              <span class="to-sub">{{ t.groups.join(' → ') }}</span>
              <span v-if="t.id === form.task_id" class="to-check"><GIcon name="check" :size="14" /></span>
            </button>
            <div v-if="!tasks.length" class="empty-hint">未获取到任务列表<br>请确认已配对 Windows 设备</div>
          </div>
          <div class="sheet-actions">
            <button class="btn btn-secondary" @click="taskPicker = false">取消</button>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.sched-card.off .sc-name { color: var(--text-3); }
.sc-head { display: flex; align-items: center; gap: var(--space-2); }
.sc-name { flex: 1; font-size: var(--font-lg); font-weight: 700; }
.sc-meta { display: flex; align-items: center; gap: var(--space-3); margin-top: 6px; flex-wrap: wrap; }
.sc-time { font-family: var(--font-mono); font-size: var(--font-md); color: var(--brand-strong); font-weight: 600; display: inline-flex; align-items: center; gap: 4px; }
.sc-days { display: flex; gap: 4px; }
.day-chip {
  width: 26px; height: 26px; border-radius: 8px;
  border: 1px solid var(--border); background: var(--surface-2);
  font-size: 12px; color: var(--text-3);
  display: inline-flex; align-items: center; justify-content: center;
}
.day-chip.on { background: var(--brand-weak); border-color: var(--brand); color: var(--brand-strong); font-weight: 600; }
.day-chip.fixed { opacity: .45; }
.sc-info { margin-top: 8px; font-size: var(--font-xs); color: var(--text-2); display: flex; flex-direction: column; gap: 3px; }
.sc-last { display: flex; align-items: center; gap: 6px; }
.sc-err { margin-top: 6px; font-size: var(--font-xs); color: var(--danger); }
.sc-actions { display: flex; gap: var(--space-2); margin-top: var(--space-3); }
.btn-sm { min-height: 36px; font-size: var(--font-sm); padding: 0 14px; }
button.btn-block { margin-top: var(--space-3); }

/* 启停开关 */
.sc-switch { position: relative; width: 44px; height: 26px; flex-shrink: 0; }
.sc-switch input { opacity: 0; width: 0; height: 0; }
.sc-switch .slider {
  position: absolute; inset: 0; border-radius: var(--radius-full);
  background: var(--border-strong); transition: background .2s;
}
.sc-switch .slider::after {
  content: ''; position: absolute; top: 3px; left: 3px;
  width: 20px; height: 20px; border-radius: 50%; background: #fff;
  transition: transform .2s; box-shadow: 0 1px 3px rgba(0,0,0,.25);
}
.sc-switch input:checked + .slider { background: var(--brand); }
.sc-switch input:checked + .slider::after { transform: translateX(18px); }

/* 底部弹层（编辑表单） */
.overlay {
  position: absolute; inset: 0; z-index: 30;
  background: rgba(59, 74, 90, .4);
  display: flex; align-items: flex-end;
}
.overlay.center { align-items: center; justify-content: center; padding: var(--space-5); }
.picker-card {
  width: 100%; max-width: 320px; background: var(--surface);
  border-radius: var(--radius-lg); padding: var(--space-4);
  box-shadow: var(--shadow-float);
  animation: sheetUp .22s ease-out;
}
@keyframes sheetUp { from { transform: translateY(40%); opacity: .5; } to { transform: none; opacity: 1; } }
.picker-title { font-size: var(--font-md); font-weight: 700; text-align: center; margin-bottom: var(--space-3); }
.tp-row { display: flex; align-items: center; justify-content: center; gap: var(--space-2); margin-bottom: var(--space-2); }
.tp-colon { font-size: var(--font-xl); font-weight: 700; color: var(--text-2); }
.tp-select {
  min-height: 52px; padding: 0 14px;
  border: 1px solid var(--border-strong); border-radius: var(--radius-md);
  background: var(--surface-2); color: var(--text-1);
  font-size: var(--font-lg); font-weight: 600; font-family: var(--font-mono);
  outline: none; appearance: none; -webkit-appearance: none;
  text-align: center; text-align-last: center;
}
.tp-select:focus { border-color: var(--brand); box-shadow: 0 0 0 3px var(--brand-weak); }
.sheet {
  width: 100%; background: var(--surface);
  border-radius: var(--radius-lg) var(--radius-lg) 0 0;
  padding: 6px var(--space-4) 0;
  max-height: 82dvh;
  display: flex; flex-direction: column;
  animation: sheetUp .22s ease-out;
}
.sheet-grip {
  width: 40px; height: 4px; border-radius: 2px;
  background: var(--border-strong); margin: 6px auto var(--space-2);
  flex-shrink: 0;
}
.sheet-title { font-size: var(--font-lg); font-weight: 700; margin-bottom: var(--space-3); flex-shrink: 0; }
.sheet-actions {
  /* sticky 底部操作条：iOS 小屏/键盘弹起时按钮固定可见，绝不随内容滚出屏幕 */
  position: sticky; bottom: 0;
  display: flex; gap: var(--space-3);
  margin: var(--space-2) calc(-1 * var(--space-4)) 0;
  padding: var(--space-3) var(--space-4) calc(var(--space-3) + env(safe-area-inset-bottom));
  background: var(--surface);
  border-top: 1px solid var(--border);
  flex-shrink: 0;
}
.sheet-actions .btn { flex: 1; }
.sheet > .field, .sheet > .task-list, .sheet > .warn { flex-shrink: 0; }
.sheet { overflow-y: auto; }
.task-list { overflow-y: visible; }
.field > label { display: block; font-size: var(--font-sm); color: var(--text-2); margin-bottom: 5px; }
.field input[type="text"], .field input[type="time"], .field select {
  width: 100%; min-height: 44px;
  border: 1px solid var(--border-strong); border-radius: var(--radius-md);
  padding: 0 12px; font-size: var(--font-base);
  background: var(--surface); color: var(--text-1); outline: none;
  font-family: inherit;
}
.field input:focus, .field select:focus { border-color: var(--brand); box-shadow: 0 0 0 3px var(--brand-weak); }
.day-picker { display: flex; gap: 6px; }
.day-chip.pick { cursor: pointer; }
.field.row .check { display: flex; align-items: center; gap: 8px; font-size: var(--font-base); color: var(--text-1); }
.field.row .check input { width: 18px; height: 18px; accent-color: var(--brand); }
.warn { margin-top: 5px; font-size: var(--font-xs); color: var(--warning); }

/* 自绘选择器（触发时间/执行任务）：模拟 iOS 风格列表行，点开子弹层，规避原生
   select/time input 在 Windows/Android/iOS 上样式不一致的问题 */
.select-sim {
  width: 100%; min-height: 44px;
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  border: 1px solid var(--border-strong); border-radius: var(--radius-md);
  padding: 0 12px; font-size: var(--font-base);
  background: var(--surface); color: var(--text-1);
}
.select-sim:active { background: var(--surface-2); }
.ss-value { flex: 1; text-align: left; font-weight: 500; }
.ss-value.dim { color: var(--text-3); }
.ss-arrow { color: var(--text-3); font-size: 18px; line-height: 1; }

/* 任务选择列表 */
.task-list { margin-bottom: var(--space-2); }
.task-opt {
  width: 100%; min-height: 52px;
  display: flex; flex-direction: column; align-items: flex-start; gap: 2px;
  position: relative;
  border: none; border-bottom: 1px solid var(--border);
  background: var(--surface); text-align: left;
  padding: 8px 30px 8px 4px;
}
.task-opt:last-of-type { border-bottom: none; }
.task-opt:active { background: var(--surface-2); }
.task-opt.sel { background: var(--brand-weak); }
.to-name { font-size: var(--font-base); font-weight: 600; color: var(--text-1); }
.to-sub { font-size: var(--font-xs); color: var(--text-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; }
.to-check { position: absolute; right: 6px; top: 50%; transform: translateY(-50%); color: var(--brand-strong); }
.spinner-dark { border-color: rgba(125, 117, 102, .35); border-top-color: var(--text-2); }
</style>
