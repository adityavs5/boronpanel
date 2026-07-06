import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// Build output lands in ../static/dist so FastAPI (which already mounts
// /static) serves the SPA bundle; `base` makes every asset URL absolute under
// /static/dist/ so the SPA works no matter which client-side route the user
// deep-links to (the FastAPI catch-all returns index.html for unknown paths).
export default defineConfig({
  plugins: [react()],
  base: '/static/dist/',
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  build: {
    outDir: path.resolve(__dirname, '../static/dist'),
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
  },
  server: {
    // `npm run dev` proxies API + auth calls to the live FastAPI (self-signed
    // cert -> secure:false). Production serving is via the built bundle, not
    // this dev server.
    port: 5173,
    proxy: {
      '/api': { target: 'https://127.0.0.1:9443', changeOrigin: true, secure: false },
      '/login': { target: 'https://127.0.0.1:9443', changeOrigin: true, secure: false },
      '/logout': { target: 'https://127.0.0.1:9443', changeOrigin: true, secure: false },
      '/change-password': { target: 'https://127.0.0.1:9443', changeOrigin: true, secure: false },
    },
  },
})
