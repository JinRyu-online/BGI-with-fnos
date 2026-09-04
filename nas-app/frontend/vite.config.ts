import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  // NAS 后端经 /spa 挂载本 SPA（main.py StaticFiles(directory=static/spa, html=True)）。
  // base 必须与挂载路径一致，否则产物 index.html 引用 /assets/* 会 404 白页。
  base: '/spa/',
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // NAS 后端同源代理：HTTP + WebSocket（/api/ws/logs/*）统一转发
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    target: 'es2022',
  },
})
