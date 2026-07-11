import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import fs from 'fs'

// The panel version's single source of truth is version.py at the repo root
// (see that file's docstring). Read it at build time so the bundle -- built
// from the same tree scripts/release.sh tarballs -- always carries the
// matching version for pre-auth display (the login page can't call the
// authenticated /api/v1/version).
function readBoronVersion() {
  try {
    const text = fs.readFileSync(path.resolve(__dirname, '../version.py'), 'utf8')
    const m = text.match(/BORON_VERSION\s*=\s*"([^"]+)"/)
    if (m) return m[1]
  } catch {
    /* fall through */
  }
  return '0.0.0-dev'
}

// Build output lands in ../static/dist so FastAPI (which already mounts
// /static) serves the SPA bundle; `base` makes every asset URL absolute under
// /static/dist/ so the SPA works no matter which client-side route the user
// deep-links to (the FastAPI catch-all returns index.html for unknown paths).
export default defineConfig({
  plugins: [react()],
  base: '/static/dist/',
  define: {
    __BORON_VERSION__: JSON.stringify(readBoronVersion()),
  },
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  build: {
    outDir: path.resolve(__dirname, '../static/dist'),
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        // Stable vendor chunks: the framework core changes far less often than
        // app code, so returning users keep it cached across panel updates.
        // Monaco/xterm/recharts are NOT listed — they stay inside the lazy
        // page chunks that use them and never block first paint.
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('recharts') || id.includes('/d3-') || id.includes('victory-vendor')) return 'charts'
          if (id.includes('@radix-ui')) return 'radix'
          if (id.includes('lucide-react')) return 'icons'
          if (
            id.includes('/react/') || id.includes('/react-dom/') || id.includes('/scheduler/') ||
            id.includes('react-router') || id.includes('@remix-run')
          ) return 'react'
          return undefined
        },
      },
    },
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
