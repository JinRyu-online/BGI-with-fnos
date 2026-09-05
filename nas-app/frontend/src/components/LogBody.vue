<script setup lang="ts">
/**
 * 日志展示体（LogPanel 与 LogDetailPage 共用，评审必须项：样式不复制）。
 * - 三色高亮（kw 金 / err 红 / sys 青）+ 等宽 12px + pre-wrap
 * - 深色日志区（--log-bg）
 * - 可选"加载更多"按钮（详情页静态数据分页用；实时页不传 onLoadMore 则不显示）
 * - 不含 WS 状态点 / 总行数角标（那些属于 LogPanel 的实时语义）
 */
import type { LogLine } from '../composables/useLogStream'

withDefaults(
  defineProps<{
    /** 已分类的日志行 */
    lines: LogLine[]
    /** 过滤模式：命中行子集（高亮角标等由调用方处理，这里只影响展示语义占位） */
    filtered?: boolean
    /** 滚动区高度（缺省用 --logpanel-height；详情页可传更大值） */
    height?: string
    /** 加载更多回调（不传 = 不显示按钮） */
    onLoadMore?: () => void
    /** 加载更多进行中 */
    loadingMore?: boolean
  }>(),
  { filtered: false, height: '', loadingMore: false },
)
</script>

<template>
  <div class="log-body" :style="height ? { height } : undefined">
    <span v-for="(l, i) in lines" :key="i" class="log-line" :class="l.cls">{{ l.text }}</span>
    <div v-if="onLoadMore" class="log-more-wrap">
      <button class="log-more" :disabled="loadingMore" @click="onLoadMore">
        <span v-if="loadingMore" class="log-more-spinner"></span>
        {{ loadingMore ? '加载中…' : '加载更早日志' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
/* 与原 LogPanel .log-body 逐条一致（唯一真相源，禁止复制修改） */
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

/* 加载更多（详情页分页）：深色区内的轻量按钮，命中区外扩至 ≥44px */
.log-more-wrap { padding: 10px 0 4px; text-align: center; }
.log-more {
  position: relative;
  display: inline-flex; align-items: center; gap: 6px;
  min-height: 32px;
  border: 1px solid var(--log-border); border-radius: var(--radius-full);
  padding: 6px 14px;
  background: rgba(255, 255, 255, .06); color: var(--log-text-dim);
  font-size: var(--font-xs); font-weight: 600;
}
.log-more::after { content: ''; position: absolute; inset: -6px; }
.log-more-spinner {
  width: 12px; height: 12px; border-radius: 50%;
  border: 2px solid rgba(255, 255, 255, .2); border-top-color: var(--log-text);
  animation: moreSpin .8s linear infinite;
}
@keyframes moreSpin { to { transform: rotate(360deg); } }
</style>
