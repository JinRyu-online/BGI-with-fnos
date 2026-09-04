<script setup lang="ts">
/**
 * App 外壳：Header（logo + 设备 chip）→ 路由页（独立滚动）→ TabBar → Toast/Confirm 覆盖层。
 * 布局规格照原型：app-shell max-width 480px、桌面手机框、safe-area 安全区。
 * 底部 tab 图标为原神 Q 版角色表情（派蒙/荧/空/莉奈娅），不满意可直接替换
 * src/assets/tabicons/ 下同名 PNG（96×96，透明底最佳）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useConfig } from './composables/useConfig'
import { bootRuntime } from './composables/jobRuntime'
import ToastHost from './components/ToastHost.vue'
import ConfirmDialog from './components/ConfirmDialog.vue'
import logoUrl from './assets/tabicons/logo.png'
import iconPaimon from './assets/tabicons/paimon.png'
import iconYing from './assets/tabicons/ying.png'
import iconKong from './assets/tabicons/kong.png'
import iconLinya from './assets/tabicons/linya.png'

const route = useRoute()
const router = useRouter()
const { configState, paired } = useConfig()

const deviceChip = computed(() => {
  const t = configState.config?.default_target
  if (t) return t.hostname || t.ip
  return '未配对'
})

const tabs = [
  { path: '/', icon: iconPaimon, label: '状态' },
  { path: '/tasks', icon: iconKong, label: '任务' },
  { path: '/history', icon: iconYing, label: '历史' },
  { path: '/settings', icon: iconLinya, label: '设置' },
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
      <img class="app-logo" :src="logoUrl" alt="" />
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
        <img class="tab-icon" :src="t.icon" :alt="t.label" />
        <span>{{ t.label }}</span>
      </button>
    </nav>

    <ToastHost />
    <ConfirmDialog />
  </div>
  <div class="desktop-note">BetterGI Trigger · 移动端应用 · 建议以 Chrome DevTools 390×844 视口查看</div>
</template>

<style scoped>
.app-logo {
  width: 30px;
  height: 30px;
  border-radius: 8px;
  flex-shrink: 0;
}
.tab-icon {
  width: 26px;
  height: 26px;
  object-fit: contain;
  line-height: 1;
  /* 透明底表情在浅色 tab 栏上加浅描边晕光，避免白色部分融入背景 */
  filter: drop-shadow(0 1px 2px rgba(15, 23, 42, 0.18));
  transition: transform 0.15s;
}
.tab.active .tab-icon {
  transform: scale(1.15);
}
</style>
