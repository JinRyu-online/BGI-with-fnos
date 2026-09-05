import { createRouter, createWebHistory } from 'vue-router'
import StatusPage from './pages/StatusPage.vue'
import TasksPage from './pages/TasksPage.vue'
import HistoryPage from './pages/HistoryPage.vue'
import SettingsPage from './pages/SettingsPage.vue'
import SchedulesPage from './pages/SchedulesPage.vue'

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
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
