<script setup lang="ts">
/**
 * App 外壳：Header（logo + 退出）→ 路由页（独立滚动）→ TabBar → Toast/Confirm 覆盖层。
 * 布局规格照原型：app-shell max-width 480px、桌面手机框、safe-area 安全区。
 * 底部 tab 双态图标：<功能名>_gray.png 未选（灰度）/ <功能名>.png 选中（彩色 + 弹跳）。
 * 换图直接替换 src/assets/tabicons/ 下同名 PNG（96×96 透明底）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useConfig } from './composables/useConfig'
import { bootRuntime } from './composables/jobRuntime'
import ToastHost from './components/ToastHost.vue'
import ConfirmDialog from './components/ConfirmDialog.vue'
import logoUrl from './assets/tabicons/logo.png'
import iconStatus from './assets/tabicons/status.png'
import iconStatusGray from './assets/tabicons/status_gray.png'
import iconTasks from './assets/tabicons/tasks.png'
import iconTasksGray from './assets/tabicons/tasks_gray.png'
import iconHistory from './assets/tabicons/history.png'
import iconHistoryGray from './assets/tabicons/history_gray.png'
import iconSettings from './assets/tabicons/settings.png'
import iconSettingsGray from './assets/tabicons/settings_gray.png'
import iconSchedules from './assets/tabicons/schedules.png'
import iconSchedulesGray from './assets/tabicons/schedules_gray.png'

const route = useRoute()
const router = useRouter()
const { configState, paired } = useConfig()

const deviceChip = computed(() => {
  const t = configState.config?.default_target
  if (t) return t.hostname || t.ip
  return '未配对'
})

const tabs = [
  { path: '/', icon: iconStatus, iconGray: iconStatusGray, label: '状态' },
  { path: '/tasks', icon: iconTasks, iconGray: iconTasksGray, label: '任务' },
  { path: '/schedules', icon: iconSchedules, iconGray: iconSchedulesGray, label: '定时' },
  { path: '/history', icon: iconHistory, iconGray: iconHistoryGray, label: '历史' },
  { path: '/settings', icon: iconSettings, iconGray: iconSettingsGray, label: '设置' },
] as const

/** tab 直系子页（/tasks/xxx）由 startsWith 前缀自动覆盖；
 *  首段不等于父 tab 的路由在此登记（如 /logs 归属 历史 tab）。 */
const PARENT_TAB: Record<string, string> = { '/logs': '/history' }

function isActive(path: string): boolean {
  if (route.path === path) return true
  if (route.path.startsWith(path + '/')) return true   // tab 直系子页
  const seg2 = route.path.split('/').slice(0, 2).join('/')
  return PARENT_TAB[seg2] === path                     // 登记的跨归属子页
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
        <img class="tab-icon" :src="isActive(t.path) ? t.icon : t.iconGray" :alt="t.label" />
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
  transition: transform 0.15s;
}
.tab.active .tab-icon {
  transform: scale(1.2) translateY(-2px);
  animation: tabPop .35s cubic-bezier(.34, 1.56, .64, 1);
}
@keyframes tabPop {
  0% { transform: scale(.7) translateY(2px); }
  60% { transform: scale(1.28) translateY(-3px); }
  100% { transform: scale(1.2) translateY(-2px); }
}
</style>
