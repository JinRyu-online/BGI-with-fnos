/* ============================================================
 * API 数据类型（与后端契约对齐：nas-app/app/docker/app/main.py + Wave 2 冻结契约）
 * ============================================================ */
import type { JobState } from './constants'

export interface TargetInfo {
  ip: string
  port: number
  hostname: string
}

export interface AppConfig {
  default_target: TargetInfo | null
  api_key: string
  scan: { subnet: string | null; listener_port: number; diag_ports: number[] }
  poll: { interval_sec: number }
  target_mac: string
  schedules?: ScheduleConfig[]
}

/** 定时任务（config.schedules 元素；schedules_state.json 运行元数据见 SchedulesItem） */
export interface ScheduleConfig {
  id: string
  enabled: boolean
  name: string
  /** "HH:MM" 本地时间（容器 TZ=Asia/Shanghai） */
  time: string
  /** ISO 0=周一；空数组=每天 */
  weekdays: number[]
  task_id: string
  /** false=PC 常开，不 WOL 直接触发 */
  wake: boolean
  wake_timeout_sec: number
  skip_if_busy: boolean
}

/** GET /api/schedules 返回项 = ScheduleConfig + 运行元数据 */
export interface ScheduleItem extends ScheduleConfig {
  /** 下次触发时刻（Unix 秒，后端算好；null=配置非法） */
  next_fire_at: number | null
  last_fired_at: number | null
  last_result: string | null
  last_error: string | null
}

export interface ScanAck {
  started: boolean
  reason?: string
  port?: number
  subnet?: string
}

export interface SubnetProgress {
  subnet: string
  status: 'pending' | 'scanning' | 'scanning…' | 'done' | 'skipped' | 'skipping' | string
  found: number
}

export interface DeviceInfo {
  ip: string
  port: number
  hostname: string
  version?: string
}

export interface ScanProgress {
  active: boolean
  stage: 'idle' | 'preparing' | 'scanning' | 'done' | 'error' | string
  subnets: SubnetProgress[]
  scanned: number
  total: number
  devices: DeviceInfo[]
  elapsed: number
  scanned_ips: number
  total_ips: number
  error: string | null
}

export interface BgiTask {
  id: string
  display_name: string
  groups: string[]
  timeout_min: number
  after_done: string
}

/** 任务编辑合法的收尾动作（与 Windows 端 VALID_AFTER_DONE 对齐）。 */
export const TASK_AFTER_DONE_OPTIONS = ['sleep', 'shutdown', 'lock', 'none'] as const

export interface TriggerAck {
  job_id: string
  task_id: string
  display_name: string
  created_at: number
}

export interface JobStatus {
  id: string
  task_id: string
  groups: string[]
  state: JobState | string
  completion_reason: string
  created_at: number
  finished_at: number | null
  elapsed: number
}

export interface JobRecord {
  job_id: string
  task_id: string
  display_name: string
  state: JobState | string
  created_at: number
  finished_at: number | null
  /** NAS 端 history 不写该字段（仅 JobStatus 有），展示用时由 finished_at-created_at 推算 */
  elapsed?: number
  /** 定时任务溯源：由定时调度触发时写入（reconcile 落终态时保留） */
  schedule_id?: string
}

export interface AbortAck { aborted: boolean }
export interface StopAck { stopped: boolean; killed: string[] }
export interface WolAck { sent: boolean; mac: string; saved: boolean }
/** mac 为后端规范值（AA-BB-CC-DD-EE-FF）；saved=true 表示本次已持久化到 target_mac */
export interface DiscoverKeyAck { api_key: string; hostname: string }

/** GET /api/logs/{job_id} 响应：历史任务日志（strings，前端 classifyLog 分类） */
export interface LogBundle {
  job_id: string
  lines: string[]
}
