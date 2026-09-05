/* ============================================================
 * Mock 后端：模仿 docs/原型/bgi-prototype.html 的模拟流转
 *   触发 → triggering → running（日志逐行流入）→ completing → done
 *   running/completing 可中止为 aborted
 * 仅供开发期脱离后端使用（?mock=1 或 VITE_USE_MOCK=true），不追求 tree-shake。
 * ============================================================ */
import type {
  AppConfig, BgiTask, TriggerAck, JobStatus, JobRecord,
  ScanProgress, DeviceInfo, DiscoverKeyAck, AbortAck, StopAck, WolAck,
  ScheduleItem, LogBundle,
} from '../types'

export class MockHttpError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

const sleep = (ms: number) => new Promise<void>(r => setTimeout(r, ms))

const MOCK_CONFIG: AppConfig = {
  default_target: { ip: '192.168.31.43', port: 8766, hostname: 'DESKTOP-GAMING' },
  api_key: 'mock-api-key-xxxxxxxxxxxx',
  scan: { subnet: null, listener_port: 8766, diag_ports: [8765, 8766] },
  poll: { interval_sec: 2 },
  target_mac: 'AA-BB-CC-DD-EE-FF',
}

const MOCK_TASKS: BgiTask[] = [
  { id: 't1', display_name: '日常一条龙', groups: ['日常一条龙', '每日委托', '领取奖励', '关闭游戏'], timeout_min: 90, after_done: 'sleep' },
  { id: 't2', display_name: '快速委托', groups: ['日常一条龙', '每日委托'], timeout_min: 30, after_done: 'none' },
  { id: 't3', display_name: '无尽采矿', groups: ['日常一条龙', '采矿'], timeout_min: 120, after_done: 'sleep' },
  { id: 't4', display_name: '尘歌壶助力', groups: ['尘歌壶', '好友助力'], timeout_min: 20, after_done: 'lock' },
]

const LOG_SCRIPT = [
  '[任务] 「{name}」开始执行',
  '[系统] 启动 BetterGI 主进程…',
  '[系统] 已聚焦原神窗口 (UnityWndClass)',
  '[调度] ▶ 步骤 1/4：领取每日奖励',
  '[调度] ✔ 派遣奖励领取完成',
  '[调度] ▶ 步骤 2/4：洞天宝钱收取',
  '[识别] ⚠ 图像识别异常，自动重试 1/3',
  '[调度] ✔ 洞天宝钱收取完成',
  '[调度] ▶ 步骤 3/4：每日委托-战斗',
  '[战斗] 队伍配置：雷神 / 班尼特 / 香菱 / 行秋',
  '[调度] ✔ 每日委托完成',
  '★ 任务结束',
  '[系统] 命中完成判定：日志关键字（log_keyword）',
  '[系统] 执行收尾动作：休眠',
  '[系统] BetterGI 进程已退出',
]

let paired = true
let job: {
  id: string
  taskId: string
  displayName: string
  groups: string[]
  state: string
  completionReason: string
  createdAt: number
  finishedAt: number | null
  aborted: boolean
} | null = null

let lineTimer: ReturnType<typeof setInterval> | null = null
let finishTimer: ReturnType<typeof setTimeout> | null = null
let lineIdx = 0

export interface MockLogMessage {
  ts: number
  lines?: string[]
  state?: JobStatus
  last?: boolean
  error?: string
}

type LogListener = (msg: MockLogMessage) => void
const logListeners = new Set<LogListener>()

function emitLog(msg: MockLogMessage) {
  for (const fn of logListeners) fn(msg)
}

function jobStatus(): JobStatus | null {
  if (!job) return null
  return {
    id: job.id,
    task_id: job.taskId,
    groups: job.groups,
    state: job.state,
    completion_reason: job.completionReason,
    created_at: job.createdAt,
    finished_at: job.finishedAt,
    elapsed: (job.finishedAt ?? Date.now() / 1000) - job.createdAt,
  }
}

function pushState() {
  const st = jobStatus()
  if (st) emitLog({ ts: Date.now(), state: st })
}

function clearTimers() {
  if (lineTimer) { clearInterval(lineTimer); lineTimer = null }
  if (finishTimer) { clearTimeout(finishTimer); finishTimer = null }
}

function finalize(state: string, reason: string) {
  if (!job) return
  clearTimers()
  job.state = state
  job.completionReason = reason
  job.finishedAt = Date.now() / 1000
  pushState()
  emitLog({ ts: Date.now(), lines: ['[系统] 连接关闭，本次运行结束'], last: true, state: jobStatus()! })
}

export function mockJobLogs(onMessage: LogListener, onEnd: (code: number) => void): () => void {
  const wrapped: LogListener = (msg) => {
    onMessage(msg)
    if (msg.last) {
      logListeners.delete(wrapped)
      endCbs.delete(wrapped)
      setTimeout(() => onEnd(1000), 100)
    }
  }
  const endCbs = new Set<LogListener>()
  logListeners.add(wrapped)
  return () => {
    logListeners.delete(wrapped)
    endCbs.delete(wrapped)
  }
}

export const mockApi = {
  async getConfig(): Promise<AppConfig> {
    await sleep(150)
    return { ...MOCK_CONFIG, default_target: paired ? { ...MOCK_CONFIG.default_target! } : null }
  },

  async getTasks(): Promise<BgiTask[]> {
    await sleep(200)
    if (!paired) return []
    return MOCK_TASKS.map(t => ({ ...t }))
  },

  async getBgiGroups(): Promise<{ groups: string[] }> {
    await sleep(120)
    // 模拟 Windows 端 User/ScriptGroup 目录枚举结果
    return { groups: ['日常一条龙', '每日委托', '领取奖励', '采矿', '尘歌壶', '好友助力', '关闭游戏'] }
  },

  async putTasks(tasks: BgiTask[]): Promise<BgiTask[]> {
    await sleep(200)
    MOCK_TASKS.splice(0, MOCK_TASKS.length, ...tasks.map(t => ({ ...t })))
    return MOCK_TASKS.map(t => ({ ...t }))
  },

  async trigger(taskId: string): Promise<TriggerAck> {
    if (job && !['done', 'abnormal_exit', 'timed_out', 'failed', 'aborted'].includes(job.state)) {
      throw new MockHttpError(409, '已有任务在运行')
    }
    if (!paired) throw new MockHttpError(404, '未配对设备')
    const task = MOCK_TASKS.find(t => t.id === taskId)
    if (!task) throw new MockHttpError(404, '任务不存在')
    // 模拟触发请求耗时（原型 1.5s）
    await sleep(1500)
    job = {
      id: `j-mock-${Math.random().toString(36).slice(2, 8)}`,
      taskId: task.id,
      displayName: task.display_name,
      groups: [...task.groups],
      state: 'running',
      completionReason: '',
      createdAt: Date.now() / 1000,
      finishedAt: null,
      aborted: false,
    }
    lineIdx = 0
    emitLog({ ts: Date.now(), state: jobStatus()! })
    // 日志逐行流入：每 800ms 一条（含“任务结束”黄色关键字行）
    lineTimer = setInterval(() => {
      if (!job) return
      if (lineIdx < LOG_SCRIPT.length) {
        const raw = LOG_SCRIPT[lineIdx++].replace('{name}', job.displayName)
        emitLog({ ts: Date.now(), lines: [raw] })
      }
    }, 800)
    // 10s 后进入 completing（反悔窗口），3s 后 done
    finishTimer = setTimeout(() => {
      if (!job) return
      job.state = 'completing'
      pushState()
      finishTimer = setTimeout(() => finalize('done', 'log_keyword'), 3000)
    }, 10000)
    return {
      job_id: job.id, task_id: job.taskId, display_name: job.displayName, created_at: job.createdAt,
    }
  },

  async getStatus(jobId: string): Promise<JobStatus> {
    await sleep(120)
    const st = jobStatus()
    if (!st || st.id !== jobId) throw new MockHttpError(404, 'job not found')
    return st
  },

  async getJobs(): Promise<JobRecord[]> {
    await sleep(150)
    if (!job) return []
    const st = jobStatus()!
    return [{
      job_id: st.id,
      task_id: st.task_id,
      display_name: job.displayName,
      state: st.state,
      created_at: st.created_at,
      finished_at: st.finished_at,
      elapsed: st.elapsed,
    }]
  },

  /** 历史日志回看 mock：返回假行（前端可离线开发详情页）。job 不存在 → 404。 */
  async getLogs(jobId: string, tail?: number): Promise<LogBundle> {
    await sleep(200)
    const cur = job
    if (!cur || cur.id !== jobId) throw new MockHttpError(404, '无日志记录')
    const n = Math.min(tail ?? 1000, 5000)
    const all = [
      ...LOG_SCRIPT.map(l => l.replace('{name}', cur.displayName)),
      '[调度] ✔ 全部调度组执行完毕',
    ]
    // tail 语义与后端一致：取末 n 行（mock 行数不足以测"加载更多"，循环填充到 1200 行）
    while (all.length < 1200) {
      all.push(`[调度] 补充行 ${all.length}：日常巡检正常，无异常事件`)
    }
    return { job_id: jobId, lines: all.slice(Math.max(0, all.length - n)) }
  },

  async abort(): Promise<AbortAck> {
    await sleep(150)
    if (!job || !['running', 'completing'].includes(job.state)) {
      throw new MockHttpError(409, '无活动任务')
    }
    job.aborted = true
    finalize('aborted', 'aborted')
    return { aborted: true }
  },

  async stop(): Promise<StopAck> {
    await sleep(200)
    const killed = job && !['done', 'abnormal_exit', 'timed_out', 'failed', 'aborted'].includes(job.state)
      ? [job.displayName] : []
    if (job) { job.aborted = true; finalize('aborted', 'aborted') }
    return { stopped: true, killed }
  },

  async wol(mac?: string): Promise<WolAck> {
    await sleep(300)
    const m = mac || MOCK_CONFIG.target_mac
    return { sent: true, mac: m }
  },

  async getSchedules(): Promise<ScheduleItem[]> {
    await sleep(120)
    return [
      { id: 's1', enabled: true, name: '挖矿一条龙', time: '12:00', weekdays: [0, 3],
        task_id: 't3', wake: true, wake_timeout_sec: 300, skip_if_busy: true,
        next_fire_at: Date.now() / 1000 + 86400, last_fired_at: Date.now() / 1000 - 86400 * 3,
        last_result: 'triggered', last_error: null },
      { id: 's2', enabled: false, name: '每日日常', time: '04:10', weekdays: [],
        task_id: 't1', wake: false, wake_timeout_sec: 300, skip_if_busy: true,
        next_fire_at: null, last_fired_at: null, last_result: null, last_error: null },
    ]
  },

  async putSchedules(): Promise<ScheduleItem[]> {
    await sleep(200)
    return this.getSchedules()
  },

  async runSchedule(id: string): Promise<{ dispatched: boolean; id: string }> {
    await sleep(150)
    return { dispatched: true, id }
  },

  async scheduleState(_id: string): Promise<{ last_fired_at: number | null; last_job_id: string | null; last_result: string; last_error: string | null }> {
    await sleep(80)
    return { last_fired_at: Date.now() / 1000 - 60, last_job_id: 'mock-job', last_result: 'triggered', last_error: null }
  },

  async unpair(): Promise<void> {
    await sleep(150)
    paired = false
  },

  async startScan(): Promise<{ started: boolean; reason?: string }> {
    await sleep(150)
    return { started: true }
  },

  async scanProgress(): Promise<ScanProgress> {
    await sleep(80)
    const subnets = [
      { subnet: '192.168.31.0/24', status: 'done', found: 1 },
      { subnet: '192.168.1.0/24', status: 'done', found: 0 },
      { subnet: '10.0.0.0/24', status: 'pending', found: 0 },
    ]
    return {
      active: false,
      stage: 'done',
      subnets,
      scanned: subnets.length,
      total: subnets.length,
      devices: paired ? [] : ([{ ip: '192.168.31.43', port: 8766, hostname: 'DESKTOP-GAMING', version: 'v1.2.0' }] as DeviceInfo[]),
      elapsed: 2.1,
      scanned_ips: 512,
      total_ips: 768,
      error: null,
    }
  },

  async discoverKey(ip: string, port: number): Promise<DiscoverKeyAck> {
    await sleep(300)
    if (!ip) throw new MockHttpError(400, 'missing ip')
    return { api_key: 'mock-api-key-xxxxxxxxxxxx', hostname: `MOCK-${port}` }
  },

  async pair(): Promise<void> {
    await sleep(300)
    paired = true
  },
}
