/* ============================================================
 * Toast 全局提示（照原型 §3：顶部滑入 → 2.2s 停留 → 淡出上移）
 * 替代一切 alert()。
 * ============================================================ */
import { reactive } from 'vue'

export type ToastType = 'success' | 'error' | 'info'

export interface ToastItem {
  id: number
  type: ToastType
  msg: string
  leaving: boolean
}

interface ToastState {
  items: ToastItem[]
}

/** 模块级 reactive 单例（轻量替代 Pinia store）。 */
export const toastState = reactive<ToastState>({ items: [] })

let seq = 0

export function toast(msg: string, type: ToastType = 'info'): void {
  const id = ++seq
  toastState.items.push({ id, type, msg, leaving: false })
  // 停留 2.2s → 淡出 0.3s → 移除
  setTimeout(() => {
    const it = toastState.items.find(t => t.id === id)
    if (!it) return
    it.leaving = true
    setTimeout(() => {
      const i = toastState.items.findIndex(t => t.id === id)
      if (i >= 0) toastState.items.splice(i, 1)
    }, 320)
  }, 2200)
}

export function useToast() {
  return { toast, toastState }
}
