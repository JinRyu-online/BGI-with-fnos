<script setup lang="ts">
import type { JobStateMeta } from '../constants'

const props = defineProps<{ meta: JobStateMeta }>()
</script>

<template>
  <span class="dot" :class="[props.meta.dotClass, { breathe: props.meta.breathe }]"></span>
</template>

<style scoped>
.dot {
  width: 20px; height: 20px; border-radius: 50%; flex-shrink: 0;
  background: var(--dot, var(--state-idle));
  transition: background .3s;
}
/* 呼吸动画：running 黄 / completing 蓝（光晕用配套 --glow token） */
.dot.breathe { animation: breathe 1.6s ease-in-out infinite; }
@keyframes breathe {
  0%, 100% { box-shadow: 0 0 0 0 var(--glow, transparent); transform: scale(1); }
  50%      { box-shadow: 0 0 0 10px transparent; transform: scale(1.1); }
}
.dot.s-idle       { --dot: var(--state-idle); }
.dot.s-running    { --dot: var(--state-running);    --glow: var(--glow-running); }
.dot.s-completing { --dot: var(--state-completing); --glow: var(--glow-completing); }
.dot.s-done       { --dot: var(--state-done); }
.dot.s-failed     { --dot: var(--state-failed); }
.dot.s-timed-out  { --dot: var(--state-timed-out); }
.dot.s-aborted    { --dot: var(--state-aborted); }
.dot.s-abnormal   { --dot: var(--state-abnormal-exit); }
</style>
