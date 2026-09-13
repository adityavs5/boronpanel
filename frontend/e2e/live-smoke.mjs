// Read-only smoke test of a deployed Boron panel. Credentials stay outside Git.
import { chromium } from 'playwright'
import { expect } from '@playwright/test'
import fs from 'node:fs'
const creds = JSON.parse(fs.readFileSync(process.env.BORON_CREDENTIALS_FILE, 'utf8'))
const base = process.env.BORON_TEST_URL || 'https://127.0.0.1:9443'
const output = process.env.BORON_SCREENSHOT_DIR || '/tmp/boron-theme-screenshots'
fs.mkdirSync(output, { recursive: true })
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1440, height: 1000 } })
const page = await context.newPage()
const errors = []
page.on('pageerror', (e) => errors.push(e.message))
const missingAssets = []
page.on('response', (r) => { if (r.url().includes('/static/dist/') && r.status() >= 400) missingAssets.push(r.url()) })
try {
  await page.goto(`${base}/app/login`)
  await page.getByLabel('Username', { exact: true }).fill(creds.username)
  await page.locator('input#password').fill(creds.password)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(page).toHaveURL(/\/app\/overview$/, { timeout: 30000 })
  await expect(page.getByRole('heading', { name: 'Admin Dashboard' })).toBeVisible()
  for (const [skin, name] of [['evolution', 'Evolution'], ['paper-lantern', 'Paper Lantern']]) {
    await page.getByRole('button', { name: 'Choose theme', exact: true }).first().click()
    await page.getByRole('menuitemradio', { name: new RegExp(name) }).click()
    await expect(page.locator('html')).toHaveAttribute('data-skin', skin)
    await expect(page.getByRole('progressbar', { name: 'CPU Usage' })).toBeVisible()
    await expect(page.locator('.query-notice')).toHaveCount(0)
    await page.screenshot({ path: `${output}/${skin}-desktop.png` })
    await page.locator('.tool-link').filter({ hasText: 'Manage Accounts' }).click()
    await expect(page.getByRole('heading', { name: 'Accounts', exact: true })).toBeVisible()
    await page.screenshot({ path: `${output}/${skin}-accounts.png` })
    await page.goto(`${base}/app/change-password`)
    await expect(page.getByRole('heading', { name: 'Change password' })).toBeVisible()
    await page.locator('input[type=password]').first().fill('unsaved-verification-only')
    await page.getByRole('button', { name: 'Switch to dark mode', exact: true }).click()
    await expect(page.locator('input[type=password]').first()).toHaveValue('unsaved-verification-only')
    await page.screenshot({ path: `${output}/${skin}-form-dark.png` })
    await page.getByRole('button', { name: 'Switch to light mode', exact: true }).click()
    await page.goto(`${base}/app/appearance`)
    await expect(page.getByRole('button', { name: `Use ${name} theme` })).toHaveAttribute('aria-pressed', 'true')
    await page.screenshot({ path: `${output}/${skin}-appearance.png` })
    await page.reload()
    await expect(page.locator('html')).toHaveAttribute('data-skin', skin)
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(`${base}/app/overview`)
    await expect(page.locator('.tool-link').first()).toBeVisible()
    await page.screenshot({ path: `${output}/${skin}-mobile.png` })
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)
    expect(overflow).toBe(false)
    await page.setViewportSize({ width: 1440, height: 1000 })
    console.log(`${name}: real login, live statistics, accounts, form, appearance, persistence and mobile passed`)
  }
  expect(errors).toEqual([])
  expect(missingAssets).toEqual([])
  console.log('No uncaught browser errors or failed static assets.')
} finally { await browser.close() }
