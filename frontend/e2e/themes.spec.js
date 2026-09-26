import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import vm from 'node:vm'
const skins = ['evolution', 'paper-lantern']
const nameOf = (skin) => skin === 'evolution' ? 'Evo' : 'Paper'

async function setup(page, role = 'admin', skin = 'evolution', mode = 'light') {
  await page.addInitScript(({ role, skin, mode }) => {
    if (!localStorage.getItem('boron.ui')) localStorage.setItem('boron.ui', JSON.stringify({ state: { skin, theme: mode }, version: 0 }))
    localStorage.setItem('boron.auth', JSON.stringify({ state: { role, username: role === 'admin' ? 'admin' : 'hostingdemo' }, version: 0 }))
  }, { role, skin, mode })
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.route('**/api/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname
    let data = {}
    if (pathname.endsWith('/whoami')) data = { role, username: role === 'admin' ? 'admin' : 'hostingdemo' }
    else if (pathname.endsWith('/branding')) data = { panel_name: 'Boron' }
    else if (pathname.endsWith('/version')) data = { version: '1.0.1' }
    else if (pathname.endsWith('/health')) data = { cpu_pct: 12, mem_pct: 32, disks: [{ mount: '/', pct: 24 }], uptime_seconds: 84600 }
    else if (pathname === '/api/v1/accounts') data = [{ username: 'hostingdemo', primary_domain: 'example.com', status: 'active', php_version: '8.3' }]
    else if (pathname.endsWith('/plans')) data = []
    else if (pathname.endsWith('/onboarding')) data = { completed: true }
    else if (pathname.endsWith('/usage')) data = { current: { disk_total_bytes: 123456789, disk_home_bytes: 100000000, disk_db_bytes: 13456789, disk_mail_bytes: 10000000, inode_count: 4300 }, quota_hard_mb: 5120, bandwidth_month_to_date_bytes: 987654321, resources: { sampled_at: '2026-09-15T12:00:00Z', cpu_usage_usec: 5000000, cpu_limit_cores: 2, memory_current_bytes: 268435456, memory_limit_bytes: 1073741824, io_limit_bytes_per_second: 52428800, read_bytes: 1000, write_bytes: 2000, read_ops: 4, write_ops: 8, domain_count: 2, subdomain_count: 3, subdomain_limit: 10, database_count: 4, database_limit: 10, email_account_count: 8, email_account_limit: 25, ftp_account_count: 2, ftp_account_limit: 5, bandwidth_limit_bytes: 107374182400 } }
    else if (pathname.endsWith('/domains')) data = { domains: [{ domain: 'example.com', is_primary: true }] }
    else if (pathname.endsWith('/alerts')) data = { active: [] }
    else if (pathname === '/api/v1/accounts/hostingdemo') data = { username: 'hostingdemo', primary_domain: 'example.com', status: 'active', php_version: '8.3', quota_hard_mb: 5120 }
    await route.fulfill({ json: data })
  })
  return errors
}
async function choose(page, skin) {
  await page.getByRole('button', { name: 'Choose theme', exact: true }).first().click()
  await page.getByRole('menuitemradio', { name: new RegExp(nameOf(skin)) }).click()
  await expect(page.locator('html')).toHaveAttribute('data-skin', skin)
}
async function noOverflow(page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth || document.querySelector('main').scrollWidth > document.querySelector('main').clientWidth)
  expect(overflow).toBe(false)
}
for (const role of ['admin', 'customer']) {
  for (const skin of skins) {
    test(`${role} ${skin}: dashboard, real tool links, search and collapse`, async ({ page }, testInfo) => {
      const errors = await setup(page, role, skin)
      await page.goto(role === 'admin' ? '/app/overview' : '/app/dashboard')
      await expect(page.getByRole('heading', { name: role === 'admin' ? 'Admin Dashboard' : 'Hosting Dashboard', exact: true })).toBeVisible()
      await expect(page.locator('.tool-link').first()).toBeVisible()
      await expect(page.locator('.query-notice')).toHaveCount(0)
      if (role === 'customer') {
        await expect(page.locator('.usage-row').filter({ hasText: 'Memory' })).toContainText('256.0 MB')
        await expect(page.locator('.usage-row').filter({ hasText: 'Subdomains' })).toContainText('3 / 10')
        await expect(page.locator('.usage-row').filter({ hasText: 'Email Accounts' })).toContainText('8 / 25')
        const toolCounts = await page.locator('.tool-grid').evaluateAll(grids => grids.map(grid => grid.querySelectorAll('.tool-link').length))
        expect(toolCounts).toEqual([6, 6, 6, 6, 6, 6, 6])
      }
      if (skin === 'evolution') {
        const iconSizes = await page.locator('.tool-icon').evaluateAll(items => items.map(item => {
          const child = item.querySelector(':scope > svg, :scope > img')
          const outer = item.getBoundingClientRect()
          const inner = child?.getBoundingClientRect()
          return [outer.width, outer.height, inner?.width, inner?.height]
        }))
        expect(new Set(iconSizes.map(size => size.join('x')))).toEqual(new Set(['48x48x44x44']))
        const gridRows = await page.locator('.tool-grid').evaluateAll(grids => grids.map(grid => {
          const left = grid.getBoundingClientRect().left
          const rows = new Map()
          for (const item of grid.querySelectorAll('.tool-link')) {
            const rect = item.getBoundingClientRect()
            const key = Math.round(rect.top)
            if (!rows.has(key)) rows.set(key, [])
            rows.get(key).push(Math.round(rect.left - left))
          }
          return [...rows.values()]
        }))
        const fullRows = gridRows.flat().filter(row => row.length === 6)
        expect(fullRows.length).toBeGreaterThan(0)
        expect(new Set(fullRows.map(row => row.join(','))).size).toBe(1)
        expect(Math.max(...gridRows.flat().map(row => row.length))).toBe(6)
        if (role === 'customer') {
          const heightGap = await page.evaluate(() => {
            const tools = document.querySelector('.tools-column').getBoundingClientRect()
            const stats = document.querySelector('.dashboard-stats').getBoundingClientRect()
            return Math.abs(tools.height - stats.height)
          })
          expect(heightGap).toBeLessThan(24)
        }
      }
      await noOverflow(page)
      const links = await page.locator('.tool-link').evaluateAll((items) => items.map((item) => item.getAttribute('href')))
      expect(links).toContain('/app/appearance')
      expect(links).toContain(role === 'admin' ? '/app/accounts' : '/app/files')
      if (role === 'customer') expect(links).not.toContain('/app/accounts')
      await page.screenshot({ path: testInfo.outputPath(`${role}-${skin}.png`), fullPage: true })
      const section = page.locator('.tool-group').first()
      await section.locator('button').click()
      await expect(section.locator('.tool-grid')).toBeHidden()
      await page.reload()
      await expect(page.locator('.tool-group').first().locator('.tool-grid')).toBeHidden()
      const search = page.getByRole('textbox', { name: 'Search hosting tools' })
      await search.fill(role === 'admin' ? 'accounts' : 'files')
      await expect(page.getByRole('option').first()).toBeVisible()
      await search.fill('no-such-tool-xyz')
      await expect(page.getByText('No matching tool.')).toBeVisible()
      await page.getByRole('button', { name: 'Clear search' }).click()
      await expect(search).toHaveValue('')
      expect(errors).toEqual([])
    })
    test(`${role} ${skin}: phone and tablet layouts`, async ({ page }, testInfo) => {
      const errors = await setup(page, role, skin)
      for (const width of [320, 390, 768]) {
        await page.setViewportSize({ width, height: 844 })
        await page.goto(role === 'admin' ? '/app/overview' : '/app/dashboard')
        await expect(page.locator('.tool-link').first()).toBeVisible()
        await noOverflow(page)
        if (width === 390) await page.screenshot({ path: testInfo.outputPath(`${role}-${skin}-mobile.png`), fullPage: true })
      }
      await expect(page.getByRole('button', { name: 'Open navigation' })).toHaveCount(0)
      await page.getByRole('textbox', { name: 'Search hosting tools' }).fill('change style')
      await page.getByRole('option').filter({ hasText: 'Appearance' }).click()
      await expect(page).toHaveURL(/\/appearance$/)
      expect(errors).toEqual([])
    })
  }
}
test('theme and color mode persist without losing a password form draft', async ({ page }) => {
  const errors = await setup(page)
  await page.goto('/app/change-password')
  const input = page.locator('input[type="password"]').first()
  await input.fill('unsaved-test-value')
  await choose(page, 'paper-lantern')
  await expect(page).toHaveURL(/\/change-password$/)
  await expect(input).toHaveValue('unsaved-test-value')
  await page.getByRole('button', { name: 'Switch to dark mode', exact: true }).click()
  await expect(page.locator('html')).toHaveClass(/dark/)
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-skin', 'paper-lantern')
  await expect(page.locator('html')).toHaveClass(/dark/)
  expect(errors).toEqual([])
})
for (const skin of skins) {
  test(`${skin}: appearance previews, switching, and dark surfaces`, async ({ page }, testInfo) => {
    const errors = await setup(page, 'admin', skin)
    await page.goto('/app/appearance')
    await expect(page.getByRole('button', { name: `Use ${nameOf(skin)} theme` })).toHaveAttribute('aria-pressed', 'true')
    await page.getByRole('button', { name: 'Dark', exact: true }).click()
    await page.getByRole('link', { name: 'Back to dashboard' }).click()
    await expect(page.locator('.tool-link').first()).toBeVisible()
    await expect(page.locator('html')).toHaveClass(/dark/)
    expect(await page.locator('main').evaluate((el) => getComputedStyle(el).backgroundColor)).not.toBe('rgb(255, 255, 255)')
    await page.screenshot({ path: testInfo.outputPath(`${skin}-dark.png`), fullPage: true })
    expect(errors).toEqual([])
  })
}
test('failed statistics offer retry without hiding the tool directory', async ({ page }) => {
  await setup(page)
  await page.route('**/api/v1/health', (route) => route.fulfill({ status: 500, json: { detail: 'Unavailable' } }))
  await page.goto('/app/overview')
  await expect(page.getByRole('button', { name: 'Retry server statistics' })).toBeVisible()
  await expect(page.locator('.tool-link').first()).toBeVisible()
  await page.route('**/api/v1/health', (route) => route.fulfill({ json: { cpu_pct: 0, mem_pct: 0, disks: [] } }))
  await page.getByRole('button', { name: 'Retry server statistics' }).click()
  await expect(page.getByRole('progressbar', { name: 'CPU Usage' })).toHaveAttribute('aria-valuenow', '0')
})
test('preferences synchronize across tabs without navigation', async ({ page, context }) => {
  await setup(page)
  await page.goto('/app/overview')
  const second = await context.newPage()
  await setup(second)
  await second.goto('/app/appearance')
  await choose(page, 'paper-lantern')
  await expect(second.locator('html')).toHaveAttribute('data-skin', 'paper-lantern')
  await expect(second).toHaveURL(/\/appearance$/)
})
test('prepaint preference loader tolerates old, invalid and unavailable storage', () => {
  const script = fs.readFileSync('public/theme-init.js', 'utf8')
  for (const [saved, expectedSkin, expectedDark] of [
    ['{}', 'evolution', false], ['{bad', 'evolution', false],
    [JSON.stringify({ state: { theme: 'dark' } }), 'evolution', true],
    [JSON.stringify({ state: { skin: 'paper-lantern', theme: 'dark' } }), 'paper-lantern', true],
    [JSON.stringify({ state: { skin: 'unknown', theme: 'invalid' } }), 'evolution', false],
    [null, 'evolution', false],
  ]) {
    let dark
    const root = { dataset: {}, style: {}, classList: { toggle: (_, value) => { dark = value } } }
    vm.runInNewContext(script, { document: { documentElement: root }, localStorage: { getItem: () => { if (saved === null) throw Error('blocked'); return saved } } })
    expect(root.dataset.skin).toBe(expectedSkin)
    expect(dark).toBe(expectedDark)
  }
})

test('keyboard navigation can switch themes and reach Appearance', async ({ page }) => {
  await setup(page)
  await page.goto('/app/overview')
  await page.getByRole('button', { name: 'Choose theme', exact: true }).first().focus()
  await page.keyboard.press('Enter')
  const paperTheme = page.getByRole('menuitemradio', { name: /Paper/ })
  await expect(paperTheme).toBeVisible()
  await page.keyboard.press('p')
  await expect(paperTheme).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.locator('html')).toHaveAttribute('data-skin', 'paper-lantern')
  const search = page.getByRole('textbox', { name: 'Search hosting tools' })
  await search.focus()
  await search.fill('Appearance')
  await expect(page.getByRole('option').filter({ hasText: 'Appearance' })).toBeVisible()
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/appearance$/)
})
