<script setup lang="ts">
/**
 * 历史页 /history（照原型 ③）：过滤 chip（全部/进行中/已完成/异常）+ 记录卡列表。
 * running/completing 的历史记录若存在则恢复监视（resumeActiveJob，轮询 + WS）。
 */
import { computed, onMounted, ref } from 'vue'
import HistoryCard from '../components/HistoryCard.vue'
import Skeleton from '../components/Skeleton.vue'
import GIcon from '../components/GIcon.vue'
import { api } from '../composables/useApi'
import { resumeActiveJob } from '../composables/useJob'
import { pollIntervalSec } from '../composables/useConfig'
import { HISTORY_FILTERS, isTerminalState, type HistoryFilterKey } from '../constants'
import type { JobRecord } from '../types'

const records = ref<JobRecord[]>([])
const loading = ref(true)
const refreshing = ref(false)
const error = ref('')
const filter = ref<HistoryFilterKey>('all')

const filtered = computed(() => {
  return records.value.filter(r => {
    if (filter.value === 'active') return r.state === 'running' || r.state === 'completing'
    if (filter.value === 'finished') return isTerminalState(r.state)
    if (filter.value === 'abnormal') return r.state !== 'running' && r.state !== 'completing' && r.state !== 'done'
    return true
  })
})

async function refresh(): Promise<void> {
  if (refreshing.value) return
  refreshing.value = true
  error.value = ''
  try {
    records.value = await api.getJobs()
    // 进行中的记录恢复监视（复用状态页轮询 + WS 逻辑）
    resumeActiveJob(records.value, pollIntervalSec.value)
  } catch (e) {
    error.value = `加载历史失败：${(e as Error).message}`
  } finally {
    refreshing.value = false
  }
}

onMounted(async () => {
  // 骨架屏至少 300ms（照原型节奏）
  const started = Date.now()
  try {
    records.value = await api.getJobs()
    resumeActiveJob(records.value, pollIntervalSec.value)
  } catch (e) {
    error.value = `加载历史失败：${(e as Error).message}`
  } finally {
    const remain = Math.max(0, 300 - (Date.now() - started))
    setTimeout(() => { loading.value = false }, remain)
  }
})
</script>

<template>
  <div>
    <template v-if="loading">
      <Skeleton height="40px" width="60%" />
      <Skeleton height="72px" />
      <Skeleton height="72px" />
      <Skeleton height="72px" />
    </template>
    <template v-else>
      <div class="chip-row">
        <button
          v-for="f in HISTORY_FILTERS"
          :key="f.key"
          class="chip"
          :class="{ active: filter === f.key }"
          @click="filter = f.key"
        >{{ f.label }}</button>
        <button class="chip chip-refresh" :disabled="refreshing" @click="refresh">
          <span v-if="refreshing" class="spinner spinner-dark"></span><template v-else><GIcon name="refresh" :size="13" /></template> 刷新
        </button>
      </div>

      <div v-if="error" class="card"><div class="empty-hint">{{ error }}</div></div>
      <div v-else-if="!filtered.length" class="card"><div class="empty-hint">暂无记录</div></div>
      <HistoryCard v-for="r in filtered" v-else :key="r.job_id" :record="r" />
    </template>
  </div>
</template>

<style scoped>
/* 筛选行：单行横滑（flex-wrap:nowrap + overflow-x:auto），刷新按钮 sticky 钉在可视区右缘。
   坑 A：overflow-x:auto 会使 overflow-y 计算为 auto，chip::after 外扩 4px 的触控热区
   会被裁剪甚至引发纵向滚动——用 padding:4px 0 + margin:-4px 0 补偿，
   热区恰好落进容器内边距（36 内容 + 8 padding = 44），不再越界。不加渐变遮罩。 */
.chip-row {
  display: flex; gap: var(--space-2);
  margin: -4px 0 var(--space-3);
  padding: 4px 0;
  flex-wrap: nowrap;
  overflow-x: auto;
  scrollbar-width: none; /* Firefox 隐藏滚动条 */
}
.chip-row::-webkit-scrollbar { display: none; } /* WebKit 隐藏滚动条 */
.chip {
  position: relative;
  min-height: 36px;
  padding: 8px 16px;
  border: 1px solid var(--border); border-radius: var(--radius-full);
  background: var(--surface); color: var(--text-2);
  font-size: var(--font-sm); font-weight: 500;
  /* chip 是原子块：不许内部断行、不许被压缩——iOS（CJK 文本任意字符可断）
     上"刷新"两字被折成两行的问题即源于此 */
  white-space: nowrap; flex-shrink: 0;
  transition: all .15s;
}
/* 扩大触控热区到 ≥44px（视觉 36px，命中区域外扩 4px） */
.chip::after { content: ''; position: absolute; inset: -4px; }
.chip.active { background: var(--brand-weak); border-color: var(--brand); color: var(--brand); font-weight: 600; }
.chip-refresh {
  display: inline-flex; align-items: center; gap: 4px; margin-left: auto;
  /* 横滑时钉在可视区右缘（-webkit-sticky 兼容老 iOS） */
  position: -webkit-sticky;
  position: sticky;
  right: 0;
}
.chip-refresh svg { flex-shrink: 0; }
.spinner-dark { border-color: rgba(100, 116, 139, .35); border-top-color: var(--text-2); }
</style>
