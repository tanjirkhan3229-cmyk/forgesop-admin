import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Standalone operator SPA. Talks only to the admin API (admin-api.forgesop.app)
// — never to the tenant product. The dev proxy points /v1 at the local admin
// backend (port 8000).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/v1': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
})
