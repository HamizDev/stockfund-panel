import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

const backendHost = process.env.BACKEND_HOST || '127.0.0.1'
const proxyHost = ['0.0.0.0', '::'].includes(backendHost) ? '127.0.0.1' : backendHost
const backendPort = process.env.BACKEND_PORT || '3018'
const backendTarget = `http://${proxyHost}:${backendPort}`

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host: '0.0.0.0',   // dev.sh / dev.ps1 会用 CLI --host 覆盖
    port: 3011,
    proxy: {
      // dev 时 /api 转发到与启动脚本相同的 FastAPI 地址
      '/api': {
        target: backendTarget,
        // SSE 端点需要禁用缓冲
        configure: (proxy) => {
          proxy.on('proxyReq', (_proxyReq, req) => {
            if (req.url?.includes('/stream')) {
              _proxyReq.setHeader('Accept', 'text/event-stream')
              _proxyReq.setHeader('Cache-Control', 'no-cache')
              _proxyReq.setHeader('Connection', 'keep-alive')
            }
          })
        },
      },
      '/health': backendTarget,
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    // 不清空旧构建产物: 用户标签页可能停留在旧版应用, 其引用的旧 hash chunk
    // 必须继续可访问, 否则部署后旧标签页切路由会 404。旧文件会自然累积,
    // 定期手动清理 dist 即可 (文件名都带 content hash, 不会冲突)。
    emptyOutDir: false,
    rollupOptions: {
      output: {
        // 把重型图表库拆到独立 chunk, 避免打进主包 + 让页面按需加载。
        // 用函数形式按 node_modules 路径匹配, 比对象形式更可靠。
        manualChunks(id) {
          if (id.includes('node_modules')) {
            if (id.includes('echarts')) return 'echarts'
            if (id.includes('lightweight-charts')) return 'lightweight-charts'
          }
        },
      },
    },
  },
})
