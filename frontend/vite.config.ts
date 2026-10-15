import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const appBase = process.env.VITE_APP_BASE_PATH || '/'

// https://vite.dev/config/
export default defineConfig({
  base: appBase,
  plugins: [react()],
  server: {
    port: 5185,
    strictPort: true,
    proxy: {
      [`${appBase}api`]: {
        target: 'http://127.0.0.1:8010',
        changeOrigin: true,
        rewrite: (path) => path.replace(appBase, '/'),
      }
    }
  }
})
