<script setup lang="ts">
/**
 * 历史任务日志详情页 /logs/:jobId（独立全屏路由，非弹层：内容长、刷新不丢、可返回）。
 * - 布局：in-flow height:100% flex 列（.page padding 保留含 tabbar 避让），页面不滚，
 *   日志滚动区是唯一滚动域（scrollChaining=contain 止链，无双滚动/双滚动条）
 * - 头部：返回 + 摘要卡（StateBadge + 任务名 + 定时徽章 + 起止时间/用时），天然固定
 * - 工具条：过滤输入（250ms 防抖、大小写不敏感）+ 复制（clipboard 优先，HTTP 内网
 *   iOS 走 execCommand 降级；toast 如实反映降级成败）+ 命中数角标（行内，显隐
 *   不改变纵向高度）
 * - 日志区：.log-section 定高链（flex:1 min-height:0 逐层）→ LogCard（统一外壳）+
 *   LogBody 共用组件；followKey = contentVersion（仅 fetch/loadMore 成功 bump，
 *   过滤不触发跟随）；showBadge=false；首屏 1000 行 + "加载更多"（视口保持的
 *   scrollHeight 差值补偿在 LogBody 内做）
 * - 空态（404 无录制）：引导配置 Windows 端 bettergi 日志路径；错误态/空态超长
 *   文案由页面 overflow-y:auto 兜底可滚
 */
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import StateBadge from '../components/StateBadge.vue'
import Skeleton from '../components/Skeleton.vue'
import GIcon from '../components/GIcon.vue'
import LogCard from '../components/LogCard.vue'
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
    <!-- 头部容器：返回行 + 摘要卡整体固定（页面不滚，无需 sticky） -->
    <div class="detail-header">
      <button class="back-btn" @click="goBack">
        <span class="back-chevron">‹</span> 返回
      </button>

      <!-- 摘要卡（浅色历史卡风格，随头部容器整体固定，自身不 sticky） -->
      <div class="card summary-card">
        <div class="sum-row">
          <StateBadge v-if="record" :state="record.state ?? ''" />
          <span class="sum-name">{{ record?.display_name || record?.task_id || jobId }}</span>
          <span v-if="record?.schedule_id" class="sum-sched">定时</span>
        </div>
        <div class="sum-times">
          开始 {{ fmtDT(record?.created_at) }} · 结束 {{ record?.finished_at ? fmtDT(record.finished_at) : '—' }} · 用时 {{ durText }}
        </div>
        <div class="sum-jobid">#{{ jobId }}</div>
      </div>
    </div>

    <!-- 骨架屏 -->
    <template v-if="logHistoryState.loading">
      <Skeleton height="44px" />
      <Skeleton height="240px" />
    </template>

    <template v-else>
      <!-- 加载错误 -->
      <div v-if="logHistoryState.error" class="card detail-static-card"><div class="empty-hint">{{ logHistoryState.error }}</div></div>

      <!-- 无录制空态：引导配置 bettergi 日志路径 -->
      <div v-else-if="noLog" class="card detail-static-card">
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
          <!-- hit-badge 放工具条行内：出现/消失不改变纵向布局高度
              （独立行会突变日志区 clientHeight，扰动 atBottom 判定/回底钮） -->
          <span v-if="hitCount.active" class="hit-badge">
            命中 {{ hitCount.hits }}/{{ hitCount.total }}
          </span>
        </div>
        <!-- 唯一滚动域容器：吃满页面剩余高度，LogCard→LogBody 定高链在其内闭合 -->
        <div class="log-section">
          <LogCard
            title="任务日志"
            :lines="visibleLines"
            :follow-key="logHistoryState.contentVersion"
            :show-badge="false"
            :on-load-more="logHistoryState.atMaxTail ? undefined : loadMore"
            :loading-more="logHistoryState.loadingMore"
            body-height="100%"
            scroll-chaining="contain"
            class="detail-log-card"
          >
            <template v-if="hitCount.active">
              <span class="head-hit">命中 {{ hitCount.hits }}/{{ hitCount.total }}</span>
            </template>
          </LogCard>
        </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
/* 页面根：in-flow height:100%（父级 .page 为 abspos 定高、padding 原样保留含
   tabbar/safe-area 避让）。flex 列 + 日志区吃满剩余高度 → 整页自身不产生滚动，
   overflow-y:auto 仅兜底错误态/空态超长文案（小屏 + 三段引导）可滚不被裁。 */
.detail-page {
  height: 100%;
  display: flex; flex-direction: column;
  overflow-y: auto; -webkit-overflow-scrolling: touch;
}

/* 头部容器（返回行 + 摘要卡）：页面不滚后无 sticky 语义，天然固定。
   水平负 margin 保留 —— 抵消 .page 左右内边距实现全宽贯通，否则内容从两侧缝穿出；
   上下负值去掉，容器底用 border 分层。背景必须不透明。 */
.detail-header {
  flex-shrink: 0;
  margin: 0 -4px var(--space-3);
  padding: 4px;
  background: var(--bg);
  border-bottom: 1px solid var(--border);
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

/* 摘要卡：随 .detail-header 整体固定，自身不 sticky（top:44px 估算值已废弃） */
.summary-card { margin: 0 4px var(--space-2); }
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

/* 工具条：过滤 + 复制 + 命中数角标（同行，显隐不改变纵向高度） */
.toolbar {
  flex-shrink: 0;
  display: flex; align-items: center; gap: var(--space-2);
  margin-bottom: var(--space-2);
}
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

/* 命中数角标（toolbar 行内）：不换行，与复制钮同排；过窄时收缩省略 */
.hit-badge {
  flex-shrink: 0; min-width: 0;
  font-family: var(--font-mono); font-size: var(--font-xs);
  color: var(--text-3);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}

/* 定高链：.log-section 吃满页面剩余高度 → LogCard（flex:1）→ LogBody（flex:1）
   → .log-scroll height:100% 内联。链路逐层 min-height:0 打破百分比高度对
   auto 父级失效的断点；页面唯一滚动域是 .log-scroll，页面本身不滚（无双滚动）。 */
.log-section {
  flex: 1; min-height: 0;
  display: flex; flex-direction: column;
}
/* LogCard 根：flex:1 吃满 .log-section；margin-bottom:0 覆盖组件默认 12px
   （定高链内边距会吃掉日志可视高度，底部间距由 .page padding 承担）。
   .detail-log-card 挂在 LogCard 根上，Vue 子根带父 scope id → 父作用域直接命中，
   无需 :deep。 */
.detail-log-card {
  flex: 1; min-height: 0;
  margin-bottom: 0;
}

/* 深色日志区改由 LogCard 统一外壳（log-frame 已删）；头部命中数角标 */
.head-hit {
  margin-left: auto;
  font-family: var(--font-mono);
  padding: 1px 8px; border-radius: var(--radius-full);
  background: rgba(255, 255, 255, .06); color: var(--log-head-text);
  flex-shrink: 0;
}

/* 骨架屏与错误/空态卡：非日志态无定高链，卡片保持自然高度、不参与 flex 挤压
   （内容超高时由 .detail-page overflow-y:auto 兜底滚动） */
.detail-static-card { flex-shrink: 0; }
.detail-page > .sk-block { flex-shrink: 0; }
</style>
