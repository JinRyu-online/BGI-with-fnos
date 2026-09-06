import { createRouter, createWebHistory } from 'vue-router'
import StatusPage from './pages/StatusPage.vue'
import TasksPage from './pages/TasksPage.vue'
import HistoryPage from './pages/HistoryPage.vue'
import SettingsPage from './pages/SettingsPage.vue'
import SchedulesPage from './pages/SchedulesPage.vue'
import LogDetailPage from './pages/LogDetailPage.vue'

export const router = createRouter({
  // base 必须与挂载路径一致：缺省时 /spa/schedules 深链匹配不到任何路由，
  // 被 pathMatch 兜底重定向回 /（真实踩坑：SPA 上线以来深链/刷新一直回首页）。
  history: createWebHistory('/spa/'),
  routes: [
    { path: '/', name: 'status', component: StatusPage },
    { path: '/tasks', name: 'tasks', component: TasksPage },
    { path: '/schedules', name: 'schedules', component: SchedulesPage },
    { path: '/history', name: 'history', component: HistoryPage },
    { path: '/settings', name: 'settings', component: SettingsPage },
    // 历史日志详情：必须注册在 catch-all 之前；两段路由刷新靠后端
    // spa_fallback 深链回退 index.html（main.py 已修复含 / 路由的 404）
    { path: '/logs/:jobId', name: 'log-detail', component: LogDetailPage },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
