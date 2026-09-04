// 简单自测脚本：验证 dev server 各路由与关键模块可访问（HTTP 200）
const base = process.env.SMOKE_URL || 'http://localhost:5173'
const paths = [
  '/', '/tasks', '/history', '/settings',
  '/src/main.ts', '/src/App.vue',
  '/src/pages/StatusPage.vue', '/src/pages/TasksPage.vue',
  '/src/pages/HistoryPage.vue', '/src/pages/SettingsPage.vue',
  '/src/components/LogPanel.vue', '/src/composables/useJob.ts',
  '/src/constants.ts', '/src/styles/tokens.css',
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
