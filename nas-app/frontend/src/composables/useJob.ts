/* ============================================================
 * 单活 job 状态管理（模块级 reactive 单例，轻量替代 Pinia）
 * - 当前活动 job：id / state / startedAt（Unix 秒，用于恢复计时）
 * - pollStatus：按 config.poll.interval_sec（默认 10s）轮询；
 *   404 → 停止轮询并标记 job_gone（任务已从 listener 历史清理，照旧版 GUI）
 * - abort / stop / triggerTask 动作
 * - 页面刷新恢复：loadJobs 后若有 running/completing 记录 → 恢复轮询 + WS（resumeActiveJob）
 * ============================================================ */
import { computed, reactive } from 'vue'
import { api } from './useApi'
import { isTerminalState, JOB_STATE_MAP, COMPLETION_REASON_LABEL, type JobState } from '../constants'
import type { JobRecord } from '../types'
import { useLogStream } from './useLogStream'
import { toast } from './useToast'

const DEFAULT_POLL_INTERVAL_SEC = 10

export interface ActiveJob {
  id: string
  taskId: string
  displayName: string
  groups: string[]
  state: JobState
  completionReason: string
  /** 任务开始时刻（Unix 秒），0 表示未开始（triggering 中） */
  createdAtSec: number
}

interface JobStateStore {
  job: ActiveJob | null
  /** 前端瞬时态：触发请求进行中 */
  triggering: boolean
  triggeringTaskId: string
  /** 轮询标记：job 已被 listener 清理 */
  jobGone: boolean
  loadingJobs: boolean
}

export const jobState = reactive<JobStateStore>({
  job: null,
  triggering: false,
  triggeringTaskId: '',
  jobGone: false,
  loadingJobs: false,
})

let pollTimer: ReturnType<typeof setInterval> | null = null
let pollIntervalMs = DEFAULT_POLL_INTERVAL_SEC * 1000

const { open: openLogStream, close: closeLogStream } = useLogStream()

/** 当前状态（含 triggering 瞬时态与 idle 兜底）。 */
export const currentState = computed<JobState>(() => {
  if (jobState.job) return jobState.job.state
  if (jobState.triggering) return 'triggering'
  return 'idle'
})

export const stateMeta = computed(() => JOB_STATE_MAP[currentState.value] ?? JOB_STATE_MAP.idle)

/** 是否未知状态（后端新增/对账兜底，JOB_STATE_MAP 未覆盖）。 */
export const isUnknownState = computed<boolean>(() => !!jobState.job && !Object.hasOwn(JOB_STATE_MAP, jobState.job.state))

/** 是否有非终态任务占用（triggering/running/completing），用于全局禁用触发按钮。 */
export const hasActiveJob = computed<boolean>(() => {
  if (jobState.triggering) return true
  return !!jobState.job && !isTerminalState(jobState.job.state)
})

/** 是否可中止（running/completing）。 */
export const canAbort = computed<boolean>(() => currentState.value === 'running' || currentState.value === 'completing')

export function stopPolling(): void {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

function setJobState(state: string, reason = ''): void {
  if (!jobState.job) return
  jobState.job.state = (Object.hasOwn(JOB_STATE_MAP, state) ? state : 'unknown') as JobState
  if (reason) jobState.job.completionReason = reason
}

function handleTerminal(state: string, reason: string, opts: { notify?: boolean } = {}): void {
  setJobState(state, reason)
  stopPolling()
  closeLogStream()
  const notify = opts.notify !== false
  if (notify) {
    const zh = (JOB_STATE_MAP as Record<string, { zh: string }>)[state]?.zh ?? state
    if (state === 'done') toast(`任务完成${reason === 'log_keyword' ? '，命中日志关键字' : ''}`, 'success')
    else if (state === 'aborted') toast('任务已中止', 'info')
    else toast(`任务结束：${zh}`, 'info')
  }
}

/** 轮询一次状态；返回当前 state。404 → 标记 job_gone 并停止。 */
async function pollOnce(jobId: string): Promise<void> {
  try {
    const st = await api.getStatus(jobId)
    if (jobState.job?.id !== jobId) return // 已切换到别的 job
    if (isTerminalState(st.state)) {
      handleTerminal(st.state, st.completion_reason || '', { notify: true })
    } else {
      setJobState(st.state)
      // 同步 createdAt，供恢复计时
      if (st.created_at) jobState.job.createdAtSec = st.created_at
    }
  } catch (e) {
    const status = (e as { status?: number }).status
    if (status === 404) {
      // 任务已不存在（listener 历史仅保留 20 条）——停止轮询，避免无限空转
      stopPolling()
      jobState.jobGone = true
      toast('任务已结束并被清理，停止状态轮询', 'info')
    }
    // 其他错误（网络抖动等）：保持轮询，等待恢复
  }
}

export function startPolling(jobId: string, intervalSec?: number): void {
  stopPolling()
  pollIntervalMs = (intervalSec && intervalSec > 0 ? intervalSec : DEFAULT_POLL_INTERVAL_SEC) * 1000
  pollTimer = setInterval(() => { void pollOnce(jobId) }, pollIntervalMs)
  void pollOnce(jobId) // 立即拉一次
}

/** 状态订阅（WS onTerminal 与轮询共用）。 */
function onLogTerminal(state: string, reason: string): void {
  if (isTerminalState(state)) {
    handleTerminal(state, reason, { notify: true })
  } else {
    setJobState(state, reason)
  }
}

/** 触发任务：单活互斥 → 立即进入 triggering → 成功跳状态页并启动轮询 + WS。 */
export async function triggerTask(taskId: string): Promise<boolean> {
  if (hasActiveJob.value) return false
  jobState.triggering = true
  jobState.triggeringTaskId = taskId
  try {
    const ack = await api.trigger(taskId)
    jobState.triggering = false
    jobState.triggeringTaskId = ''
    jobState.jobGone = false
    jobState.job = {
      id: ack.job_id,
      taskId: ack.task_id,
      displayName: ack.display_name || ack.task_id,
      groups: [],
      state: 'running',
      completionReason: '',
      createdAtSec: ack.created_at || Date.now() / 1000,
    }
    openLogStream(ack.job_id, onLogTerminal)
    startPolling(ack.job_id)
    return true
  } catch (e) {
    jobState.triggering = false
    jobState.triggeringTaskId = ''
    throw e
  }
}

/** 中止（running/completing 全程可打断为 aborted）。 */
export async function abortJob(): Promise<void> {
  if (!canAbort.value || !jobState.job) return
  await api.abort()
  // 后端 abort 返回后，状态以下一次轮询/WS 为准；这里乐观标记，避免 UI 抖动
  setJobState('aborted', 'aborted')
  stopPolling()
  closeLogStream()
  toast('任务已中止', 'info')
}

export interface StopResult { killed: string[] }

/** 强制清理（危险操作，调用方需先弹确认）。 */
export async function forceStop(): Promise<StopResult> {
  stopPolling()
  let killed: string[] = []
  try {
    const r = await api.stop()
    killed = r.killed ?? []
  } catch (e) {
    const status = (e as { status?: number }).status
    if (status !== 409) throw e
    // 409 = 无活动任务，视为清理成功（幂等）
  }
  jobState.job = null
  jobState.jobGone = false
  closeLogStream()
  return { killed }
}

/** 拉取历史列表。 */
export async function loadJobs(): Promise<JobRecord[]> {
  jobState.loadingJobs = true
  try {
    return await api.getJobs()
  } finally {
    jobState.loadingJobs = false
  }
}

/**
 * 页面刷新恢复：loadJobs 后若有 running/completing 记录 → 恢复轮询 + WS。
 * createdAt 秒 → 计时基准，刷新后不归零（照旧版 GUI resumeActiveJob）。
 */
export function resumeActiveJob(jobs: JobRecord[], pollIntervalSec?: number): boolean {
  const live = jobs.find(j => (j.state === 'running' || j.state === 'completing') && !j.finished_at)
  if (!live) return false
  if (jobState.job?.id === live.job_id && hasActiveJob.value) return true // 已在监视，不重复
  jobState.jobGone = false
  jobState.job = {
    id: live.job_id,
    taskId: live.task_id,
    displayName: live.display_name || live.task_id,
    groups: [],
    state: live.state as JobState,
    completionReason: '',
    createdAtSec: live.created_at || Date.now() / 1000,
  }
  openLogStream(live.job_id, onLogTerminal)
  startPolling(live.job_id, pollIntervalSec)
  return true
}

/** 清空活动 job（强制清理成功后的本地收尾，不清日志缓冲以便回看）。 */
export function clearJob(): void {
  stopPolling()
  jobState.job = null
  jobState.jobGone = false
  closeLogStream()
}

export function reasonZh(reason: string): string {
  return reason ? (COMPLETION_REASON_LABEL[reason] ?? reason) : ''
}

export function useJob() {
  return {
    jobState,
    currentState,
    stateMeta,
    isUnknownState,
    hasActiveJob,
    canAbort,
    triggerTask,
    abortJob,
    forceStop,
    loadJobs,
    resumeActiveJob,
    clearJob,
    startPolling,
    stopPolling,
    reasonZh,
  }
}
