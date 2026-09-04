import { createRouter, createWebHistory } from 'vue-router'
import StatusPage from './pages/StatusPage.vue'
import TasksPage from './pages/TasksPage.vue'
import HistoryPage from './pages/HistoryPage.vue'
import SettingsPage from './pages/SettingsPage.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'status', component: StatusPage },
    { path: '/tasks', name: 'tasks', component: TasksPage },
    { path: '/history', name: 'history', component: HistoryPage },
    { path: '/settings', name: 'settings', component: SettingsPage },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
