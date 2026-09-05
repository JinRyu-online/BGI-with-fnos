// 简单自测脚本：验证 dev server 各路由与关键模块可访问（HTTP 200）
// dev server 挂在 base=/spa/ 下（vite.config.ts），源文件模块也要带 /spa 前缀访问。
const base = process.env.SMOKE_URL || 'http://localhost:5173'
const paths = [
  '/spa/', '/spa/tasks', '/spa/schedules', '/spa/history', '/spa/settings',
  '/spa/src/main.ts', '/spa/src/App.vue',
  '/spa/src/pages/StatusPage.vue', '/spa/src/pages/TasksPage.vue',
  '/spa/src/pages/SchedulesPage.vue',
  '/spa/src/pages/HistoryPage.vue', '/spa/src/pages/SettingsPage.vue',
  '/spa/src/components/LogPanel.vue', '/spa/src/composables/useJob.ts',
  '/spa/src/constants.ts', '/spa/src/styles/tokens.css',
]
let fail = 0
for (const p of paths) {
  try {
    const r = await fetch(base + p)
    const ok = r.ok
    if (!ok) fail++
    console.log(`${ok ? 'OK ' : 'FAIL'} ${r.status} ${p}`)
  } catch (e) {
    fail++
    console.log(`FAIL ERR ${p}: ${e.message}`)
  }
}
console.log(fail === 0 ? 'SMOKE PASS' : `SMOKE FAIL (${fail})`)
// 注：不调用 process.exit —— Windows 上 fetch keep-alive 句柄未完全关闭时
// 强制退出可能触发 libuv 断言（UV_HANDLE_CLOSING）；正常退出即可。
