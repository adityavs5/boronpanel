import { test, expect } from '@playwright/test'

for (const skin of ['evolution', 'paper-lantern']) for (const theme of ['light', 'dark']) {
  test(`${skin} ${theme}: direct app, FTP and Git management`, async ({ page }, info) => {
    // This scenario visits eight desktop/mobile pages and exercises service
    // actions; its total budget must cover all of those independent checks.
    test.setTimeout(120_000)
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.addInitScript(({ skin, theme }) => {
      localStorage.setItem('boron.ui', JSON.stringify({ state: { skin, theme }, version: 0 }))
      localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'customer', username: 'alpha' }, version: 0 }))
    }, { skin, theme })
    let active = false, failRestart = true
    const writes = []
    await page.route('**/api/**', async route => {
      const req = route.request(), p = new URL(req.url()).pathname
      let data = {}
      if (req.method() !== 'GET') writes.push({ path: p, method: req.method(), body: req.postData() })
      if (p.endsWith('/whoami')) data = { role: 'customer', username: 'alpha' }
      else if (p.endsWith('/onboarding')) data = { completed: true }
      else if (/\/apps\/(node|python)$/.test(p)) data = { apps: [{ id: 7, name: 'my-api', active, domain: 'api.example.com', port: 3001, entry_point: 'server.js' }] }
      else if (p.endsWith('/7/start')) active = true
      else if (p.endsWith('/7/stop')) active = false
      else if (p.endsWith('/7/restart') && failRestart) {
        failRestart = false
        await route.fulfill({ status: 500, json: { detail: 'Service could not restart' } })
        return
      }
      else if (p.endsWith('/7/logs')) data = { log_lines: ['Application ready on port 3001'] }
      else if (p.endsWith('/domains')) data = { domains: [{ domain: 'example.com' }] }
      else if (p.endsWith('/records')) data = { zone: 'example.com', records: [{ name: 'www.example.com.', type: 'A', ttl: 3600, values: ['192.0.2.1'] }] }
      else if (p.endsWith('/crons')) data = { jobs: [{ id: 4, label: 'Daily cleanup', schedule: '0 0 * * *', command: 'php cleanup.php' }] }
      else if (p.endsWith('/ftp')) data = { ftp_accounts: [{ id: 3, ftp_login: 'alpha_designer', label: 'designer', path: '/home/alpha/public_html' }] }
      else if (p.endsWith('/git')) data = { repos: [{ name: 'website', deploy_target: '/home/alpha/public_html' }] }
      else if (p.endsWith('/git/website/log')) data = { lines: ['Deploy succeeded'] }
      await route.fulfill({ json: data })
    })

    for (const type of ['node', 'python']) {
      await page.goto(`/app/${type}-apps`)
      const name = page.getByRole('button', { name: 'Manage application my-api', exact: true })
      await name.focus()
      await page.keyboard.press('Enter')
      let dialog = page.getByRole('dialog')
      await expect(dialog.getByRole('heading', { name: 'Manage my-api' })).toBeVisible()
      await dialog.getByRole('button', { name: 'Start', exact: true }).click()
      await expect(dialog.getByRole('button', { name: 'Stop', exact: true })).toBeVisible()
      if (type === 'node') {
        await dialog.getByRole('button', { name: 'Restart', exact: true }).click()
        await expect(page.getByText('Service could not restart', { exact: true })).toBeVisible()
        await expect(dialog.getByRole('button', { name: 'Stop', exact: true })).toBeEnabled()
      }
      await dialog.getByRole('button', { name: 'Stop', exact: true }).click()
      await expect(dialog.getByRole('button', { name: 'Start', exact: true })).toBeVisible()
      await dialog.getByRole('button', { name: 'View logs', exact: true }).click()
      await expect(page.getByRole('dialog').getByText('Application ready on port 3001')).toBeVisible()
      await page.keyboard.press('Escape')
      await page.getByRole('button', { name: 'Manage', exact: true }).click()
      await page.getByRole('dialog').getByRole('button', { name: 'Delete app', exact: true }).click()
      await expect(page.getByRole('heading', { name: 'Delete my-api?' })).toBeVisible()
      expect(writes.some(w => w.method === 'DELETE')).toBe(false)
      await page.getByRole('button', { name: 'Cancel', exact: true }).click()
    }

    await page.goto('/app/ftp')
    await page.getByRole('button', { name: 'Manage FTP account alpha_designer', exact: true }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Change directory' }).click()
    await expect(page.getByRole('heading', { name: 'Change path', exact: true })).toBeVisible()
    await expect(page.getByRole('textbox', { name: 'Path', exact: true })).toHaveValue('/home/alpha/public_html')
    await page.keyboard.press('Escape')
    await page.getByRole('button', { name: 'Manage', exact: true }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Change password', exact: true }).click()
    await expect(page.getByRole('dialog').locator('input[type=password]')).toHaveValue('')
    await page.keyboard.press('Escape')

    await page.goto('/app/git')
    await page.getByRole('button', { name: 'Manage repository website', exact: true }).click()
    await expect(page.getByRole('dialog').getByText(/ssh:\/\/alpha@/)).toBeVisible()
    await page.getByRole('dialog').getByRole('button', { name: 'Set deploy target', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Set deploy target', exact: true })).toBeVisible()
    await page.keyboard.press('Escape')
    await page.setViewportSize({ width: 390, height: 844 })
    for (const [path, name] of [['node-apps', 'application my-api'], ['python-apps', 'application my-api'], ['ftp', 'FTP account alpha_designer'], ['git', 'repository website']]) {
      await page.goto(`/app/${path}`)
      await page.getByRole('button', { name: `Manage ${name}`, exact: true }).click()
      const dialog = page.getByRole('dialog')
      await expect(dialog).toBeVisible()
      expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      await page.screenshot({ path: info.outputPath(`${path}-mobile.png`) })
      await page.keyboard.press('Escape')
    }
    for (const [path, button, heading] of [['cron', 'Edit cron job Daily cleanup', 'Edit cron job'], ['dns', 'Edit A record www', 'Edit DNS record']]) {
      await page.goto(`/app/${path}`)
      await page.getByRole('button', { name: button, exact: true }).click()
      await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible()
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      await page.keyboard.press('Escape')
    }
    expect(writes.filter(w => w.method === 'DELETE')).toHaveLength(0)
    expect(writes.filter(w => w.path.endsWith('/start'))).toHaveLength(2)
    expect(writes.filter(w => w.path.endsWith('/stop'))).toHaveLength(2)
    expect(errors).toEqual([])
  })
}
