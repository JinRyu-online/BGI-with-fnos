<script setup lang="ts">
/**
 * 日志展示体（LogPanel 与 LogDetailPage 共用）：三明治结构
 *   root(relative, 非滚动) > .log-scroll(真实滚动区) + .log-jump(absolute 浮动回底钮)
 * 滚动状态机内聚于此、与真实滚动元素同层（外层组件不再自行实现跟随/回底）：
 * - 跟随：followKey 变化且在底（scrollHeight - scrollTop - clientHeight ≤ 40）→
 *   置脏 + 单 rAF 消费（双帧滚动；高频日志下 reflow 钳到每帧一次）
 *   followKey 语义：实时页传 totalLines（逻辑计数不封顶——lines.length 会被
 *   400 行缓冲裁剪封顶导致跟随静默失效）；详情页传内容版本号（仅 fetch/loadMore
 *   成功后 +1，过滤不 bump）
 * - 上滑离底：暂停跟随，新增量计入角标（仅 showBadge=true 显示）；回底清零
 * - 回底钮：40px 圆钮 + ::after 热区外扩至 48px（≥44px）；smooth 特性检测 +
 *   prefers-reduced-motion 双重降级（不支持 scrollBehavior 或偏好减少动态 → 直接赋值）
 * - "加载更早"视口保持：记录 prevScrollHeight，DOM 更新后按 scrollHeight 差值补偿
 *   scrollTop（不依赖原生 overflow-anchor——Safari/iOS 不支持）；原本在底部则回底
 */
import { nextTick, onMounted, ref, watch } from 'vue'
import type { LogLine } from '../composables/useLogStream'

const props = withDefaults(
  defineProps<{
    /** 已分类的日志行 */
    lines: LogLine[]
    /** 跟随键（见文件头注释的语义说明） */
    followKey?: number
    /** 滚动区高度（缺省 --logpanel-height；详情页可传更大值） */
    height?: string
    /** 加载更多回调（不传 = 不显示按钮） */
    onLoadMore?: () => void
    /** 加载更多进行中 */
    loadingMore?: boolean
    /** 是否显示离底新增行数角标（实时页开；静态详情页关，避免 loadMore 时角标泄漏） */
    showBadge?: boolean
  }>(),
  { followKey: 0, height: '', loadingMore: false, showBadge: false },
)

/** 在底判定阈值（px） */
const BOTTOM_THRESHOLD = 40

const scrollEl = ref<HTMLElement | null>(null)
/** 是否吸附在底部（跟随中） */
const atBottom = ref(true)
/** 离底期间新增行数（角标） */
const newCount = ref(0)

/* ---- 平滑滚动能力：特性检测 + reduced-motion 双重降级，只算一次 ---- */
const canSmooth =
  'scrollBehavior' in document.documentElement.style &&
  !(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false)

function scrollToBottom(smooth = false): void {
  const el = scrollEl.value
  if (!el) return
  if (smooth && canSmooth) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  else el.scrollTop = el.scrollHeight
}

/* ---- 跟随：置脏 + 单 rAF 消费（双帧：布局完成后再滚）---- */
let rafPending = false
function scheduleFollow(): void {
  if (rafPending) return
  rafPending = true
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      rafPending = false
      if (atBottom.value) scrollToBottom()
    })
  })
}

watch(
  () => props.followKey,
  (nv, ov) => {
    if (nv === ov) return
    if (atBottom.value) {
      scheduleFollow()
    } else {
      // 离底期间：暂停跟随，增量计入角标（详情页 showBadge=false 不显示）
      newCount.value += Math.max(0, nv - ov)
    }
  },
)

/* ---- 滚动事件（passive）：更新在底状态；手动滚回底部清零角标 ---- */
function onScroll(): void {
  const el = scrollEl.value
  if (!el) return
  atBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_THRESHOLD
  if (atBottom.value) newCount.value = 0
}

onMounted(() => {
  // 挂载即有内容（实时页缓冲回看 / 详情页首屏）→ 直接贴底（无动画）
  if (props.followKey > 0) scrollToBottom()
})

/* ---- 回底钮点击：平滑回底（能力降级时直接赋值）+ 角标清零 ---- */
function jumpToBottom(): void {
  scrollToBottom(true)
  newCount.value = 0
}

/* ---- 加载更早：scrollHeight 差值补偿视口（不依赖 overflow-anchor）---- */
async function handleLoadMore(): Promise<void> {
  const prevScrollHeight = scrollEl.value?.scrollHeight ?? 0
  const wasAtBottom = atBottom.value
  await props.onLoadMore?.()
  await nextTick()
  const el = scrollEl.value
  if (!el) return
  if (wasAtBottom) {
    scrollToBottom()
  } else {
    el.scrollTop += el.scrollHeight - prevScrollHeight
  }
}
</script>

<template>
  <div class="log-body-root">
    <div
      ref="scrollEl"
      class="log-scroll"
      :style="height ? { height } : undefined"
      @scroll.passive="onScroll"
    >
      <span v-for="(l, i) in lines" :key="i" class="log-line" :class="l.cls">{{ l.text }}</span>
      <div v-if="onLoadMore" class="log-more-wrap">
        <button class="log-more" :disabled="loadingMore" @click="handleLoadMore">
          <span v-if="loadingMore" class="log-more-spinner"></span>
          {{ loadingMore ? '加载中…' : '加载更早日志' }}
        </button>
      </div>
    </div>
    <transition name="log-jump-fade">
      <button v-show="!atBottom" class="log-jump" aria-label="回到底部" @click="jumpToBottom">
        ↓
        <span v-if="showBadge && newCount > 0" class="log-jump-badge">
          {{ newCount > 99 ? '99+' : newCount }}
        </span>
      </button>
    </transition>
  </div>
</template>

<style scoped>
/* 三明治根：relative 定位上下文（浮钮锚点），自身不滚动 */
.log-body-root {
  position: relative;
  background: var(--log-bg);
}
/* 真实滚动区：高度单源 --logpanel-height（height prop 内联覆盖） */
.log-scroll {
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

/* 浮动"↓"回底钮：40px 圆 + ::after 热区外扩至 48px（≥44px） */
.log-jump {
  position: absolute; right: 12px; bottom: 12px;
  display: flex; align-items: center; justify-content: center;
  width: 40px; height: 40px;
  min-height: 0; padding: 0;
  border: 1px solid rgba(255, 255, 255, .12);
  border-radius: 50%;
  background: rgba(255, 255, 255, .14);
  color: #fff; font-size: 16px; font-weight: 600; line-height: 1;
  -webkit-backdrop-filter: blur(4px); backdrop-filter: blur(4px);
  box-shadow: 0 2px 8px rgba(0, 0, 0, .3);
}
.log-jump::after { content: ''; position: absolute; inset: -4px; border-radius: 50%; }
/* 离底新增行数角标（showBadge opt-in）：钮内计数，99+ 封顶显示 */
.log-jump-badge {
  position: absolute; top: -6px; right: -6px;
  display: flex; align-items: center; justify-content: center;
  min-width: 18px; height: 18px; padding: 0 5px;
  border-radius: var(--radius-full);
  background: var(--brand-strong); color: #fff;
  font-family: var(--font-mono); font-size: 10px; font-weight: 700; line-height: 1;
}
/* 浮钮出现/消失过渡（150ms，v-show 防闪烁） */
.log-jump-fade-enter-active, .log-jump-fade-leave-active { transition: opacity .15s ease, transform .15s ease; }
.log-jump-fade-enter-from, .log-jump-fade-leave-to { opacity: 0; transform: translateY(6px); }
@media (prefers-reduced-motion: reduce) {
  .log-jump-fade-enter-active, .log-jump-fade-leave-active { transition: none; }
}
</style>
