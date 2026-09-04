/* ============================================================
 * 确认弹窗（Promise 化，照原型 showConfirm）
 * 替代一切 confirm()；确认按钮文案随场景变化。
 * ============================================================ */
import { reactive } from 'vue'

export interface ConfirmOptions {
  title: string
  body: string
  okText?: string
}

interface ConfirmState extends ConfirmOptions {
  visible: boolean
  resolver: ((v: boolean) => void) | null
}

export const confirmState = reactive<ConfirmState>({
  visible: false,
  title: '',
  body: '',
  okText: '确认',
  resolver: null,
})

export function showConfirm(title: string, body: string, okText = '确认'): Promise<boolean> {
  // 上一个未决的确认按取消处理（防御式）
  if (confirmState.resolver) {
    confirmState.resolver(false)
  }
  confirmState.title = title
  confirmState.body = body
  confirmState.okText = okText
  confirmState.visible = true
  return new Promise<boolean>(resolve => { confirmState.resolver = resolve })
}

function settle(v: boolean) {
  confirmState.visible = false
  const r = confirmState.resolver
  confirmState.resolver = null
  r?.(v)
}

export function useConfirm() {
  return {
    confirmState,
    showConfirm,
    resolve: settle,
  }
}
