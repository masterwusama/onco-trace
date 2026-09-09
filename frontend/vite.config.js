import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发期把 /api 代理到后端（8001）。生产不用这里：npm run build 出 dist/ 之后，
// 后端在同一个端口上把站点与接口一起托管，本来就是同源的。
// 端口写死 5174 而不是用 vite 默认的 5173：本机 5173 长期是 stock-value-analysis
// 的开发服务器（与"后端用 8001 不用 8000"是同一件事）。strictPort 让撞端口直接报错，
// 而不是悄悄滑到下一个空闲端口——那样代理打到谁身上没人看得出来。
export default defineConfig({
  plugins: [vue()],
  build: {
    rollupOptions: {
      // echarts 只在详情页的统计与生存率两屏出现，单独切一个 chunk：
      // 它是这站最大的一块依赖（约 500 KB），与应用代码分开才谈得上分开缓存。
      output: {
        manualChunks: {
          echarts: ['echarts/core', 'echarts/charts', 'echarts/components', 'echarts/renderers'],
        },
      },
    },
  },
  server: {
    host: true,
    port: 5174,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8001', changeOrigin: true },
    },
  },
})
