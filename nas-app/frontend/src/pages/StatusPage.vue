<script setup lang="ts">
/**
 * 状态页 /（照原型 ①）
 * 大号状态卡（StatusDot 呼吸 + 中文 + 走秒计时）→ 空闲占位/当前任务卡 →
 * LogPanel（自动滚动/上滑暂停）→ 操作区（中止 + 强制清理）
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import StatusDot from '../components/StatusDot.vue'
import LogPanel from '../components/LogPanel.vue'
import Skeleton from '../components/Skeleton.vue'
import GIcon from '../components/GIcon.vue'
import { useJob, reasonZh, forceStop, abortJob } from '../composables/useJob'
import { logStreamState } from '../composables/useLogStream'
import { showConfirm } from '../composables/useConfirm'
import { toast } from '../composables/useToast'
import { JOB_STATE_MAP, isTerminalState } from '../constants'
import { mmss } from '../utils'

const { jobState, currentState, stateMeta, canAbort } = useJob()

const loading = ref(true)

const active = computed(() => {
  const st = currentState.value
  return !!jobState.job && st !== 'idle' && !isTerminalState(st) // triggering/running/completing
})

const unknown = computed(() => !!jobState.job && !(jobState.job.state in JOB_STATE_MAP))
const displayState = computed(() => unknown.value ? (jobState.job!.state as string) : stateMeta.value.zh)

const subText = computed(() => {
  const st = currentState.value
  const job = jobState.job
  if (unknown.value) return '未知状态（已与后端对账兜底）'
  if (st === 'triggering') return '正在向 Windows 监听器发送触发请求…'
  if (st === 'running') return `${job?.displayName ?? ''} · 可随时中止`
  if (st === 'completing') return '命中完成判定，正在收尾（反悔窗口）'
  if (isTerminalState(st)) return job?.displayName ?? ''
  return '等待任务触发'
})

/* ---- 计时走秒：running/completing 每 500ms 刷新；终态冻结为总用时 ---- */
const timerText = ref('')
let tickTimer: ReturnType<typeof setInterval> | null = null

function tick(): void {
  const job = jobState.job
  const st = currentState.value
  if (st === 'running' || st === 'completing') {
    if (job?.createdAtSec) {
      timerText.value = '已运行 ' + mmss(Date.now() / 1000 - job.createdAtSec)
    } else {
      timerText.value = ''
    }
  } else if (isTerminalState(st) && job) {
    // 终态冻结：用冻结的 finishedAtSec（abort/WS/轮询落终态时刻），
    // 其次用后端记录的 finished_at；都没有才用当前时刻兜底（不再持续增长）
    const endSec = job.finishedAtSec ?? (job.createdAtSec ? Date.now() / 1000 : 0)
    const elapsed = job.createdAtSec && !jobState.jobGone ? Math.max(0, endSec - job.createdAtSec) : 0
    timerText.value = '总用时 ' + mmss(elapsed)
  } else {
    timerText.value = ''
  }
}

/* ---- 中止 ---- */
async function onAbort(): Promise<void> {
  try {
    await abortJob()
  } catch (e) {
    const err = e as Error & { status?: number }
    toast(err.status === 409 ? '当前没有活动任务' : `中止失败：${err.message}`, 'error')
  }
}

/* ---- 强制清理（确认弹窗）---- */
const cleaning = ref(false)
async function onCleanup(): Promise<void> {
  const ok = await showConfirm(
    '强制清理',
    '将终止当前任务并重置监听器运行状态，可能造成任务中断。仅建议任务卡死时使用。',
    '强制清理',
  )
  if (!ok) return
  cleaning.value = true
  try {
    const { killed } = await forceStop()
    toast(killed.length ? `已强制清理：${killed.join('、')}` : '已发送强制清理指令', 'success')
  } catch (e) {
    toast(`强制清理失败：${(e as Error).message}`, 'error')
  } finally {
    cleaning.value = false
  }
}

const cleaningLabel = computed(() => (cleaning.value ? '清理中…' : '强制清理'))
const reasonText = computed(() => reasonZh(jobState.job?.completionReason ?? ''))

onMounted(() => {
  // 首次进入 300ms 骨架屏（照原型 switchTab 节奏）
  setTimeout(() => { loading.value = false }, 300)
  tick()
  tickTimer = setInterval(tick, 500)
})
onBeforeUnmount(() => { if (tickTimer) clearInterval(tickTimer) })
</script>

<template>
  <div>
    <template v-if="loading">
      <Skeleton height="92px" />
      <Skeleton height="110px" />
      <Skeleton height="300px" />
    </template>
    <template v-else>
      <!-- 大号状态指示：圆点 + 中文状态 + 走秒计时 -->
      <div class="card status-hero">
        <StatusDot :meta="stateMeta" />
        <div class="hero-main">
          <div class="hero-state" :class="{ 'hero-unknown': unknown }">{{ displayState }}</div>
          <div class="hero-sub">{{ subText }}<template v-if="reasonText">（{{ reasonText }}）</template></div>
        </div>
        <div class="hero-timer">{{ timerText }}</div>
      </div>

      <!-- 空闲占位 / 当前任务卡 -->
      <div v-if="!active" class="card">
        <div class="empty-hint">当前没有运行中的任务<br>去「任务」页选择任务，点击"执行"</div>
      </div>
      <div v-else class="card">
        <div class="sec-title" style="margin:0 0 4px;">当前任务</div>
        <div class="cj-name">{{ jobState.job?.displayName }}</div>
        <div v-if="jobState.job?.groups?.length" class="groups">
          <template v-for="(g, i) in jobState.job.groups" :key="i">
            <span v-if="i > 0" class="g-arrow">→</span>
            <span class="g-pill" :class="{ last: i === jobState.job!.groups.length - 1 }">{{ g }}</span>
          </template>
        </div>
        <div class="cj-meta">job #{{ jobState.job?.id }}<template v-if="jobState.job?.taskId"> · {{ jobState.job.taskId }}</template></div>
      </div>

      <!-- 实时日志面板：有 job 或有日志缓冲即显示（终态保留最后一屏） -->
      <LogPanel v-if="jobState.job || logStreamState.lines.length" :job-id="jobState.job?.id" />

      <!-- 主操作区：中止 + 强制清理 -->
      <div class="action-row">
        <button
          class="btn btn-secondary"
          :class="{ 'btn-attention': currentState === 'completing' }"
          :disabled="!canAbort"
          @click="onAbort"
        ><GIcon name="stop" :size="14" /> 中止</button>
        <button class="btn btn-danger" :disabled="cleaning" @click="onCleanup">
          <span v-if="cleaning" class="spinner"></span><GIcon v-else name="broom" :size="14" />{{ cleaningLabel }}
        </button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.status-hero { display: flex; align-items: center; gap: var(--space-3); padding: var(--space-5); }
.hero-main { flex: 1; min-width: 0; }
.hero-state { font-size: var(--font-2xl); font-weight: 700; line-height: 1.2; }
.hero-unknown { color: var(--state-idle-text); }
.hero-sub { font-size: var(--font-sm); color: var(--text-2); margin-top: 2px; }
.hero-timer {
  font-family: var(--font-mono); font-size: var(--font-md);
  color: var(--text-2); text-align: right; white-space: nowrap;
}
.cj-name { font-size: var(--font-lg); font-weight: 700; margin: 2px 0 6px; }
.cj-meta { font-size: var(--font-xs); color: var(--text-2); margin-top: 6px; font-family: var(--font-mono); }
.action-row { display: flex; gap: var(--space-3); }
.action-row .btn { flex: 1; }
</style>
