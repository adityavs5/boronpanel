import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import vm from 'node:vm'

const themes = ['classic', 'grid', 'modern']

// theme-init.js falls back to the cached public branding when the page was not
// served by the panel, which is how these tests choose the server's theme.
async function setup(page, role = 'admin', theme = 'modern', { mode = 'light', handle } = {}) {
  await page.addInitScript(({ role, theme, mode }) => {
    if (!sessionStorage.getItem('seeded')) {
      localStorage.setItem('boron.public-branding.v1', JSON.stringify({ panel_name: 'Boron', theme }))
      localStorage.setItem('boron.ui', JSON.stringify({ state: { theme: mode }, version: 0 }))
      sessionStorage.setItem('seeded', '1')
    }
    localStorage.setItem('boron.auth', JSON.stringify({ state: { role, username: role === 'admin' ? 'admin' : 'hostingdemo' }, version: 0 }))
  }, { role, theme, mode })
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.route('**/api/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname
    const handled = await handle?.(route, pathname)
    if (handled === 'done') return undefined
    if (handled) return route.fulfill({ json: handled })
    let data = {}
    if (pathname.endsWith('/whoami')) data = { role, username: role === 'admin' ? 'admin' : 'hostingdemo' }
    else if (pathname === '/api/v1/branding') data = { panel_name: 'Boron', theme }
    else if (pathname === '/api/v1/admin/branding') data = { panel_name: 'Boron', theme }
    else if (pathname.endsWith('/version')) data = { version: '1.0.1' }
    else if (pathname.endsWith('/health')) data = { cpu_pct: 12, mem_pct: 32, disks: [{ mount: '/', pct: 24 }], uptime_seconds: 84600 }
    else if (pathname === '/api/v1/accounts') data = [{ username: 'hostingdemo', primary_domain: 'example.com', status: 'active', php_version: '8.3' }]
    else if (pathname.endsWith('/plans')) data = []
    else if (pathname.endsWith('/onboarding')) data = { completed: true }
    else if (pathname.endsWith('/usage')) data = { current: { disk_total_bytes: 123456789 }, resources: {} }
    else if (pathname.endsWith('/domains')) data = { domains: [{ domain: 'example.com', kind: 'primary', is_primary: true, created_at: '2026-01-01T00:00:00Z' }], parked: [] }
    else if (pathname.endsWith('/alerts')) data = { active: [] }
    else if (pathname === '/api/v1/accounts/hostingdemo') data = { username: 'hostingdemo', primary_domain: 'example.com', status: 'active', php_version: '8.3' }
    await route.fulfill({ json: data })
  })
  return errors
}
const noOverflow = (page) => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth && document.querySelector('main').scrollWidth <= document.querySelector('main').clientWidth + 1)
const home = (role) => role === 'admin' ? '/app/overview' : '/app/dashboard'

for (const role of ['admin', 'customer']) for (const theme of themes) {
  test(`${role} ${theme}: home has its own frame, icons and working tool links`, async ({ page }) => {
    const errors = await setup(page, role, theme)
    await page.goto(home(role))
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    await expect(page.locator('.tool-link').first()).toBeVisible()
    if (theme === 'classic') {
      await expect(page.locator('.persistent-navigation .hosting-sidebar[data-tone=navy]')).toBeVisible()
      await expect(page.locator('main h1')).toHaveText('Tools')
    }
    if (theme === 'grid') {
      await expect(page.locator('.grid-menu')).toBeVisible()
      await expect(page.locator('.persistent-navigation')).toHaveCount(0)
      await expect(page.getByLabel('Navigation filter')).toBeVisible()
    }
    if (theme === 'modern') {
      await expect(page.locator('.persistent-navigation .hosting-sidebar[data-tone=rail]')).toBeVisible()
      await expect(page.locator('.glance-card').first()).toBeVisible()
      await expect(page.getByRole('heading', { name: 'All tools' })).toBeVisible()
    }
    // Each theme draws from its own icon set: lucide stays in Modern only.
    expect(await page.locator('svg.theme-icon').count() > 0).toBe(theme !== 'modern')
    expect(await page.locator('main svg.lucide').count() > 0).toBe(theme === 'modern')
    const link = page.locator('.tool-link[href$="/ssl"]').first()
    await link.click()
    await expect(page).toHaveURL(/\/app\/ssl$/)
    expect(await noOverflow(page)).toBe(true)
    expect(errors).toEqual([])
  })

  test(`${role} ${theme}: phone and tablet layouts reach every tool`, async ({ page }) => {
    await setup(page, role, theme)
    for (const width of [375, 768]) {
      await page.setViewportSize({ width, height: 900 })
      await page.goto(home(role))
      await expect(page.locator('.tool-link').first()).toBeVisible()
      expect(await noOverflow(page)).toBe(true)
      await page.locator('.inner-menu-button').click()
      const drawer = page.locator('#panel-navigation-drawer')
      await expect(drawer.getByRole('navigation', { name: 'Primary navigation' })).toBeVisible()
      await drawer.getByRole('button', { name: 'Close navigation' }).click()
      await expect(drawer).toHaveCount(0)
    }
  })
}

test('internal pages look different in every theme', async ({ browser }) => {
  const seen = {}
  for (const theme of themes) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
    const page = await context.newPage()
    await setup(page, 'customer', theme)
    await page.goto('/app/domains')
    await expect(page.locator('main h1')).toHaveText('Domains')
    await expect(page.locator('main table').first()).toBeVisible()
    seen[theme] = await page.evaluate(() => {
      const css = (selector, property) => { const element = document.querySelector(selector); return element ? getComputedStyle(element)[property] : null }
      return {
        font: css('main h1', 'fontFamily'), title: css('main h1', 'fontWeight') + css('main h1', 'fontSize'),
        button: css('main [data-ui-button=primary]', 'borderRadius') + css('main [data-ui-button=primary]', 'textTransform') + css('main [data-ui-button=primary]', 'backgroundColor'),
        tableHead: css('main thead th', 'backgroundColor') + css('main thead th', 'color'),
        icon: document.querySelector('.page-heading-icon svg')?.innerHTML,
      }
    })
    await context.close()
  }
  for (const key of ['font', 'title', 'button', 'tableHead', 'icon']) {
    expect(new Set(themes.map((theme) => seen[theme][key])).size, key).toBe(3)
  }
})

test('tabs and forms change with the theme', async ({ browser }) => {
  const seen = {}
  for (const theme of themes) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
    const page = await context.newPage()
    await setup(page, 'admin', theme)
    await page.goto('/app/accounts/new')
    const field = page.locator('.form-field').first()
    await expect(field).toBeVisible()
    seen[theme] = await field.evaluate((element) => { const label = getComputedStyle(element.querySelector('label')); return label.fontWeight + label.fontFamily })
    await context.close()
  }
  expect(new Set(Object.values(seen)).size).toBe(3)
})

test('grid: the top menu reaches a tool from any page, and the filter narrows the tiles', async ({ page }) => {
  await setup(page, 'customer', 'grid')
  await page.goto('/app/dashboard')
  await page.getByLabel('Navigation filter').fill('cron')
  await expect(page.locator('.tool-link')).toHaveCount(1)
  await page.getByLabel('Navigation filter').fill('nothing-like-this')
  await expect(page.getByText(/No tools match/)).toBeVisible()
  await page.getByLabel('Navigation filter').fill('')
  await page.goto('/app/domains')
  await page.locator('.grid-menu').getByRole('button', { name: 'Email' }).click()
  await page.getByRole('menuitem', { name: 'Email accounts' }).click()
  await expect(page).toHaveURL(/\/app\/email$/)
  await expect(page.locator('.grid-menu').getByRole('button', { name: 'Email' })).toHaveAttribute('data-current', 'true')
})

test('nobody picks a theme for themselves', async ({ page }) => {
  await setup(page, 'customer', 'classic')
  await page.goto('/app/appearance')
  await expect(page.getByRole('heading', { name: 'Display preferences', exact: true })).toBeVisible()
  await expect(page.getByText('This panel uses the Classic theme, set by the server administrator.')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Change the panel theme' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Choose theme' })).toHaveCount(0)
  await expect(page.getByRole('radio')).toHaveCount(0)
  await page.goto('/app/panel-theme')
  await expect(page.getByRole('radiogroup', { name: 'Panel theme' })).toHaveCount(0)
  await page.goto('/app/dashboard')
  await expect(page.locator('a[href$="/panel-theme"]')).toHaveCount(0)
})

test('a stored theme from the old per-browser setting is ignored', async ({ page }) => {
  await setup(page, 'customer', 'grid')
  await page.addInitScript(() => localStorage.setItem('boron.ui', JSON.stringify({ state: { skin: 'paper-lantern', theme: 'dark' }, version: 0 })))
  await page.goto('/app/dashboard')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'grid')
  await expect(page.locator('html')).not.toHaveClass(/dark/)
})

test('the login page follows the server theme and offers no choice', async ({ page }) => {
  for (const theme of themes) {
    await page.addInitScript((value) => localStorage.setItem('boron.public-branding.v1', JSON.stringify({ panel_name: 'Boron', theme: value })), theme)
    await page.route('**/api/**', (route) => route.fulfill({ status: new URL(route.request().url()).pathname === '/api/v1/branding' ? 200 : 401, json: { panel_name: 'Boron', theme } }))
    await page.goto('/app/login')
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Choose theme' })).toHaveCount(0)
    await page.unrouteAll()
  }
})

test('dark mode is offered only by a theme that has one', async ({ page }) => {
  await setup(page, 'customer', 'modern')
  await page.goto('/app/dashboard')
  await page.getByRole('button', { name: 'Switch to dark mode' }).click()
  await expect(page.locator('html')).toHaveClass(/dark/)
  await page.reload()
  await expect(page.locator('html')).toHaveClass(/dark/)
  const other = await page.context().newPage()
  await setup(other, 'customer', 'classic', { mode: 'dark' })
  await other.goto('/app/dashboard')
  await expect(other.locator('.tool-link').first()).toBeVisible()
  await expect(other.locator('html')).not.toHaveClass(/dark/)
  await expect(other.getByRole('button', { name: /Switch to (dark|light) mode/ })).toHaveCount(0)
})

test('the administrator applies a theme for the whole server', async ({ page }) => {
  let saved = 'modern'
  const puts = []
  await setup(page, 'admin', 'modern', { handle: (route, pathname) => {
    if (pathname === '/api/v1/admin/branding/theme') { saved = route.request().postDataJSON().theme; puts.push(saved); return { theme: saved } }
    if (pathname === '/api/v1/admin/branding' || pathname === '/api/v1/branding') return { panel_name: 'Boron', theme: saved }
    return null
  } })
  await page.goto('/app/panel-theme')
  const options = page.getByRole('radiogroup', { name: 'Panel theme' }).getByRole('radio')
  await expect(options).toHaveCount(3)
  await expect(page.getByRole('radio', { name: /Modern/ })).toHaveAttribute('aria-checked', 'true')
  await expect(page.getByRole('button', { name: 'Apply theme for everyone' })).toBeDisabled()
  await page.getByRole('radio', { name: /Grid/ }).click()
  await page.getByRole('button', { name: 'Apply theme for everyone' }).click()
  await expect.poll(() => puts).toEqual(['grid'])
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'grid')
  await expect(page.locator('.grid-menu')).toBeVisible()
})

test('an open page moves to the new theme when the administrator changes it', async ({ page }) => {
  let server = 'classic'
  await setup(page, 'customer', 'classic', { handle: (_route, pathname) => pathname === '/api/v1/branding' ? { panel_name: 'Boron', theme: server } : null })
  await page.goto('/app/dashboard')
  await expect(page.locator('.persistent-navigation')).toBeVisible()
  server = 'grid'
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'grid')
  await expect(page.locator('.grid-menu')).toBeVisible()
})

test('a cached copy of the old theme does not reload the page', async ({ page }) => {
  await setup(page, 'customer', 'classic', { handle: (_route, pathname) => pathname === '/api/v1/branding' ? { panel_name: 'Boron', theme: 'grid' } : null })
  // The panel names the theme on <html>; the browser still caches the old one.
  await page.route('**/app/dashboard', async (route) => {
    const response = await route.fetch()
    await route.fulfill({ response, body: (await response.text()).replace('<html ', '<html data-theme="grid" ') })
  })
  let loads = 0
  page.on('load', () => { loads += 1 })
  await page.goto('/app/dashboard')
  await expect(page.locator('.grid-menu')).toBeVisible()
  await page.waitForTimeout(1200)
  expect(loads).toBe(1)
})

test('failed statistics offer retry without hiding the tool directory', async ({ page }) => {
  await setup(page, 'admin', 'classic', { handle: async (route, pathname) => {
    if (!pathname.endsWith('/health')) return null
    await route.fulfill({ status: 500, json: { detail: 'down' } })
    return 'done'
  } })
  await page.goto('/app/overview')
  await expect(page.getByRole('button', { name: 'Retry server statistics' })).toBeVisible()
  await expect(page.locator('.tool-link').first()).toBeVisible()
})

test('prepaint loader trusts the server, then the cache, and tolerates bad storage', () => {
  const script = fs.readFileSync('public/theme-init.js', 'utf8')
  const run = ({ attribute, ui, branding }) => {
    let dark
    const applied = []
    const root = { dataset: attribute ? { theme: attribute } : {}, style: {}, classList: { toggle: (_, value) => { dark = value } } }
    const storage = { getItem: (key) => { const value = key === 'boron.ui' ? ui : branding; if (value === null) throw Error('blocked'); return value ?? null } }
    vm.runInNewContext(script, { window: { BoronAppearance: { apply: (...args) => applied.push(args) } }, document: { documentElement: root }, localStorage: storage })
    return { theme: root.dataset.theme, dark, scheme: root.style.colorScheme, applied }
  }
  const dark = JSON.stringify({ state: { theme: 'dark' } })
  expect(run({ attribute: 'grid', branding: JSON.stringify({ theme: 'classic' }) }).theme).toBe('grid')
  expect(run({ branding: JSON.stringify({ theme: 'classic' }) }).theme).toBe('classic')
  expect(run({ branding: '{bad', ui: '{bad' }).theme).toBe('modern')
  expect(run({ branding: null, ui: null }).theme).toBe('modern')
  expect(run({ attribute: 'evolution' }).theme).toBe('modern')
  expect(run({ attribute: 'modern', ui: dark })).toMatchObject({ dark: true, scheme: 'dark' })
  expect(run({ attribute: 'classic', ui: dark })).toMatchObject({ dark: false, scheme: 'light' })
  expect(run({ attribute: 'grid', ui: JSON.stringify({ state: { appearance: { textSize: 'large' } } }) }).applied).toEqual([[{ textSize: 'large' }, 'grid', false]])
  const bare = { dataset: {}, style: {}, classList: { toggle: () => {} } }
  vm.runInNewContext(script, { window: {}, document: { documentElement: bare }, localStorage: { getItem: () => null } })
  expect(bare.dataset.theme).toBe('modern')
})
