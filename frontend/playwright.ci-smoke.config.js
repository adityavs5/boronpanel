import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  testMatch: 'ci-live-smoke.spec.js',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  workers: 1,
  retries: 1,
  reporter: [['list'], ['html', { outputFolder: 'playwright-report-ci', open: 'never' }]],
  use: {
    baseURL: process.env.BORON_CI_BASE_URL || 'https://127.0.0.1:9443',
    ignoreHTTPSErrors: true,
    viewport: { width: 1440, height: 1000 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
})
