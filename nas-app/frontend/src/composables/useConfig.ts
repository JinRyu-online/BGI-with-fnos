/* ============================================================
 * 配置/配对状态（模块级 reactive 单例）
 * ============================================================ */
import { computed, reactive } from 'vue'
import { api } from './useApi'
import type { AppConfig, DeviceInfo } from '../types'

interface ConfigStore {
  config: AppConfig | null
  loading: boolean
  error: string
}

export const configState = reactive<ConfigStore>({
  config: null,
  loading: false,
  error: '',
})

export const paired = computed<boolean>(() => !!configState.config?.default_target)

export const pollIntervalSec = computed<number>(() => configState.config?.poll?.interval_sec || 10)

export async function loadConfig(): Promise<AppConfig | null> {
  configState.loading = true
  configState.error = ''
  try {
    configState.config = await api.getConfig()
    return configState.config
  } catch (e) {
    configState.error = (e as Error).message || '读取配置失败'
    return null
  } finally {
    configState.loading = false
  }
}

export function currentTarget(): DeviceInfo | null {
  const t = configState.config?.default_target
  return t ? { ip: t.ip, port: t.port, hostname: t.hostname } : null
}

export function useConfig() {
  return { configState, paired, pollIntervalSec, loadConfig, currentTarget }
}
