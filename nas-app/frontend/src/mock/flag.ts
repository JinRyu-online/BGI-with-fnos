/* ============================================================
 * Mock 模式开关
 * 开启方式（优先级从高到低）：
 *   1. URL 参数 ?mock=1（开发期临时切换，无需改 .env）
 *   2. .env 中 VITE_USE_MOCK === 'true'
 * 关闭方式：?mock=0 可强制覆盖关闭。
 * ============================================================ */
const envFlag = import.meta.env.VITE_USE_MOCK === 'true'

export function resolveMockEnabled(): boolean {
  try {
    const q = new URLSearchParams(window.location.search).get('mock')
    if (q === '1' || q === 'true') return true
    if (q === '0' || q === 'false') return false
  } catch {
    /* SSR/非浏览器环境忽略 */
  }
  return envFlag
}

export const mockEnabled = resolveMockEnabled()
