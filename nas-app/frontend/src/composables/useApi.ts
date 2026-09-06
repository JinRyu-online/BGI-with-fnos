/* ============================================================
 * fetch 封装（照旧版 GUI api() 的错误语义）
 * - JSON 请求/响应；!ok 时抛 Error(detail || `HTTP n`)，错误对象带 status。
 * - 409 = 忙/冲突，404 = 未配对或不存在（调用方按语义处理）。
 * - mock 模式下整体替换为 src/mock/server.ts。
 * ============================================================ */
import { mockEnabled } from '../mock/flag'
import { MockHttpError, mockApi } from '../mock/server'
import type {
  AppConfig, BgiTask, TriggerAck, JobStatus, JobRecord,
  ScanAck, ScanProgress, DeviceInfo, DiscoverKeyAck,
  AbortAck, StopAck, WolAck, ScheduleItem, LogBundle,
} from '../types'

export interface ApiError extends Error {
  status?: number
}

export class HttpError extends Error implements ApiError {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const opt: RequestInit = { method, headers: {} }
  if (body !== undefined) {
    opt.headers = { 'Content-Type': 'application/json' }
    opt.body = JSON.stringify(body)
  }
  const r = await fetch(path, opt)
  const data = await r.json().catch(() => ({}))
  if (!r.ok) {
    const detail = (data as { detail?: string }).detail || `HTTP ${r.status}`
    throw new HttpError(r.status, detail)
  }
  return data as T
}

export const api = {
  getConfig(): Promise<AppConfig> {
    if (mockEnabled) return mockApi.getConfig()
    return request<AppConfig>('GET', '/api/config')
  },

  getTasks(): Promise<BgiTask[]> {
    if (mockEnabled) return mockApi.getTasks()
    return request<BgiTask[]>('GET', '/api/tasks')
  },

  getBgiGroups(): Promise<{ groups: string[] }> {
    if (mockEnabled) return mockApi.getBgiGroups()
    return request<{ groups: string[] }>('GET', '/api/bgi-groups')
  },

  putTasks(tasks: BgiTask[]): Promise<BgiTask[]> {
    if (mockEnabled) return mockApi.putTasks(tasks)
    return request<BgiTask[]>('PUT', '/api/tasks', { tasks })
  },

  startScan(subnet?: string): Promise<ScanAck> {
    if (mockEnabled) return mockApi.startScan()
    return request<ScanAck>('POST', '/api/scan', subnet ? { subnet } : {})
  },

  scanProgress(): Promise<ScanProgress> {
    if (mockEnabled) return mockApi.scanProgress()
    return request<ScanProgress>('GET', '/api/scan-progress')
  },

  discoverKey(ip: string, port: number): Promise<DiscoverKeyAck> {
    if (mockEnabled) return mockApi.discoverKey(ip, port)
    return request<DiscoverKeyAck>('GET', `/api/discover-key?ip=${encodeURIComponent(ip)}&port=${port}`)
  },

  pair(body: { ip: string; port: number; hostname: string; api_key: string }): Promise<void> {
    if (mockEnabled) return mockApi.pair()
    return request<void>('POST', '/api/pair', body)
  },

  unpair(): Promise<void> {
    if (mockEnabled) return mockApi.unpair()
    return request<void>('POST', '/api/unpair')
  },

  trigger(taskId: string): Promise<TriggerAck> {
    if (mockEnabled) return mockApi.trigger(taskId)
    return request<TriggerAck>('POST', '/api/trigger', { task_id: taskId })
  },

  getStatus(jobId: string): Promise<JobStatus> {
    if (mockEnabled) return mockApi.getStatus(jobId)
    return request<JobStatus>('GET', `/api/status?job_id=${encodeURIComponent(jobId)}`)
  },

  getJobs(): Promise<JobRecord[]> {
    if (mockEnabled) return mockApi.getJobs()
    return request<JobRecord[]>('GET', '/api/jobs')
  },

  /** 历史任务日志回看：tail 默认 1000、后端钳制上限 5000；404 = 无录制 */
  getLogs(jobId: string, tail?: number): Promise<LogBundle> {
    if (mockEnabled) return mockApi.getLogs(jobId, tail)
    const q = tail ? `?tail=${tail}` : ''
    return request<LogBundle>('GET', `/api/logs/${encodeURIComponent(jobId)}${q}`)
  },

  abort(): Promise<AbortAck> {
    if (mockEnabled) return mockApi.abort()
    return request<AbortAck>('POST', '/api/abort')
  },

  stop(): Promise<StopAck> {
    if (mockEnabled) return mockApi.stop()
    return request<StopAck>('POST', '/api/stop')
  },

  wol(mac?: string): Promise<WolAck> {
    if (mockEnabled) return mockApi.wol(mac)
    return request<WolAck>('POST', '/api/wol', mac ? { mac } : {})
  },

  getSchedules(): Promise<ScheduleItem[]> {
    if (mockEnabled) return mockApi.getSchedules()
    return request<ScheduleItem[]>('GET', '/api/schedules')
  },

  putSchedules(schedules: ScheduleItem[]): Promise<ScheduleItem[]> {
    if (mockEnabled) return mockApi.putSchedules()
    // 回传时剥掉运行元数据（后端只收 config.schedules 结构）
    const slim = schedules.map(({ next_fire_at, last_fired_at, last_result, last_error, ...rest }) => rest)
    return request<ScheduleItem[]>('PUT', '/api/schedules', { schedules: slim })
  },

  runSchedule(id: string): Promise<{ dispatched: boolean; id: string }> {
    if (mockEnabled) return mockApi.runSchedule(id)
    return request<{ dispatched: boolean; id: string }>('POST', `/api/schedules/${encodeURIComponent(id)}/run`)
  },

  scheduleState(id: string): Promise<{ last_fired_at: number | null; last_job_id: string | null; last_result: string; last_error: string | null }> {
    if (mockEnabled) return mockApi.scheduleState(id)
    return request<{ last_fired_at: number | null; last_job_id: string | null; last_result: string; last_error: string | null }>(
      'GET', `/api/schedules/${encodeURIComponent(id)}/state`)
  },
}

/** 触发设备的 mock 版本（设置页用，绕开 mock server 的 jobs 状态）。 */
export const mockDeviceHelper = { MockHttpError }
export type { DeviceInfo }
