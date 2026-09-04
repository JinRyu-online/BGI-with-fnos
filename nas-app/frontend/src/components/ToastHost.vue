<script setup lang="ts">
/** 全局 Toast 容器：顶部滑入 → 2.2s 停留 → 淡出上移（照原型 §3/§4.6）。 */
import { toastState } from '../composables/useToast'
</script>

<template>
  <div class="toast-wrap">
    <div
      v-for="t in toastState.items"
      :key="t.id"
      class="toast"
      :class="['t-' + t.type, { out: t.leaving }]"
    >{{ t.msg }}</div>
  </div>
</template>

<style scoped>
.toast-wrap {
  position: absolute; top: calc(var(--header-height) + 8px);
  left: 0; right: 0; z-index: 60;
  display: flex; flex-direction: column; align-items: center; gap: var(--space-2);
  pointer-events: none;
}
.toast {
  max-width: 86%;
  background: rgba(17, 24, 39, .95); color: #fff;
  font-size: var(--font-sm); font-weight: 500; line-height: 1.4;
  padding: 10px 16px; border-radius: var(--radius-full);
  box-shadow: var(--shadow-float);
  display: flex; align-items: center; gap: 8px;
  animation: toastIn .25s ease-out;
  transition: opacity .3s, transform .3s;
}
.toast.out { opacity: 0; transform: translateY(-8px); }
.toast::before { content: ''; width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.toast.t-success::before { background: var(--state-done); }
.toast.t-error::before   { background: var(--log-error); }
.toast.t-info::before    { background: var(--brand); }
@keyframes toastIn { from { opacity: 0; transform: translateY(-12px); } to { opacity: 1; transform: none; } }
</style>
