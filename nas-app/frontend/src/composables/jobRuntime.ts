/* ============================================================
 * App 级运行时引导（App.vue 挂载后执行一次）：
 * - 拉取 config（供设备 chip / 轮询间隔）
 * - loadJobs + resumeActiveJob：页面刷新后若有 running/completing 记录
 *   → 恢复轮询 + WS 日志（照旧版 GUI resumeActiveJob）
 * ============================================================ */
import { loadConfig, pollIntervalSec } from './useConfig'
import { loadJobs, resumeActiveJob } from './useJob'

let booted = false

export async function bootRuntime(): Promise<void> {
  if (booted) return
  booted = true
  await loadConfig()
  try {
    const jobs = await loadJobs()
    resumeActiveJob(jobs, pollIntervalSec.value)
  } catch {
    /* 历史加载失败不阻塞 UI（未配对等场景正常返回 []） */
  }
}
