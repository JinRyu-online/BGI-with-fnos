<script setup lang="ts">
/**
 * 历史记录卡（照原型 §4.5）：7 态徽章 + 任务名 + 用时（等宽）；次行起止时间。
 * 进行中记录：结束显示 —、用时显示 进行中…；定时触发的记录带"定时"徽章。
 * 整卡可点 → 日志详情页 /logs/:jobId（右侧 › chevron，--text-3 弱化）。
 */
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import type { JobRecord } from '../types'
import { fmtDT, mmss } from '../utils'
import StateBadge from './StateBadge.vue'

const props = defineProps<{ record: JobRecord }>()

const router = useRouter()

const durText = computed(() => {
  const r = props.record
  if (r.state === 'running' || r.state === 'completing') return '进行中…'
  const sec = r.elapsed || (r.created_at && r.finished_at ? r.finished_at - r.created_at : 0)
  return sec ? mmss(sec) : '—'
})

function openDetail(): void {
  // history.state 只收可结构化克隆的原始值；vue-router 的 HistoryState 索引签名
  // 对 JobRecord 这种 interface 不兼容，先拍平成 string/number 原始字段
  const r = props.record
  const snapshot: Record<string, string | number | boolean | null> = {
    job_id: r.job_id,
    task_id: r.task_id,
    display_name: r.display_name,
    state: r.state,
    created_at: r.created_at,
    finished_at: r.finished_at,
    elapsed: r.elapsed ?? null,
    schedule_id: r.schedule_id ?? null,
  }
  void router.push({
    path: `/logs/${encodeURIComponent(r.job_id)}`,
    state: { record: snapshot },
  })
}
</script>

<template>
  <div class="card hist-card" role="button" @click="openDetail">
    <div class="hc-row">
      <StateBadge :state="record.state" />
      <span class="hc-name">{{ record.display_name || record.task_id }}</span>
      <span v-if="record.schedule_id" class="hc-sched">定时</span>
      <span class="hc-dur">{{ durText }}</span>
      <span class="hc-chevron">›</span>
    </div>
    <div class="hc-times">开始 {{ fmtDT(record.created_at) }} · 结束 {{ record.finished_at ? fmtDT(record.finished_at) : '—' }}</div>
  </div>
</template>

<style scoped>
.hist-card { cursor: pointer; }
.hist-card .hc-row { display: flex; align-items: center; gap: var(--space-2); }
.hc-name { flex: 1; min-width: 0; font-size: var(--font-md); font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hc-sched {
  flex-shrink: 0; font-size: var(--font-xs); font-weight: 600;
  padding: 2px 8px; border-radius: var(--radius-full);
  background: var(--brand-weak); color: var(--brand-strong);
  border: 1px solid rgba(165, 133, 74, .3);
}
.hc-dur { font-family: var(--font-mono); font-size: var(--font-sm); color: var(--text-2); flex-shrink: 0; }
.hc-times { font-size: var(--font-xs); color: var(--text-3); margin-top: 6px; }
.hc-chevron {
  flex-shrink: 0; margin-left: -2px;
  font-size: 20px; line-height: 1; color: var(--text-3);
}
</style>
