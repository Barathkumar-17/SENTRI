import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Built into the Python package, so `sentri ui` serves it with no Node on the user's machine.
export default defineConfig({
  plugins: [react()],
  base: '/',
  build: { outDir: '../src/sentri/dashboard/static', emptyOutDir: true, sourcemap: false },
  server: { proxy: { '/api': 'http://127.0.0.1:8787', '/evidence': 'http://127.0.0.1:8787' } },
})
