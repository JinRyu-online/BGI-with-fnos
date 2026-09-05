<script setup lang="ts">
/**
 * 历史任务日志详情页 /logs/:jobId（独立全屏路由，非弹层：内容长、刷新不丢、可返回）。
 * - sticky 摘要卡：StateBadge + 任务名 + 定时徽章 + 起止时间/用时
 * - 工具条：过滤输入（250ms 防抖、大小写不敏感）+ 复制（clipboard 优先，HTTP 内网
 *   iOS 走 execCommand 降级；toast 如实反映降级成败）+ 命中数角标
 * - 日志区：LogBody 共用组件；无自动滚动（静态数据）；首屏 1000 行 + "加载更多"
 * - 空态（404 无录制）：引导配置 Windows 端 bettergi 日志路径
 */
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import StateBadge from '../components/StateBadge.vue'
import Skeleton from '../components/Skeleton.vue'
import GIcon from '../components/GIcon.vue'
import LogBody from '../components/LogBody.vue'
import {
  fetchLogHistory, loadMore, onKeywordInput, resetLogHistory,
  visibleLines, hitCount, visibleText, logHistoryState,
} from '../composables/useLogHistory'
import { toast } from '../composables/useToast'
import { fmtDT, mmss } from '../utils'
import type { JobRecord } from '../types'

const route = useRoute()
const router = useRouter()

/** 历史记录快照（router state 传入摘要，HistoryCard 已拍平为原始值；
 * 刷新后 history.state 保留 → 依然可读；直接深链进入则为 null） */
const record = (history.state?.record ?? null) as Partial<JobRecord> | null

const jobId = computed(() => String(route.params.jobId || ''))

const durText = computed(() => {
  const r = record
  if (!r) return '—'
  if (r.state === 'running' || r.state === 'completing') return '进行中…'
  const sec = r.elapsed || (r.created_at && r.finished_at ? r.finished_at - r.created_at : 0)
  return sec ? mmss(sec) : '—'
})

/** 无录制空态：404（未配 log_path / 旧任务 / 录制失败） */
const noLog = computed(() =>
  !logHistoryState.loading && !logHistoryState.error && logHistoryState.lines.length === 0,
)

async function copyVisible(): Promise<void> {
  const text = visibleText()
  if (!text) {
    toast('没有可复制的日志', 'info')
    return
  }
  // navigator.clipboard 仅在 secure context 存在；本项目 NAS 是 HTTP 内网，iOS 必走降级
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text)
      toast(`已复制 ${hitCount.value.hits} 行`, 'success')
      return
    } catch { /* 落降级 */ }
  }
  // 降级：execCommand + 临时 textarea（必须 user gesture 内同步执行）
  const ta = document.createElement('textarea')
  ta.value = text
  ta.style.position = 'fixed'
  ta.style.opacity = '0'
  ta.style.left = '-9999px'
  document.body.appendChild(ta)
  ta.focus()
  ta.select()
  let ok = false
  try { ok = document.execCommand('copy') } catch { ok = false }
  document.body.removeChild(ta)
  if (ok) toast(`已复制 ${hitCount.value.hits} 行`, 'success')
  else toast('复制失败', 'error')
}

function goBack(): void {
  // router.back 失败（无历史，深链直达）→ fallback /spa/history
  if (window.history.length > 1) router.back()
  else router.replace('/history')
}

onMounted(() => {
  if (jobId.value) void fetchLogHistory(jobId.value)
})
onBeforeUnmount(() => {
  resetLogHistory()
})
</script>

<template>
  <div class="detail-page">
    <div class="detail-top">
      <button class="back-btn" @click="goBack">
        <span class="back-chevron">‹</span> 返回
      </button>
    </div>

    <!-- sticky 摘要卡 -->
    <div class="card summary-card">
      <div class="sum-row">
        <StateBadge v-if="record" :state="record.state ?? ''" />
        <span class="sum-name">{{ record?.display_name || record?.task_id || jobId }}</span>
        <span v-if="record?.schedule_id" class="sum-sched">定时</span>
      </div>
      <div class="sum-times">
        开始 {{ fmtDT(record?.created_at) }} · 结束 {{ record?.finished_at ? fmtDT(record.finished_at) : '—' }} · 用时 {{ durText }}
      </div>
      <div class="sum-jobid">#{{ jobId }}</div>    </div>

    <!-- 骨架屏 -->
    <template v-if="logHistoryState.loading">
      <Skeleton height="44px" />
      <Skeleton height="240px" />
    </template>

    <template v-else>
      <!-- 加载错误 -->
      <div v-if="logHistoryState.error" class="card"><div class="empty-hint">{{ logHistoryState.error }}</div></div>

      <!-- 无录制空态：引导配置 bettergi 日志路径 -->
      <div v-else-if="noLog" class="card">
        <div class="empty-hint">
          <p class="nl-title">暂无日志记录</p>
          <p class="nl-sub">该任务没有在 NAS 上留下日志（可能录制未启用或录制失败）。</p>
          <p class="nl-sub">Windows 端需配置 BetterGI 日志路径（config.toml 的 log_path / bettergi.dir）后重新触发任务，才有日志记录。</p>
        </div>
      </div>

      <!-- 日志区 -->
      <template v-else>
        <div class="toolbar">
          <div class="filter-wrap">
            <GIcon name="search" :size="13" />
            <input
              class="filter-input"
              type="text"
              :value="logHistoryState.keyword"
              placeholder="过滤日志…"
              @input="onKeywordInput(($event.target as HTMLInputElement).value)"
            />
            <button
              v-if="logHistoryState.keyword"
              class="filter-clear"
              @click="onKeywordInput('')"
            >×</button>
          </div>
          <button class="copy-btn" @click="copyVisible">
            <GIcon name="check" :size="13" /> 复制
          </button>
        </div>
        <div v-if="hitCount.active" class="hit-badge">
          命中 {{ hitCount.hits }} / {{ hitCount.total }} 行
        </div>
        <div class="log-frame">
          <LogBody
            :lines="visibleLines"
            :height="'60vh'"
            :on-load-more="logHistoryState.atMaxTail ? undefined : loadMore"
            :loading-more="logHistoryState.loadingMore"
          />
        </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
.detail-page { display: flex; flex-direction: column; }
/* 顶部返回条：sticky 在页面滚动容器顶部（.page 是滚动宿主） */
.detail-top {
  position: sticky; top: 0; z-index: 5;
  margin: -4px -4px var(--space-3);
  padding: 4px;
}
.back-btn {
  position: relative;
  display: inline-flex; align-items: center; gap: 2px;
  min-height: 40px;
  border: none; background: none;
  font-size: var(--font-md); font-weight: 600; color: var(--text-1);
  padding: 6px 10px 6px 4px;
}
.back-btn::after { content: ''; position: absolute; inset: -2px -6px; }
.back-chevron { font-size: 26px; line-height: 1; margin-top: -3px; }

/* 摘要卡（浅色历史卡风格 + sticky） */
.summary-card {
  position: sticky; top: 44px; z-index: 4;
}
.sum-row { display: flex; align-items: center; gap: var(--space-2); }
.sum-name { flex: 1; min-width: 0; font-size: var(--font-md); font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sum-sched {
  flex-shrink: 0; font-size: var(--font-xs); font-weight: 600;
  padding: 2px 8px; border-radius: var(--radius-full);
  background: var(--brand-weak); color: var(--brand-strong);
  border: 1px solid rgba(165, 133, 74, .3);
}
.sum-times { font-size: var(--font-xs); color: var(--text-3); margin-top: 6px; }
.sum-jobid { font-family: var(--font-mono); font-size: var(--font-xs); color: var(--text-3); margin-top: 2px; }

/* 工具条：过滤 + 复制 */
.toolbar { display: flex; gap: var(--space-2); margin-bottom: var(--space-2); }
.filter-wrap {
  flex: 1; min-width: 0;
  display: flex; align-items: center; gap: 6px;
  border: 1px solid var(--border); border-radius: var(--radius-md);
  background: var(--surface);
  padding: 0 10px;
  color: var(--text-3);
}
.filter-input {
  flex: 1; min-width: 0; min-height: 40px;
  border: none; outline: none; background: none;
  font-size: 16px; /* iOS 聚焦防缩放 */
  color: var(--text-1);
}
.filter-clear {
  position: relative;
  border: none; background: none;
  font-size: 18px; color: var(--text-3);
  padding: 4px 8px; min-height: 0;
}
.filter-clear::after { content: ''; position: absolute; inset: -4px; }
.copy-btn {
  position: relative;
  display: inline-flex; align-items: center; gap: 4px;
  min-height: 42px;
  border: 1px solid var(--border); border-radius: var(--radius-md);
  background: var(--surface); color: var(--text-2);
  font-size: var(--font-sm); font-weight: 600;
  padding: 0 14px;
  flex-shrink: 0;
}
.copy-btn::after { content: ''; position: absolute; inset: -4px; }

/* 命中数角标 */
.hit-badge {
  font-family: var(--font-mono); font-size: var(--font-xs);
  color: var(--text-3);
  margin: 0 2px var(--space-2);
}

/* 深色日志框：LogBody 套一层圆角边框 */
.log-frame {
  background: var(--log-bg);
  border: 1px solid var(--log-border);
  border-radius: var(--radius-md);
  overflow: hidden;
}
</style>
