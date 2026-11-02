import { test, expect } from '@playwright/test'

// Round 2 customer follow-ups: databases, Redis, email, SSH keys and file
// permissions, against the API shapes in api/routers and the daemon.
const combos = [['evolution', 'light'], ['paper-lantern', 'dark']]
const fits = (page) => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)

async function customer(page, { skin, mode }, handler, username = 'alpha') {
  await page.addInitScript(({ skin, mode, username }) => {
    localStorage.setItem('boron.ui', JSON.stringify({ state: { skin, theme: mode }, version: 0 }))
    localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'customer', username }, version: 0 }))
  }, { skin, mode, username })
  const calls = []
  await page.route('**/api/**', async (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method()
    let body = null
    try { body = request.postDataJSON() } catch { body = null }
    calls.push({ method, path, search: url.search, body })
    if (path.endsWith('/whoami')) return route.fulfill({ json: { role: 'customer', username } })
    if (path.endsWith('/onboarding')) return route.fulfill({ json: { completed: true } })
    const answer = await handler({ method, path, url, body })
    if (typeof answer?.status === 'number') return route.fulfill({ status: answer.status, json: answer.json ?? {}, headers: answer.headers })
    return route.fulfill({ json: answer ?? {} })
  })
  return calls
}

for (const [skin, mode] of combos) {
  test(`${skin} ${mode}: phpMyAdmin and remote database hosts follow the server state`, async ({ page }) => {
    const calls = await customer(page, { skin, mode }, ({ method, path }) => {
      if (path.endsWith('/databases/users') && method === 'POST') return { status: 409, json: { detail: "database user 'alpha_reporter' already exists" } }
      if (path.endsWith('/databases/users')) return { users: [], remote_access: { available: false, bind_address: '127.0.0.1', port: 3306 } }
      if (path.endsWith('/databases')) return { databases: [{ db_name: 'alpha_shop', db_user: 'alpha_shop', grants: [] }], pma_available: false }
      return {}
    })
    await page.goto('/app/databases')
    await page.getByRole('button', { name: 'Actions for alpha_shop' }).click()
    await expect(page.getByRole('menuitem', { name: 'Reset password' })).toBeVisible()
    await expect(page.getByRole('menuitem', { name: 'phpMyAdmin' })).toHaveCount(0)
    await page.keyboard.press('Escape')
    await page.getByRole('button', { name: 'Manage database alpha_shop', exact: true }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByRole('button', { name: 'Open phpMyAdmin' })).toBeDisabled()
    await expect(dialog.getByText('phpMyAdmin is not set up on this server.', { exact: false })).toBeVisible()
    await dialog.getByRole('button', { name: 'Done' }).click()

    await page.getByRole('tab', { name: 'Users' }).click()
    const host = page.getByLabel('Allowed host')
    await expect(host).toBeDisabled()
    await expect(host).toHaveValue('localhost')
    await expect(page.getByText('Remote access is off: MariaDB accepts local connections only. An administrator must enable MariaDB network listening and open port 3306.')).toBeVisible()
    await page.getByLabel(/^New user/).fill('reporter')
    await page.getByRole('button', { name: 'Create user' }).click()
    await expect(page.getByRole('alert').filter({ hasText: "database user 'alpha_reporter' already exists" })).toBeVisible()
    expect(calls.find((call) => call.method === 'POST' && call.path.endsWith('/databases/users')).body).toEqual({ name: 'reporter', host: 'localhost' })
    await page.setViewportSize({ width: 375, height: 812 })
    expect(await fits(page)).toBe(true)
  })

  test(`${skin} ${mode}: an unpublished sign-on and an open listener are explained`, async ({ page }) => {
    let tokens = 0
    const calls = await customer(page, { skin, mode }, ({ method, path }) => {
      if (path.endsWith('/pma-token')) { tokens++; return { token: null, db_name: 'alpha_shop', pma_url: null, available: false, expires_in_seconds: 0 } }
      if (path.endsWith('/databases/users') && method === 'POST') return { db_user: 'alpha_remote', host: '203.0.113.5', password: 'generated-test-value' }
      if (path.endsWith('/databases/users')) return { users: [], remote_access: { available: true, bind_address: '0.0.0.0', port: 3306 } }
      if (path.endsWith('/databases')) return { databases: [{ db_name: 'alpha_shop', db_user: 'alpha_shop', grants: [] }] }
      return {}
    })
    await page.goto('/app/databases')
    await page.getByRole('button', { name: 'Manage database alpha_shop', exact: true }).click()
    const dialog = page.getByRole('dialog')
    const open = dialog.getByRole('button', { name: 'Open phpMyAdmin' })
    await expect(open).toBeEnabled()
    await open.click()
    await expect(page.getByText('phpMyAdmin is not set up on this server', { exact: true })).toBeVisible()
    await expect(open).toBeDisabled()
    expect(tokens).toBe(1)
    await dialog.getByRole('button', { name: 'Done' }).click()

    await page.getByRole('tab', { name: 'Users' }).click()
    await expect(page.getByText(/Remote clients also need port 3306 open in the server firewall/)).toBeVisible()
    await page.getByLabel(/^New user/).fill('remote')
    await page.getByLabel('Allowed host').fill('203.0.113.5')
    await page.getByRole('button', { name: 'Create user' }).click()
    await expect(page.getByRole('dialog').getByText('alpha_remote', { exact: true })).toBeVisible()
    expect(calls.find((call) => call.method === 'POST' && call.path.endsWith('/databases/users')).body).toEqual({ name: 'remote', host: '203.0.113.5' })
  })

  test(`${skin} ${mode}: SQL import failures keep the server's reason in view`, async ({ page }) => {
    let imports = 0
    await customer(page, { skin, mode }, ({ path }) => {
      if (path.endsWith('/import')) {
        imports++
        return imports === 1
          ? { status: 400, json: { detail: 'SQL import failed; the database was restored to its previous contents. MariaDB reported: ERROR 1064 (42000) at line 3: You have an error in your SQL syntax' } }
          : { status: 409, json: { detail: 'Earlier SQL imports for this account left recovery copies that need administrator review. Ask an administrator to restore or release them before importing again' } }
      }
      if (path.endsWith('/databases/users')) return { users: [], remote_access: { available: null } }
      if (path.endsWith('/databases')) return { databases: [{ db_name: 'alpha_shop', db_user: 'alpha_shop', grants: [] }], pma_available: true }
      return {}
    })
    await page.goto('/app/databases')
    await page.getByRole('button', { name: 'Manage database alpha_shop', exact: true }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByRole('tab', { name: 'Import / export' }).click()
    await expect(dialog.getByText('The file is applied to the database; tables it does not mention are kept. If the import fails, the database is restored automatically.', { exact: false })).toBeVisible()
    await expect(dialog.getByText(/recovery copy was retained|pre-import recovery copy/)).toHaveCount(0)
    const file = { name: 'dump.sql', mimeType: 'application/sql', buffer: Buffer.from('CREATE TABLE t (id INT);') }
    await dialog.locator('input[type=file]').setInputFiles(file)
    await expect(dialog.getByRole('alert')).toContainText('MariaDB reported: ERROR 1064 (42000) at line 3')
    await dialog.locator('input[type=file]').setInputFiles(file)
    await expect(dialog.getByRole('alert')).toContainText('need administrator review')
    await page.setViewportSize({ width: 375, height: 812 })
    expect(await fits(page)).toBe(true)
  })

  test(`${skin} ${mode}: Redis connection details are requested only while it runs`, async ({ page }) => {
    let enabled = false, connections = 0
    await customer(page, { skin, mode }, ({ method, path }) => {
      if (path.endsWith('/redis/connection-info')) { connections++; return { socket_path: '/home/alpha/.redis/redis.sock', predis_parameters: { scheme: 'unix', path: '/home/alpha/.redis/redis.sock' } } }
      if (path.endsWith('/redis') && method === 'POST') { enabled = true; return { username: 'alpha', enabled: true, active: 'active', mem_mb: 256, socket_path: '/home/alpha/.redis/redis.sock' } }
      if (path.endsWith('/redis')) return enabled
        ? { username: 'alpha', provisioned: true, enabled: true, active: 'active', mem_mb: 256, socket_path: '/home/alpha/.redis/redis.sock', used_memory_human: '1M' }
        : { username: 'alpha', provisioned: true, enabled: false, active: 'inactive', mem_mb: 256, socket_path: '/home/alpha/.redis/redis.sock' }
      return {}
    })
    await page.goto('/app/redis')
    await expect(page.getByText('Redis is disabled.', { exact: false })).toBeVisible()
    await expect(page.getByRole('button', { name: 'More' })).toHaveCount(0)
    await page.getByRole('button', { name: 'Enable Redis' }).first().click()
    await page.getByRole('button', { name: 'More' }).click()
    await page.getByRole('menuitem', { name: 'Connection info' }).click()
    await expect(page.getByRole('dialog').getByText('/home/alpha/.redis/redis.sock').first()).toBeVisible()
    expect(connections).toBe(1)
  })

  test(`${skin} ${mode}: autoresponders can be read, set and removed per mailbox`, async ({ page }, info) => {
    const current = { info: { subject: 'Away this week', body: 'I am back on Monday.', start_date: '2026-11-01', end_date: '2026-11-10', active: 1 }, sales: null }
    let saves = 0
    const calls = await customer(page, { skin, mode }, ({ method, path, body }) => {
      const autoresponder = path.match(/\/email\/autoresponders(?:\/([^/]+))?$/)
      if (autoresponder && method === 'GET') return { domain: 'example.com', local_part: autoresponder[1], autoresponder: current[autoresponder[1]] }
      if (autoresponder && method === 'POST') {
        saves++
        if (saves === 1) return { status: 400, json: { detail: "'2026-02-30' is not a valid calendar date" } }
        current[body.local_part] = { ...body, active: 1 }
        return { domain: 'example.com', ...body }
      }
      if (autoresponder && method === 'DELETE') { current[autoresponder[1]] = null; return { status: 'deleted' } }
      if (path.endsWith('/mailboxes')) return { mailboxes: [{ local_part: 'info', quota_mb: 1024, active: true }, { local_part: 'sales', quota_mb: 1024, active: true }] }
      if (path.endsWith('/domains')) return { domains: [{ domain: 'example.com', kind: 'primary' }] }
      return {}
    })
    await page.goto('/app/email/settings?tab=autoresponders')
    await expect(page.getByRole('button', { name: /Autoresponders/ })).toHaveAttribute('aria-pressed', 'true')
    await expect(page.getByText('Replies with “Away this week”. From 2026-11-01 until 2026-11-10.')).toBeVisible()
    await expect(page.getByLabel(/^Subject/)).toHaveValue('Away this week')
    await expect(page.getByLabel('Start date (optional)')).toHaveValue('2026-11-01')
    await page.getByLabel(/^Message/).fill('I am back on Tuesday.')
    await page.getByRole('button', { name: 'Save changes' }).click()
    await expect(page.getByRole('alert').filter({ hasText: "'2026-02-30' is not a valid calendar date" })).toBeVisible()
    await page.getByRole('button', { name: 'Save changes' }).click()
    await expect(page.getByText('Replies with “Away this week”.', { exact: false })).toBeVisible()
    expect(calls.filter((call) => call.method === 'POST').at(-1).body).toEqual({ local_part: 'info', subject: 'Away this week', body: 'I am back on Tuesday.', start_date: '2026-11-01', end_date: '2026-11-10' })

    await page.getByLabel('End date (optional)').fill('2026-10-01')
    await expect(page.getByText('The end date must be on or after the start date.')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Save changes' })).toBeDisabled()
    await page.getByLabel('End date (optional)').fill('')

    await page.getByRole('button', { name: 'Remove autoresponder' }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Remove autoresponder' }).click()
    await expect(page.getByText('No autoresponder is set for this mailbox.')).toBeVisible()
    expect(calls.some((call) => call.method === 'DELETE' && call.path === '/api/v1/accounts/alpha/domains/example.com/email/autoresponders/info')).toBe(true)

    await page.getByLabel('Mailbox', { exact: true }).selectOption('sales')
    await expect(page.getByRole('button', { name: 'Turn on autoresponder' })).toBeDisabled()
    expect(calls.some((call) => call.method === 'GET' && call.path.endsWith('/email/autoresponders/sales'))).toBe(true)
    await page.screenshot({ path: info.outputPath(`${skin}-${mode}-autoresponders.png`), fullPage: true })
    await page.setViewportSize({ width: 375, height: 812 })
    expect(await fits(page)).toBe(true)
  })

  test(`${skin} ${mode}: forwarders explain local delivery and show refusals`, async ({ page }) => {
    await customer(page, { skin, mode }, ({ method, path }) => {
      if (path.endsWith('/email/forwarders') && method === 'POST') return { status: 409, json: { detail: "forwarder 'sales@example.com' -> 'boss@example.org' already exists" } }
      if (path.endsWith('/email/forwarders')) return { forwards: [{ local_part: 'sales', destination: 'boss@example.org' }] }
      if (path.endsWith('/domains')) return { domains: [{ domain: 'example.com', kind: 'primary' }] }
      return {}
    })
    await page.goto('/app/email/settings')
    await expect(page.getByText('When the address is also a mailbox, mail is delivered to both', { exact: false })).toBeVisible()
    await page.setViewportSize({ width: 375, height: 812 })
    expect(await fits(page)).toBe(true)
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.getByRole('button', { name: 'Add forwarder' }).first().click()
    const dialog = page.getByRole('dialog')
    await dialog.getByPlaceholder('sales').fill('sales')
    await dialog.getByPlaceholder('you@example.com').fill('boss@example.org')
    await dialog.getByRole('button', { name: 'Add forwarder' }).click()
    await expect(dialog.getByRole('alert')).toContainText("forwarder 'sales@example.com' -> 'boss@example.org' already exists")
  })

  test(`${skin} ${mode}: a missing DMARC record explains why none was published`, async ({ page }, info) => {
    await customer(page, { skin, mode }, ({ path }) => {
      if (path.endsWith('/dns-readiness')) return {
        domain: 'shop.example.com', managed: true, provider: 'local', mail_hostname: 'mail.example.com',
        records: [
          { key: 'mx', name: '@', type: 'MX', expected: ['10 mail.example.com.'], actual: ['10 mail.example.com.'], status: 'present' },
          { key: 'dmarc', name: '_dmarc', type: 'TXT', expected: [], actual: [], status: 'optional' },
        ],
      }
      if (path.endsWith('/records')) return { zone: 'shop.example.com', managed: true, provider: 'local', records: [{ name: 'shop.example.com.', type: 'MX', ttl: 3600, values: ['10 mail.example.com.'] }] }
      if (path.endsWith('/domains')) return { domains: [{ domain: 'shop.example.com', kind: 'primary' }] }
      return {}
    })
    await page.goto('/app/email/dns')
    await expect(page.getByText('unless this domain or a parent domain already has a DMARC policy, or public DNS could not be checked', { exact: false })).toBeVisible()
    await page.getByRole('button', { name: 'Set a DMARC policy' }).click()
    await expect(page.getByRole('combobox', { name: 'Policy', exact: true })).toBeFocused()
    await page.screenshot({ path: info.outputPath(`${skin}-${mode}-email-dns.png`), fullPage: true })
    await page.setViewportSize({ width: 375, height: 812 })
    expect(await fits(page)).toBe(true)
  })

  test(`${skin} ${mode}: an SSH key with options shows the server's reason`, async ({ page }) => {
    await customer(page, { skin, mode }, ({ method, path }) => {
      if (path.endsWith('/ssh-keys') && method === 'POST') return { status: 400, json: { detail: 'paste only the public key (it starts with ssh-ed25519, ssh-rsa or ecdsa-sha2-...); authorized_keys options such as command= or from= are not accepted' } }
      if (path.endsWith('/ssh-keys')) return { keys: [] }
      return {}
    })
    await page.goto('/app/ssh')
    await page.getByRole('button', { name: 'Add SSH key' }).first().click()
    const dialog = page.getByRole('dialog')
    const key = dialog.getByLabel(/^Public key/)
    await key.fill('command="/bin/true" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyOnly test@example')
    await dialog.getByRole('button', { name: 'Add SSH key' }).click()
    await expect(dialog.getByRole('alert')).toHaveText('paste only the public key (it starts with ssh-ed25519, ssh-rsa or ecdsa-sha2-...); authorized_keys options such as command= or from= are not accepted')
    await expect(key).toHaveAttribute('aria-invalid', 'true')
    await key.fill('ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyOnly test@example')
    await expect(dialog.getByRole('alert')).toHaveCount(0)
  })

  test(`${skin} ${mode}: file permissions are changed from the panel`, async ({ page }, info) => {
    let changes = 0
    const calls = await customer(page, { skin, mode }, ({ path, body }) => {
      if (path.endsWith('/files/permissions')) {
        changes++
        return changes === 1
          ? { status: 400, json: { detail: "'/public_html/missing.php' does not exist" } }
          : { mode: body.mode, changed: body.paths.map((item) => ({ path: item, mode: body.mode })) }
      }
      return {}
    })
    await page.goto('/app/files?tool=permissions')
    const dialog = page.getByRole('dialog', { name: 'Change permissions' })
    await expect(dialog).toBeVisible()
    await expect(dialog.getByLabel(/^Permissions/)).toHaveValue('644')
    await dialog.getByLabel(/^Files and folders/).fill('/public_html/missing.php')
    await dialog.getByRole('button', { name: 'Change permissions' }).click()
    await expect(dialog.getByRole('alert')).toHaveText("'/public_html/missing.php' does not exist")
    await dialog.getByLabel(/^Files and folders/).fill('/public_html/index.php\n/public_html/bin')
    await dialog.getByRole('checkbox', { name: 'Owner: execute' }).click()
    await expect(dialog.getByLabel(/^Permissions/)).toHaveValue('744')
    await dialog.getByLabel(/^Permissions/).fill('75')
    await expect(dialog.getByText('Enter three digits from 0 to 7, for example 644 or 755.')).toBeVisible()
    await expect(dialog.getByRole('button', { name: 'Change permissions' })).toBeDisabled()
    await dialog.getByRole('button', { name: '755 folders' }).click()
    await expect(dialog.getByRole('checkbox', { name: 'Public: execute' })).toBeChecked()
    await dialog.getByRole('button', { name: 'Change permissions' }).click()
    await expect(dialog.getByRole('status').filter({ hasText: 'Set to 755' })).toContainText('/public_html/bin')
    expect(calls.filter((call) => call.path.endsWith('/files/permissions')).at(-1).body).toEqual({ paths: ['/public_html/index.php', '/public_html/bin'], mode: '755' })
    expect(calls.some((call) => call.path.endsWith('/files/launch'))).toBe(false)
    await page.screenshot({ path: info.outputPath(`${skin}-${mode}-permissions.png`) })
    await page.setViewportSize({ width: 375, height: 812 })
    expect(await fits(page)).toBe(true)
  })

  test(`${skin} ${mode}: a refused file manager launch shows the API detail, not the raw body`, async ({ page }) => {
    await customer(page, { skin, mode }, ({ path }) => {
      if (path.endsWith('/files/launch')) return { status: 404, json: { detail: "account 'alpha' has no home directory", reference: '0123456789abcdef' } }
      return {}
    })
    await page.goto('/app/files')
    const error = page.locator('[data-ui-state="error"]')
    await expect(error).toContainText("account 'alpha' has no home directory")
    await expect(error).not.toContainText('{"detail"')
    await expect(page.getByRole('link', { name: 'Change permissions' })).toHaveAttribute('href', '/app/files?tool=permissions')
  })
}
