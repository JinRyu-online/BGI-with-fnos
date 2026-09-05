/* ============================================================
 * 历史日志回看（详情页 /logs/:jobId 数据源）
 * - fetch(jobId, tail)：GET /api/logs/{job_id}?tail=N → 分类为 LogLine[]
 * - 状态 {loading, error, lines, keyword, visible}；keyword 过滤 250ms 防抖、
 *   大小写不敏感 includes；hitCount 角标；contentVersion 内容版本号（fetch/loadMore
 *   成功 +1、过滤不动）作 LogBody followKey——过滤切换不触发跟随
 * - "加载更多"：递增 tail 上限（1000 → 2000 → 3000 → 5000 封顶）重新拉取，
 *   后端钳制上限 5000（无"全部"模式，移动端渲染保护）
 * ============================================================ */
import { computed, reactive } from 'vue'
import { api } from './useApi'
import { classifyLog } from '../utils'
import type { LogLine } from './useLogStream'

/** tail 分页阶梯：首屏 ≤1000 行，逐级翻倍到后端上限 5000 */
const TAIL_STEPS = [1000, 2000, 3000, 5000]
const KEYWORD_DEBOUNCE_MS = 250

interface LogHistoryState {
  loading: boolean
  loadingMore: boolean
  error: string
  jobId: string
  /** 后端返回的全部行（未过滤，旧 → 新） */
  lines: LogLine[]
  /** 用户输入的过滤关键字（原始值，v-model） */
  keyword: string
  /** 防抖后实际生效的关键字 */
  activeKeyword: string
  /** 当前生效的 tail 上限（阶梯游标） */
  tailStep: number
  /** 是否已到最大 tail（隐藏"加载更多"） */
  atMaxTail: boolean
  /** 内容版本号：仅 fetch/loadMore 成功后 +1（过滤不 bump）——LogBody followKey 语义源 */
  contentVersion: number
}

export const logHistoryState = reactive<LogHistoryState>({
  loading: false,
  loadingMore: false,
  error: '',
  jobId: '',
  lines: [],
  keyword: '',
  activeKeyword: '',
  tailStep: 0,
  atMaxTail: false,
  contentVersion: 0,
})

let debounceTimer: ReturnType<typeof setTimeout> | null = null

/** 过滤后的可见行（大小写不敏感 includes；空关键字 = 全部） */
export const visibleLines = computed<LogLine[]>(() => {
  const kw = logHistoryState.activeKeyword.trim().toLowerCase()
  if (!kw) return logHistoryState.lines
  return logHistoryState.lines.filter(l => l.text.toLowerCase().includes(kw))
})

/** 命中数角标（有关键字时显示 命中/总数） */
export const hitCount = computed(() => ({
  hits: visibleLines.value.length,
  total: logHistoryState.lines.length,
  active: !!logHistoryState.activeKeyword.trim(),
}))

/** 复制用：过滤后可见行的纯文本。 */
export function visibleText(): string {
  return visibleLines.value.map(l => l.text).join('\n')
}

function toLogLines(raw: string[]): LogLine[] {
  return raw.map(t => ({ cls: classifyLog(t), text: t }))
}

async function fetchInner(jobId: string, tail: number): Promise<void> {
  const r = await api.getLogs(jobId, tail)
  logHistoryState.lines = toLogLines(r.lines)
  logHistoryState.jobId = jobId
  logHistoryState.tailStep = TAIL_STEPS.indexOf(tail) >= 0 ? TAIL_STEPS.indexOf(tail) : 0
  logHistoryState.atMaxTail = tail >= TAIL_STEPS[TAIL_STEPS.length - 1]
  logHistoryState.contentVersion++
}

/** 进入详情页拉取：骨架屏态 + 错误兜底（404 → 空态引导文案在页面层渲染）。 */
export async function fetchLogHistory(jobId: string): Promise<void> {
  logHistoryState.loading = true
  logHistoryState.error = ''
  logHistoryState.jobId = jobId
  logHistoryState.keyword = ''
  logHistoryState.activeKeyword = ''
  try {
    await fetchInner(jobId, TAIL_STEPS[0])
  } catch (e) {
    const status = (e as { status?: number }).status
    if (status === 404) {
      // 无录制：页面渲染空态引导（不当作错误横幅）
      logHistoryState.lines = []
    } else {
      logHistoryState.error = (e as Error).message || '加载日志失败'
    }
  } finally {
    logHistoryState.loading = false
  }
}

/** "加载更多"：递增 tail 阶梯重新拉取（覆盖 lines，分页语义 = 多带早期内容）。 */
export async function loadMore(): Promise<void> {
  if (logHistoryState.loadingMore || logHistoryState.atMaxTail) return
  logHistoryState.loadingMore = true
  try {
    const next = TAIL_STEPS[Math.min(logHistoryState.tailStep + 1, TAIL_STEPS.length - 1)]
    await fetchInner(logHistoryState.jobId, next)
  } catch (e) {
    logHistoryState.error = (e as Error).message || '加载更早日志失败'
  } finally {
    logHistoryState.loadingMore = false
  }
}

/** 关键字输入回调：250ms 防抖写入 activeKeyword。 */
export function onKeywordInput(value: string): void {
  logHistoryState.keyword = value
  if (debounceTimer) clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => {
    logHistoryState.activeKeyword = value
  }, KEYWORD_DEBOUNCE_MS)
}

/** 重置状态（离开页面时调用，防串页残留）。 */
export function resetLogHistory(): void {
  if (debounceTimer) { clearTimeout(debounceTimer); debounceTimer = null }
  logHistoryState.loading = false
  logHistoryState.loadingMore = false
  logHistoryState.error = ''
  logHistoryState.jobId = ''
  logHistoryState.lines = []
  logHistoryState.keyword = ''
  logHistoryState.activeKeyword = ''
  logHistoryState.tailStep = 0
  logHistoryState.atMaxTail = false
  logHistoryState.contentVersion = 0
}

export function useLogHistory() {
  return {
    logHistoryState,
    visibleLines,
    hitCount,
    fetchLogHistory,
    loadMore,
    onKeywordInput,
    resetLogHistory,
    visibleText,
  }
}
