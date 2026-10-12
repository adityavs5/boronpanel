import { defineConfig } from '@playwright/test'
export default defineConfig({
  testDir: './e2e',
  // The real-login smoke test owns a separate ephemeral API configuration.
  testIgnore: 'ci-live-smoke.spec.js',
  timeout: 45_000,
  expect: { timeout: 12_000 },
  fullyParallel: false,
  workers: 2,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: { baseURL: 'http://127.0.0.1:4173', viewport: { width: 1440, height: 1000 }, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
  webServer: { command: 'node e2e/serve.mjs', url: 'http://127.0.0.1:4173/app', reuseExistingServer: !process.env.CI },
})
