import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

function port(name: string, fallback: number): number {
  const value = process.env[name] ?? String(fallback)
  if (!/^\d+$/.test(value) || Number(value) < 1024 || Number(value) > 65535) {
    throw new Error(`${name} must be an integer between 1024 and 65535`)
  }
  return Number(value)
}

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: port('WEB_PORT', 5180),
    strictPort: true,
    proxy: {
      '/api': { target: `http://127.0.0.1:${port('BACKEND_PORT', 9010)}`, ws: true },
    },
  },
})
