<script setup lang="ts">
/** 确认弹窗（Promise 化）：蒙层点击 = 取消，确认按钮文案随场景变化。 */
import { confirmState, useConfirm } from '../composables/useConfirm'

const { resolve } = useConfirm()
</script>

<template>
  <div v-if="confirmState.visible" class="modal-mask show" @click.self="resolve(false)">
    <div class="modal">
      <div class="m-title">{{ confirmState.title }}</div>
      <div class="m-body">{{ confirmState.body }}</div>
      <div class="m-actions">
        <button class="btn btn-secondary" @click="resolve(false)">取消</button>
        <button class="btn btn-danger" @click="resolve(true)">{{ confirmState.okText }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-mask {
  position: absolute; inset: 0; z-index: 80;
  background: rgba(15, 23, 42, .45);
  display: flex; align-items: center; justify-content: center;
  padding: var(--space-6);
  animation: fadeIn .15s ease-out;
}
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
.modal {
  width: 100%; max-width: 320px;
  background: var(--surface); border-radius: var(--radius-lg);
  padding: var(--space-5);
  box-shadow: var(--shadow-float);
  animation: popIn .18s ease-out;
}
@keyframes popIn { from { transform: scale(.94); opacity: 0; } to { transform: scale(1); opacity: 1; } }
.m-title { font-size: var(--font-lg); font-weight: 700; margin-bottom: var(--space-2); }
.m-body { font-size: var(--font-base); color: var(--text-2); line-height: 1.6; margin-bottom: var(--space-5); }
.m-actions { display: flex; gap: var(--space-3); }
.m-actions .btn { flex: 1; }
</style>
