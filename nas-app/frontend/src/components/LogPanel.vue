<script setup lang="ts">
/**
 * 实时日志面板（深色，照原型 ① 状态页 log-panel）
 * - 自动滚动：距底 ≤40px 跟随；>40px 判定上滑暂停 + 悬浮"↓ 回到底部"
 * - 行缓冲 400 行（裁剪在 useLogStream 内做），角标显示总行数
 * - WS 状态点：connecting 黄闪 / connected 绿 / error 红 / closed 灰
 */
import { nextTick, onMounted, ref } from 'vue'
import { logStreamState, wsStatusText } from '../composables/useLogStream'

defineProps<{ jobId?: string }>()

const body = ref<HTMLElement | null>(null)

function followBottom(): void {
  nextTick(() => {
    const el = body.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function onScroll(): void {
  const el = body.value
  if (!el) return
  logStreamState.scrollPaused = (el.scrollHeight - el.scrollTop - el.clientHeight) > 40
  if (!logStreamState.scrollPaused) return
}

function jumpToBottom(): void {
  const el = body.value
  if (el) el.scrollTop = el.scrollHeight
  logStreamState.scrollPaused = false
}

onMounted(followBottom)
// 新行到达时若未暂停则跟随到底（watch 由模板外的响应式驱动）
import { watch } from 'vue'
watch(
  () => [logStreamState.lines.length, logStreamState.scrollPaused],
  () => { if (!logStreamState.scrollPaused) followBottom() },
)
</script>

<template>
  <div class="log-panel">
    <div class="log-head">
      <span class="ws-dot" :class="'ws-' + logStreamState.wsState"></span>
      <span>{{ wsStatusText(logStreamState.wsState) }}</span>
      <span class="log-job">#{{ jobId || logStreamState.jobId || '—' }}</span>
      <span class="log-count">{{ logStreamState.totalLines }} 行</span>
    </div>
    <div ref="body" class="log-body" @scroll="onScroll">
      <span v-for="(l, i) in logStreamState.lines" :key="i" class="log-line" :class="l.cls">{{ l.text }}</span>
    </div>
    <button v-if="logStreamState.scrollPaused" class="log-jump" @click="jumpToBottom">↓ 回到底部</button>
  </div>
</template>

<style scoped>
.log-panel {
  position: relative;
  background: var(--log-bg);
  border: 1px solid var(--log-border);
  border-radius: var(--radius-md);
  overflow: hidden;
  margin-bottom: var(--space-3);
}
.log-head {
  display: flex; align-items: center; gap: 6px;
  padding: 8px 12px;
  background: var(--log-head-bg);
  border-bottom: 1px solid var(--log-border);
  font-size: var(--font-xs); color: var(--log-text-dim);
}
.log-job { font-family: var(--font-mono); color: var(--log-text-dim); min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.log-count {
  margin-left: auto;
  font-family: var(--font-mono);
  padding: 1px 8px; border-radius: var(--radius-full);
  background: rgba(255, 255, 255, .06); color: var(--log-text-dim);
}
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

.log-body {
  height: var(--logpanel-height);
  overflow-y: auto; -webkit-overflow-scrolling: touch;
  padding: 10px 12px;
  font-family: var(--font-mono);
  font-size: 12px; line-height: 1.6;
  color: var(--log-text);
  white-space: pre-wrap; word-break: break-all;
}
.log-line { display: block; padding: 1px 0; border-bottom: 1px solid rgba(255, 255, 255, .04); }
.log-line.kw  { color: var(--log-keyword); font-weight: 700; }
.log-line.err { color: var(--log-error); }
.log-line.sys { color: var(--log-sys); }
/* 上滑暂停跟随时的"回到底部"悬浮 pill（::after 外扩命中区至 ≥44px） */
.log-jump {
  position: absolute; right: 10px; bottom: 10px;
  border: none; border-radius: var(--radius-full);
  padding: 6px 12px; min-height: 0;
  font-size: var(--font-xs); font-weight: 600;
  background: rgba(255, 255, 255, .12); color: #fff;
  -webkit-backdrop-filter: blur(4px); backdrop-filter: blur(4px);
}
.log-jump::after { content: ''; position: absolute; inset: -8px; }
</style>
