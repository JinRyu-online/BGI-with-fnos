<script setup lang="ts">
/**
 * 终端卡片外壳（LogPanel / LogDetailPage 共用）：统一深色日志区的应用内嵌感。
 * - 外壳 token 化：--radius-lg 圆角 / --border 边框 / --shadow-card 阴影
 * - 头部条：--log-head-bg 深色底 + rgba(201,168,106,.08) 应用金高光渐变（同族感）；
 *   文字用 --log-head-text（对 --log-head-bg ≈5.4:1，达标 4.5:1）
 * - props：title（头部标题）+ bodyHeight + scrollChaining + LogBody 的 props
 *   显式透传对象（lines/followKey/showBadge/onLoadMore/loadingMore，
 *   bodyHeight 映射 LogBody 的 height）；默认插槽放头部元信息
 *   （WS 状态点 / 行数 / 命中数）
 * - LogBody 由本组件内部渲染，调用方不直接接触
 */
import { computed } from 'vue'
import LogBody from './LogBody.vue'
import type { LogLine } from '../composables/useLogStream'

const props = withDefaults(
  defineProps<{
    /** 头部标题（左侧） */
    title?: string
    /** 头部元信息之外的透传：已分类日志行 */
    lines: LogLine[]
    /** 透传 LogBody.followKey（跟随键） */
    followKey?: number
    /** 透传 LogBody.showBadge（离底新增行数角标开关） */
    showBadge?: boolean
    /** 透传 LogBody.onLoadMore（"加载更早"回调） */
    onLoadMore?: () => void
    /** 透传 LogBody.loadingMore */
    loadingMore?: boolean
    /** LogBody 滚动区高度（缺省 --logpanel-height；详情页可传更大值） */
    bodyHeight?: string
    /** 透传 LogBody.scrollChaining（滚动链：详情页 'contain'，状态页缺省 'allow'） */
    scrollChaining?: 'allow' | 'contain'
  }>(),
  {
    title: '',
    followKey: 0,
    showBadge: false,
    loadingMore: false,
    bodyHeight: '',
    scrollChaining: 'allow',
  },
)

/** 显式透传对象：与 LogBody props 一一对应（bodyHeight → height），不做 $attrs 魔法 */
const bodyProps = computed(() => ({
  lines: props.lines,
  followKey: props.followKey,
  showBadge: props.showBadge,
  onLoadMore: props.onLoadMore,
  loadingMore: props.loadingMore,
  height: props.bodyHeight,
  scrollChaining: props.scrollChaining,
}))
</script>

<template>
  <div class="log-card">
    <div class="log-card-head">
      <span v-if="title" class="log-card-title">{{ title }}</span>
      <!-- 元信息插槽：WS 状态点 / 行数 / 命中数（实时页/详情页各自注入） -->
      <slot />
    </div>
    <LogBody v-bind="bodyProps" />
  </div>
</template>

<style scoped>
/* flex 列根：供详情页定高链（.log-card flex:1 → LogBody flex:1）生效；
   状态页 block 容器内 flex 规则不生效，布局零变化。
   margin-bottom 保留 —— 状态页 LogPanel→操作区间距依赖它；详情页定高链下
   由父级 flex:1 + min-height:0 承担高度，边距经页面侧对齐（见 LogDetailPage）。 */
.log-card {
  display: flex; flex-direction: column;
  background: var(--log-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-card);
  overflow: hidden;
  margin-bottom: var(--space-3);
}
.log-card-head {
  display: flex; align-items: center; gap: 6px;
  min-height: 36px; padding: 6px 12px;
  background:
    linear-gradient(180deg, rgba(201, 168, 106, .08) 0%, rgba(201, 168, 106, 0) 100%),
    var(--log-head-bg);
  border-bottom: 1px solid var(--log-border);
  font-size: var(--font-xs); color: var(--log-head-text);
}
.log-card-title { font-weight: 600; flex-shrink: 0; }
</style>
