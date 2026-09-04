<script setup lang="ts">
/**
 * 历史记录卡（照原型 §4.5）：7 态徽章 + 任务名 + 用时（等宽）；次行起止时间。
 * 进行中记录：结束显示 —、用时显示 进行中…
 */
import { computed } from 'vue'
import type { JobRecord } from '../types'
import { fmtDT, mmss } from '../utils'
import StateBadge from './StateBadge.vue'

const props = defineProps<{ record: JobRecord }>()

const durText = computed(() => {
  const r = props.record
  if (r.state === 'running' || r.state === 'completing') return '进行中…'
  const sec = r.elapsed || (r.created_at && r.finished_at ? r.finished_at - r.created_at : 0)
  return sec ? mmss(sec) : '—'
})
</script>

<template>
  <div class="card hist-card">
    <div class="hc-row">
      <StateBadge :state="record.state" />
      <span class="hc-name">{{ record.display_name || record.task_id }}</span>
      <span class="hc-dur">{{ durText }}</span>
    </div>
    <div class="hc-times">开始 {{ fmtDT(record.created_at) }} · 结束 {{ record.finished_at ? fmtDT(record.finished_at) : '—' }}</div>
  </div>
</template>

<style scoped>
.hist-card .hc-row { display: flex; align-items: center; gap: var(--space-2); }
.hc-name { flex: 1; min-width: 0; font-size: var(--font-md); font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hc-dur { font-family: var(--font-mono); font-size: var(--font-sm); color: var(--text-2); flex-shrink: 0; }
.hc-times { font-size: var(--font-xs); color: var(--text-3); margin-top: 6px; }
</style>
