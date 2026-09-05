/* ============================================================
 * BetterGI Trigger · 状态映射单一来源（照 docs/gui-design.md §2.1）
 * 组件内禁止散落 if/else 状态映射；一切状态文案/配色/动画取自此文件。
 * ============================================================ */

/** 后端 7 态 + 前端占位态（idle / triggering）。 */
export type JobState =
  | 'idle'            // 前端占位：无任务（不来自后端）
  | 'triggering'      // 前端瞬时态：触发请求进行中（不来自后端）
  | 'running'
  | 'completing'
  | 'done'
  | 'abnormal_exit'
  | 'timed_out'
  | 'failed'
  | 'aborted'

export interface JobStateMeta {
  /** 中文文案 */
  zh: string
  /** StatusDot class（.dot 前缀的 s-* class） */
  dotClass: string
  /** 徽章 class（.badge 前缀的 b-* class） */
  badgeClass: string
  /** 圆点是否呼吸动画（仅 running/completing） */
  breathe: boolean
  /** 是否终态 */
  terminal: boolean
}

/**
 * 7 个后端态 + idle/triggering 前端态全覆盖。
 * 对齐义务：后端枚举见 windows-listener/bgi_trigger/core/state.py 的 JobState（7 态）。
 * 后端如新增/更名状态，必须同步回写本表（后端对账会写 unknown，见 JOB_STATE_META 兜底）。
 */
export const JOB_STATE_MAP: Record<JobState, JobStateMeta> = {
  idle:          { zh: '空闲',         dotClass: 's-idle',       badgeClass: 'b-idle',           breathe: false, terminal: false },
  triggering:    { zh: '触发中…',      dotClass: 's-idle',       badgeClass: 'b-idle',           breathe: false, terminal: false },
  running:       { zh: '运行中',       dotClass: 's-running',    badgeClass: 'b-running',        breathe: true,  terminal: false },
  completing:    { zh: '收尾中',       dotClass: 's-completing', badgeClass: 'b-completing',     breathe: true,  terminal: false },
  done:          { zh: '已完成',       dotClass: 's-done',       badgeClass: 'b-done',           breathe: false, terminal: true  },
  abnormal_exit: { zh: '游戏异常退出', dotClass: 's-abnormal',   badgeClass: 'b-abnormal_exit',  breathe: false, terminal: true  },
  timed_out:     { zh: '超时',         dotClass: 's-timed-out',  badgeClass: 'b-timed_out',      breathe: false, terminal: true  },
  failed:        { zh: '失败',         dotClass: 's-failed',     badgeClass: 'b-failed',         breathe: false, terminal: true  },
  aborted:       { zh: '已中止',       dotClass: 's-aborted',    badgeClass: 'b-aborted',        breathe: false, terminal: true  },
}

/**
 * 终态集合。
 * ⚠ 对齐义务：与后端 TERMINAL_STATES 逐字一致
 * （windows-listener/bgi_trigger/core/state.py 的 _TERMINAL：done/abnormal_exit/timed_out/failed/aborted）。
 * 后端如调整终态集合，必须同步修改此处。
 */
export const TERMINAL_STATES: readonly string[] = ['done', 'abnormal_exit', 'timed_out', 'failed', 'aborted']

/** 是否终态（对未知字符串返回 false → 视为可继续轮询，由 unknown 兜底展示）。 */
export function isTerminalState(state: string | null | undefined): boolean {
  return TERMINAL_STATES.includes(state ?? '')
}

/** 收尾动作 → 文案（图标用 GIcon name，见 components/GIcon.vue）。 */
export const AFTER_DONE_LABEL: Record<string, string> = {
  sleep: '休眠',
  shutdown: '关机',
  lock: '锁屏',
  none: '不操作',
}

/** 收尾动作 → GIcon 图标名。 */
export const AFTER_DONE_ICON: Record<string, string> = {
  sleep: 'moon',
  shutdown: 'power',
  lock: 'lock',
  none: 'pause',
}

/** completion_reason → 中文（照 gui-design/旧版 GUI 语义）。 */
export const COMPLETION_REASON_LABEL: Record<string, string> = {
  game_exited: '游戏退出',
  log_keyword: '日志关键字命中',
  timeout: '超时',
  aborted: '用户中止',
  error: '执行错误',
}

/** 历史过滤 chip → 命中谓词。 */
export const HISTORY_FILTERS = [
  { key: 'all', label: '全部' },
  { key: 'active', label: '进行中' },
  { key: 'finished', label: '已完成' },
  { key: 'abnormal', label: '异常' },
] as const
export type HistoryFilterKey = (typeof HISTORY_FILTERS)[number]['key']
