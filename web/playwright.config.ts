import { defineConfig } from '@playwright/test'

const port = 5193

export default defineConfig({
  testDir: './e2e',
  timeout: 15000,
  expect: { timeout: 3000 },
  fullyParallel: true,
  workers: 2,
  reporter: 'list',
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: `npm run dev -- --port ${port}`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    timeout: 15000,
  },
})
