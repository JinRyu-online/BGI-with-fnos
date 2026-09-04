<script setup lang="ts">
/**
 * App 外壳：Header（设备 chip）→ 路由页（独立滚动）→ TabBar → Toast/Confirm 覆盖层。
 * 布局规格照原型：app-shell max-width 480px、桌面手机框、safe-area 安全区。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useConfig } from './composables/useConfig'
import { bootRuntime } from './composables/jobRuntime'
import ToastHost from './components/ToastHost.vue'
import ConfirmDialog from './components/ConfirmDialog.vue'

const route = useRoute()
const router = useRouter()
const { configState, paired } = useConfig()

const deviceChip = computed(() => {
  const t = configState.config?.default_target
  if (t) return t.hostname || t.ip
  return '未配对'
})

const tabs = [
  { path: '/', icon: '📡', label: '状态' },
  { path: '/tasks', icon: '⚡', label: '任务' },
  { path: '/history', icon: '📜', label: '历史' },
  { path: '/settings', icon: '⚙️', label: '设置' },
] as const

function isActive(path: string): boolean {
  return route.path === path
}

function go(path: string): void {
  if (!isActive(path)) void router.push(path)
}

const ready = ref(false)
onMounted(async () => {
  await bootRuntime()
  ready.value = true
})
</script>

<template>
  <div class="app-shell">
    <header class="app-header">
      <div class="app-title">BetterGI Trigger</div>
      <div class="device-chip" :class="{ unpaired: !paired }">{{ deviceChip }}</div>
    </header>

    <main class="pages">
      <div class="page" :key="route.path">
        <template v-if="ready">
          <RouterView />
        </template>
      </div>
    </main>

    <nav class="tabbar">
      <button
        v-for="t in tabs"
        :key="t.path"
        class="tab"
        :class="{ active: isActive(t.path) }"
        @click="go(t.path)"
      >
        <span class="tab-icon">{{ t.icon }}</span>
        <span>{{ t.label }}</span>
      </button>
    </nav>

    <ToastHost />
    <ConfirmDialog />
  </div>
  <div class="desktop-note">BetterGI Trigger · 移动端应用 · 建议以 Chrome DevTools 390×844 视口查看</div>
</template>
