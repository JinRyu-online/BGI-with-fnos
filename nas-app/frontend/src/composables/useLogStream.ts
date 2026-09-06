/* ============================================================
 * WS 日志流连接管理（同源代理 /api/ws/logs/{job_id}，严禁直连 Windows IP）
 * - 连接状态机：connecting / connected / error / closed
 * - 重连退避：1s → 2s → 5s 封顶；连续失败 ≥5 次切 error 并停止
 * - 行缓冲上限 400 行（超出移除最旧行）
 * - 消息协议：{ts,lines:[...]} 新行 / {state:{...}} 快照 / {last:true,state} 结束 / {error}
 * ============================================================ */
import { reactive } from 'vue'
import { classifyLog, nowHMS } from '../utils'

export type WsState = 'connecting' | 'connected' | 'error' | 'closed'

export interface LogLine {
  cls: 'kw' | 'err' | 'sys' | ''
  text: string
}

export const LOG_BUFFER_MAX = 400

/** 重连退避序列：1s → 2s → 5s（封顶）。 */
const BACKOFF_MS = [1000, 2000, 5000]
const MAX_FAILS_BEFORE_ERROR = 5

interface LogStreamState {
  jobId: string
  wsState: WsState
  lines: LogLine[]
  /** 逻辑总行数（角标用，不受 400 行裁剪影响；兼作 LogBody followKey） */
  totalLines: number
}

/** 模块级单例：页面切换不销毁日志面板状态（gui-design.md §3.3）。 */
export const logStreamState = reactive<LogStreamState>({
  jobId: '',
  wsState: 'closed',
  lines: [],
  totalLines: 0,
})

let ws: WebSocket | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let failCount = 0
let manualClose = false

function setWs(state: WsState) {
  logStreamState.wsState = state
}

export function logAppend(raw: string): void {
  const cls = classifyLog(raw)
  logStreamState.lines.push({ cls, text: raw })
  logStreamState.totalLines++
  if (logStreamState.lines.length > LOG_BUFFER_MAX) {
    logStreamState.lines.splice(0, logStreamState.lines.length - LOG_BUFFER_MAX)
  }
}

export function logAppendSys(text: string): void {
  logAppend(`[${nowHMS()}] ${text}`)
}

/** WS 文案映射（gui-design.md §2.3）。 */
export function wsStatusText(s: WsState): string {
  switch (s) {
    case 'connecting': return '连接中…'
    case 'connected': return '实时同步中'
    case 'error': return '连接错误 / 未配对'
    case 'closed': return '已结束'
  }
}

function clearReconnectTimer() {
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
}

function connect(jobId: string, onTerminal?: (state: string, reason: string) => void, onError?: (e: Event) => void) {
  if (!jobId) return
  setWs('connecting')
  // 必须走 NAS 同源代理（旧版直连 Windows IP 在 HTTPS 下会坏）
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const url = `${proto}://${window.location.host}/api/ws/logs/${encodeURIComponent(jobId)}`
  let sock: WebSocket
  try {
    sock = new WebSocket(url)
  } catch {
    setWs('error')
    scheduleReconnect(jobId, onTerminal, onError)
    return
  }
  ws = sock

  sock.onopen = () => {
    failCount = 0
    if (ws === sock) setWs('connected')
  }
  sock.onmessage = (ev) => {
    let msg: { ts?: number; lines?: string[]; state?: { state?: string; completion_reason?: string } | string; last?: boolean; error?: string }
    try { msg = JSON.parse(ev.data as string) } catch { return }
    if (msg.error) {
      logAppend(`[${nowHMS()}] ⚠ ${msg.error}`)
      setWs('error')
      // 服务端 accept 后发 error 再 close（job 不存在 / listener 不可达 / 未配对）：
      // 此时 onopen 已触发过、failCount 被清零，若继续重连会 1s 一次打爆代理。
      // 错误是确定性的（非网络抖动），停止重连，由用户刷新页面/重新触发恢复。
      manualClose = true
      clearReconnectTimer()
      return
    }
    if (Array.isArray(msg.lines)) msg.lines.forEach(logAppend)
    if (msg.state && typeof msg.state === 'object' && msg.state.state) {
      onTerminal?.(msg.state.state, msg.state.completion_reason || '')
    }
    if (msg.last) {
      setWs('closed')
      manualClose = true
      try { sock.close() } catch { /* ignore */ }
      clearReconnectTimer()
    }
  }
  sock.onerror = (ev) => {
    if (ws === sock) setWs('error')
    onError?.(ev)
  }
  sock.onclose = () => {
    if (ws !== sock) return
    ws = null
    if (manualClose) {
      manualClose = false
      setWs('closed')
      return
    }
    // 非正常断开 → 退避重连
    scheduleReconnect(jobId, onTerminal, onError)
  }
}

function scheduleReconnect(jobId: string, onTerminal?: (state: string, reason: string) => void, onError?: (e: Event) => void) {
  clearReconnectTimer()
  failCount++
  if (failCount >= MAX_FAILS_BEFORE_ERROR) {
    setWs('error')
    logAppendSys('[系统] 日志连接失败，请检查网络')
    return
  }
  const delay = BACKOFF_MS[Math.min(failCount - 1, BACKOFF_MS.length - 1)]
  setWs('connecting')
  logAppendSys(`[系统] 连接断开，${Math.round(delay / 1000)}s 后重试…`)
  reconnectTimer = setTimeout(() => {
    if (!manualClose && logStreamState.jobId === jobId) {
      connect(jobId, onTerminal, onError)
    }
  }, delay)
}

export function useLogStream() {
  /** 新 job 开始：重置缓冲并连接。 */
  function open(jobId: string, onTerminal?: (state: string, reason: string) => void, onError?: (e: Event) => void) {
    close()
    logStreamState.jobId = jobId
    logStreamState.lines = []
    logStreamState.totalLines = 0
    failCount = 0
    manualClose = false
    logAppendSys('[系统] 正在连接实时日志通道…')
    connect(jobId, onTerminal, onError)
  }

  /** 关闭连接（保留缓冲，供终态回看最后一屏日志）。 */
  function close() {
    clearReconnectTimer()
    manualClose = true
    if (ws) { try { ws.close() } catch { /* ignore */ } ws = null }
    setWs('closed')
  }

  /** 彻底重置（清空缓冲）。 */
  function reset() {
    close()
    logStreamState.jobId = ''
    logStreamState.lines = []
    logStreamState.totalLines = 0
  }

  return { logStreamState, open, close, reset }
}
