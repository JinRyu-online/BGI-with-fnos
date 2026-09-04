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
}

export interface AbortAck { aborted: boolean }
export interface StopAck { stopped: boolean; killed: string[] }
export interface WolAck { sent: boolean; mac: string }
export interface DiscoverKeyAck { api_key: string; hostname: string }
