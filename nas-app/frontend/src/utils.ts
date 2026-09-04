/* ============================================================
 * 通用工具函数（与原型一致）
 * ============================================================ */

/** 秒 → mm:ss（超过 1 小时分钟位继续累加，如 90:00）。 */
export function mmss(sec: number): string {
  const s = Math.max(0, Math.floor(sec))
  return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0')
}

/** Unix 秒 → "09-04 19:31"（历史记录用）。 */
export function fmtDT(unixSec: number | null | undefined): string {
  if (!unixSec) return '—'
  const d = new Date(unixSec * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** 当前时刻 HH:MM:SS（日志行前缀）。 */
export function nowHMS(): string {
  return new Date().toLocaleTimeString('zh-CN', { hour12: false })
}

/** 日志行分类：关键字黄 / 异常红 / 系统青（照 gui-design.md §2.2 与原型正则）。 */
export function classifyLog(t: string): 'kw' | 'err' | 'sys' | '' {
  if (/任务结束/.test(t)) return 'kw'
  if (/⚠|失败|异常|错误|error|闪退|fatal/i.test(t)) return 'err'
  if (/^\[系统\]/.test(t)) return 'sys'
  return ''
}

/** MAC 地址格式（WOL 校验）。 */
export const MAC_RE = /^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$/

/** WOL MAC 输入归一化：把小写/冒号统一为大写连字符展示。 */
export function normalizeMac(mac: string): string {
  return mac.toUpperCase()
}
