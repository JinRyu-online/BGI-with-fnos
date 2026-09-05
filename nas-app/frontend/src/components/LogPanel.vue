<script setup lang="ts">
/**
 * 实时日志面板（照原型 ① 状态页 log-panel）：LogCard 的实时语义包装。
 * - 外壳/头部条/滚动状态机全在 LogCard + LogBody（跟随/回底已下沉，本组件零实现）
 * - 头部元信息：WS 状态点（connecting 黄闪 / connected 绿 / error 红 / closed 灰）
 *   + #job + 总行数（totalLines 逻辑计数不封顶，兼作 followKey——lines.length
 *   会被 400 行缓冲裁剪封顶，用它跟随会在第 400 行后静默失效）
 * - 角标：showBadge 开（离底期间新增行数）
 */
import { logStreamState, wsStatusText } from '../composables/useLogStream'
import LogCard from './LogCard.vue'

defineProps<{ jobId?: string }>()
</script>

<template>
  <LogCard
    :lines="logStreamState.lines"
    :follow-key="logStreamState.totalLines"
    :show-badge="true"
  >
    <span class="ws-dot" :class="'ws-' + logStreamState.wsState"></span>
    <span class="ws-text">{{ wsStatusText(logStreamState.wsState) }}</span>
    <span class="log-job">#{{ jobId || logStreamState.jobId || '—' }}</span>
    <span class="log-count">{{ logStreamState.totalLines }} 行</span>
  </LogCard>
</template>

<style scoped>
/* WS 连接状态点：connecting 黄闪 / connected 绿 / error 红 / closed 灰 */
.ws-dot {
  width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
  background: var(--state-idle);
}
.ws-dot.ws-connecting { background: var(--state-running); animation: blink 1s infinite; }
.ws-dot.ws-connected  { background: var(--state-done); }
.ws-dot.ws-error      { background: var(--log-error); }
.ws-dot.ws-closed     { background: var(--state-idle); }
@keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: .25; } }

.ws-text { flex-shrink: 0; }
/* #job 可被长 id 挤压：min-width:0 + ellipsis */
.log-job {
  font-family: var(--font-mono); color: var(--log-head-text);
  min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.log-count {
  margin-left: auto;
  font-family: var(--font-mono);
  padding: 1px 8px; border-radius: var(--radius-full);
  background: rgba(255, 255, 255, .06); color: var(--log-head-text);
  flex-shrink: 0;
}
</style>
