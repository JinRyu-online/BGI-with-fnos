<script setup lang="ts">
/**
 * 任务卡（照原型 §4.3）：显示名 + 触发按钮；分组链 pill；⏱/收尾属性 pill。
 */
import type { BgiTask } from '../types'
import { AFTER_DONE_LABEL } from '../constants'
import TriggerButton from './TriggerButton.vue'

defineProps<{ task: BgiTask }>()
</script>

<template>
  <div class="card task-card">
    <div class="tc-head">
      <div class="tc-name">{{ task.display_name }}</div>
      <TriggerButton :task-id="task.id" />
    </div>
    <div class="groups">
      <template v-for="(g, i) in task.groups" :key="i">
        <span v-if="i > 0" class="g-arrow">→</span>
        <span class="g-pill" :class="{ last: i === task.groups.length - 1 }">{{ g }}</span>
      </template>
    </div>
    <div class="tc-props">
      <span class="pill pill-time">⏱ {{ task.timeout_min }} 分钟</span>
      <span class="pill pill-after">{{ AFTER_DONE_LABEL[task.after_done] || task.after_done }}</span>
    </div>
  </div>
</template>

<style scoped>
.task-card .tc-head { display: flex; align-items: center; gap: var(--space-3); }
.tc-name { flex: 1; min-width: 0; font-size: var(--font-lg); font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.task-card .groups { margin-top: 8px; }
</style>
