import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

// Built files are committed into the Python package (huhqueue/static/app) so `pip install` users
// never need Node. Run `npm run build` after changing anything in web/src.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  base: '/static/app/',
  build: { outDir: '../huhqueue/static/app', emptyOutDir: true, chunkSizeWarningLimit: 900 },
  server: { proxy: { '/api': 'http://localhost:8787', '/files': 'http://localhost:8787', '/fake-consent': 'http://localhost:8787' } },
})
