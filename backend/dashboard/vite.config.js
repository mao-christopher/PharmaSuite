import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// API_URL points the dev proxy at another backend, e.g. API_URL=http://localhost:8001
const api = process.env.API_URL || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: api,
        changeOrigin: true,
      },
      '/ws': {
        target: api.replace(/^http/, 'ws'),
        ws: true,
      }
    }
  }
})
