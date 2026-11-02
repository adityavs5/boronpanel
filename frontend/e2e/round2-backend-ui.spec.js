import { test, expect } from '@playwright/test'

// Round 2: the UI for the backend changes of every area (domains, mail, DNS,
// SSL and Force HTTPS, staging, PHP limits, apps, backups, recovery copies,
// phpMyAdmin provisioning, plans and suspension), against the response
// shapes in api/routers and the daemon handlers.

const reply = (status, json, headers) => ({ fulfill: { status, json, headers } })
const fits = page => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
const toasts = page => page.getByRole('region', { name: 'Notifications (F8)' })

async function session(page, { skin = 'evolution', theme = 'light', role = 'customer', username = 'alpha', handle }) {
  const state = { requests: [], errors: [] }
  page.on('pageerror', error => state.errors.push(error.message))
  await page.addInitScript(({ skin, theme, role, username }) => {
    localStorage.setItem('boron.ui', JSON.stringify({ state: { skin, theme }, version: 0 }))
    localStorage.setItem('boron.auth', JSON.stringify({ state: { role, username }, version: 0 }))
  }, { skin, theme, role, username })
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method()
    let body = null
    try { body = method === 'GET' ? null : request.postDataJSON() } catch { body = request.postData() }
    const entry = { method, path, search: url.search, body }
    state.requests.push(entry)
    if (path.endsWith('/whoami')) return route.fulfill({ json: { role, username } })
    if (path.endsWith('/onboarding')) return route.fulfill({ json: { completed: true } })
    const answer = await handle?.(entry)
    if (answer?.fulfill) return route.fulfill(answer.fulfill)
    return route.fulfill({ json: answer ?? {} })
  })
  return state
}
const writes = (state, method, suffix) => state.requests.filter(item => item.method === method && item.path.endsWith(suffix))

// --- domains ---------------------------------------------------------------

for (const [skin, theme] of [['evolution', 'light'], ['paper-lantern', 'dark']]) {
  test(`${skin} ${theme}: a domain another account holds is refused on the domain field`, async ({ page }) => {
    await session(page, { skin, theme, handle: ({ method, path }) => {
      if (method === 'POST' && path === '/api/v1/task-operations/accounts/alpha/domains') return reply(409, { detail: 'domain already belongs to another account' })
      if (path === '/api/v1/accounts/alpha/domains') return { domains: [{ domain: 'example.com', kind: 'primary' }] }
      if (path.endsWith('/parked-domains')) return { parked_domains: [] }
    } })
    await page.goto('/app/domains')
    await page.getByRole('button', { name: 'Add domain' }).first().click()
    const dialog = page.getByRole('dialog')
    const field = dialog.getByLabel(/^Domain/, { exact: false }).last()
    await field.fill('taken.example.org')
    await dialog.getByRole('button', { name: 'Add domain' }).click()
    await expect(field).toHaveAttribute('aria-invalid', 'true')
    await expect(dialog.getByRole('alert')).toHaveText('domain already belongs to another account')
    await field.fill('free.example.org')
    await expect(dialog.getByRole('alert')).toHaveCount(0)
  })
}

test('parking a domain reports the DNS zone it created', async ({ page }) => {
  await session(page, { handle: ({ method, path }) => {
    if (method === 'POST' && path.endsWith('/parked-domains')) return { parked_domain: 'alias.org', target_domain: 'example.com', dns_zone_created: true }
    if (path === '/api/v1/accounts/alpha/domains') return { domains: [{ domain: 'example.com', kind: 'primary' }] }
    if (path.endsWith('/parked-domains')) return { parked_domains: [] }
  } })
  await page.goto('/app/domains')
  await page.getByRole('button', { name: 'Park a domain' }).click()
  await page.getByRole('dialog').getByPlaceholder('alias.com').fill('alias.org')
  await page.getByRole('dialog').getByRole('button', { name: 'Park domain' }).click()
  await expect(toasts(page)).toContainText('A DNS zone was created for alias.org.')
})

// --- mail --------------------------------------------------------------------

const mailboxes = hostname => ({
  domain: 'example.com', mailboxes: [{ local_part: 'info', quota_mb: 1024, active: true }],
  client_settings: {
    hostname, username: 'full_address',
    incoming: [{ protocol: 'IMAP', port: 993, security: 'SSL/TLS' }, { protocol: 'IMAP', port: 143, security: 'STARTTLS' }, { protocol: 'POP3', port: 995, security: 'SSL/TLS' }, { protocol: 'POP3', port: 110, security: 'STARTTLS' }],
    outgoing: [{ protocol: 'SMTP', port: 465, security: 'SSL/TLS', authentication: 'password' }, { protocol: 'SMTP', port: 587, security: 'STARTTLS', authentication: 'password' }],
  },
})

for (const [skin, theme] of [['evolution', 'dark'], ['paper-lantern', 'light']]) {
  test(`${skin} ${theme}: the Email page lists the settings for mail apps`, async ({ page }) => {
    let hostname = 'mail.example.com'
    await session(page, { skin, theme, handle: ({ path }) => {
      if (path.endsWith('/mailboxes')) return mailboxes(hostname)
      if (path.endsWith('/domains')) return { domains: [{ domain: 'example.com', kind: 'primary' }] }
    } })
    await page.goto('/app/email')
    const settings = page.getByRole('region', { name: 'Mail app settings' })
    await expect(settings).toContainText('Incoming mail: mail.example.com — IMAP 993 (SSL/TLS) or 143 (STARTTLS); POP3 995 (SSL/TLS) or 110 (STARTTLS)')
    await expect(settings).toContainText('Outgoing mail (SMTP): mail.example.com — 465 (SSL/TLS) or 587 (STARTTLS), authentication required')
    await expect(settings).toContainText('Username: your full email address, for example info@example.com. Password: the mailbox password.')
    await page.setViewportSize({ width: 375, height: 812 })
    expect(await fits(page)).toBe(true)
    hostname = null
    await page.reload()
    await expect(page.getByRole('region', { name: 'Mail app settings' })).toContainText("The server's mail hostname is not set up yet. Ask your hosting provider.")
  })
}

const publicReadiness = {
  domain: 'uptimeways.com', zone: 'uptimeways.com', managed: true, provider: 'local', mail_hostname: 'mail.boron.example',
  authoritative: false, source: 'public',
  authority: { zone: 'uptimeways.com', public_nameservers: ['ana.ns.cloudflare.com', 'bob.ns.cloudflare.com'], status: 'elsewhere' },
  detail: "This server's zone is not authoritative: public DNS delegates uptimeways.com to ana.ns.cloudflare.com, bob.ns.cloudflare.com. The records below are what public DNS returns; change them where the domain's DNS is hosted, or point the domain at this server's nameservers.",
  records: [
    { key: 'mx', name: '@', fqdn: 'uptimeways.com', type: 'MX', expected: ['10 mail.boron.example.'], actual: ['1 aspmx.l.google.com.'], status: 'conflicting', source: 'public', detail: 'Mail for uptimeways.com goes to aspmx.l.google.com, not to this server' },
    { key: 'spf', name: '@', fqdn: 'uptimeways.com', type: 'TXT', expected: ['"v=spf1 mx -all"'], actual: [], status: 'missing', source: 'public' },
    { key: 'dkim', name: 'default._domainkey', fqdn: 'default._domainkey.uptimeways.com', type: 'TXT', expected: ['"v=DKIM1; k=rsa; p=MIIBexample"'], actual: [], status: 'unknown', source: 'public', detail: 'Public DNS did not answer in time' },
  ],
  local_records: [{ key: 'mx', name: '@', type: 'MX', expected: ['10 mail.boron.example.'], actual: ['10 mail.boron.example.'], status: 'present' }],
}

for (const [skin, theme] of [['evolution', 'light'], ['paper-lantern', 'dark']]) {
  test(`${skin} ${theme}: mail readiness explains a zone public DNS does not use`, async ({ page }, info) => {
    let readiness = publicReadiness
    const state = await session(page, { skin, theme, handle: ({ path }) => {
      if (path.endsWith('/dns-readiness')) return readiness
      if (path.endsWith('/records')) return { zone: 'uptimeways.com', managed: true, provider: 'local', records: [] }
      if (path.endsWith('/domains')) return { domains: [{ domain: 'uptimeways.com', kind: 'primary' }] }
    } })
    await page.goto('/app/email/dns')
    await expect(page.getByRole('status').filter({ hasText: "This server's zone is not authoritative: public DNS delegates uptimeways.com to ana.ns.cloudflare.com, bob.ns.cloudflare.com." })).toBeVisible()
    await expect(page.getByText('default._domainkey.uptimeways.com · TXT')).toBeVisible()
    await expect(page.getByText('"v=spf1 mx -all"')).toBeVisible()
    await expect(page.getByText('Not checked')).toBeVisible()
    await expect(page.getByText('Conflict', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Replace' })).toHaveCount(0)
    await page.getByRole('button', { name: "Add missing records to this server's zone" }).click()
    await expect.poll(() => writes(state, 'POST', '/dns-repair').length).toBe(1)
    await page.screenshot({ path: info.outputPath(`${skin}-${theme}-mail-readiness.png`), fullPage: true })
    await page.setViewportSize({ width: 375, height: 812 })
    expect(await fits(page)).toBe(true)

    // Public DNS could not be checked: a muted note, local rows.
    readiness = { ...publicReadiness, authoritative: null, source: 'local', detail: 'Public DNS could not be checked, so these are the records in this server\'s zone.', records: publicReadiness.local_records, local_records: undefined }
    await page.reload()
    await expect(page.getByText("Public DNS could not be checked, so these are the records in this server's zone.")).toBeVisible()
    await expect(page.getByRole('button', { name: 'Add missing records', exact: true })).toBeVisible()

    // No zone on this server, but public records were checked.
    readiness = { domain: 'uptimeways.com', managed: false, zone: null, records: publicReadiness.records, authoritative: false, source: 'public', local_records: [], detail: "This server has no DNS zone for uptimeways.com; the records below are what public DNS returns. Publish them where the domain's DNS is hosted." }
    await page.reload()
    await expect(page.getByText('This server has no DNS zone for uptimeways.com', { exact: false })).toBeVisible()
    await expect(page.getByRole('button', { name: /Add missing records/ })).toHaveCount(0)
  })
}

// --- DNS ---------------------------------------------------------------------

test('adding a DNS value lists stored values that are not valid', async ({ page }) => {
  await session(page, { skin: 'paper-lantern', handle: ({ method, path }) => {
    if (method === 'PUT' && path.endsWith('/records')) return { zone: 'example.com', subdomain: '@', type: 'TXT', values: ['"ok"', 'bad value'], ttl: 3600, invalid_existing_values: [{ value: 'bad value', reason: 'TXT strings must be quoted' }] }
    if (path.endsWith('/records')) return { zone: 'example.com', managed: true, provider: 'local', records: [] }
    if (path.endsWith('/domains')) return { domains: [{ domain: 'example.com', kind: 'primary' }] }
  } })
  await page.goto('/app/dns')
  await page.getByRole('button', { name: 'Add record' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('combobox').selectOption('TXT')
  await dialog.getByPlaceholder('1.2.3.4').fill('"ok"')
  await dialog.getByRole('button', { name: 'Save record' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Saved. These existing values were kept as they were but are not valid: bad value — TXT strings must be quoted. Edit or remove them.' })).toBeVisible()
})

// --- domain page: Force HTTPS, staging, nameservers, .htaccess ---------------

const domainRows = { domains: [{ domain: 'example.com', kind: 'primary', docroot: '/home/alpha/public_html', status: 'active' }] }

for (const [skin, theme] of [['evolution', 'dark'], ['paper-lantern', 'light']]) {
  test(`${skin} ${theme}: Force HTTPS follows the certificate`, async ({ page }) => {
    let status = { domain: 'example.com', enabled: false, active: false, certificate_valid: false, covered_hosts: [], uncovered_hosts: [], reason: null }
    const state = await session(page, { skin, theme, handle: ({ method, path, body }) => {
      if (path.endsWith('/force-https') && method === 'PUT') {
        if (status.certificate_valid === 'refuse') return reply(400, { detail: 'Force HTTPS needs a valid SSL certificate for example.com. Issue the certificate first, then turn Force HTTPS on.' })
        status = { ...status, enabled: body.enabled, active: body.enabled, covered_hosts: ['example.com'], uncovered_hosts: ['www.example.com'] }
        return status
      }
      if (path.endsWith('/force-https')) return status
      if (path.endsWith('/staging')) return { exists: false }
      if (path.endsWith('/htaccess-status')) return { access_rules: [], unsupported_access_directives: [], access_scan_complete: true }
      if (path.endsWith('/records')) return { zone: 'example.com', managed: false }
      if (path === '/api/v1/accounts/alpha/domains') return domainRows
    } })
    await page.goto('/app/domains/example.com')
    const toggle = page.getByRole('switch', { name: 'Force HTTPS' })
    await expect(toggle).toBeDisabled()
    await expect(page.getByText('Issue an SSL certificate for example.com first.')).toBeVisible()
    await expect(page.getByText('Redirect every http:// request to https:// (permanent redirect).')).toBeVisible()

    status = { ...status, certificate_valid: true }
    await page.reload()
    await page.getByRole('switch', { name: 'Force HTTPS' }).click()
    await expect(page.getByText('http:// is redirected to https:// for: example.com')).toBeVisible()
    await expect(page.getByText('www.example.com is not on the certificate, so it stays on http://')).toBeVisible()
    expect(writes(state, 'PUT', '/force-https')[0].body).toEqual({ enabled: true })

    status = { ...status, enabled: true, active: false, reason: 'Maintenance mode is on. Force HTTPS applies again when maintenance mode is switched off.' }
    await page.reload()
    await expect(page.getByText('Maintenance mode is on. Force HTTPS applies again when maintenance mode is switched off.')).toBeVisible()

    status = { ...status, enabled: false, certificate_valid: 'refuse' }
    await page.reload()
    await page.getByRole('switch', { name: 'Force HTTPS' }).click()
    await expect(page.getByRole('alert').filter({ hasText: 'Force HTTPS needs a valid SSL certificate for example.com.' })).toBeVisible()
    await page.setViewportSize({ width: 375, height: 812 })
    expect(await fits(page)).toBe(true)
  })
}

test('the staging site is created, refused with a reason, and deleted', async ({ page }, info) => {
  let staging = { exists: false }
  let createAnswer = null
  const state = await session(page, { skin: 'paper-lantern', theme: 'dark', handle: ({ method, path }) => {
    if (path.endsWith('/staging') && method === 'POST') {
      if (createAnswer) return createAnswer
      staging = { exists: true, id: 3, source_domain: 'example.com', staging_domain: 'staging.example.com', staging_url: 'http://staging.example.com', db_name: 'alpha_stg1', created_at: '2026-11-01T10:00:00Z', last_synced_at: null }
      return { ...staging, notice: 'The staging site uses http://staging.example.com because staging.example.com does not point to this server yet. Issue an SSL certificate for it, then sync to switch it to https.' }
    }
    if (path.endsWith('/staging/sync')) return { ...staging, last_synced_at: '2026-11-02T10:00:00Z' }
    if (path.endsWith('/staging') && method === 'DELETE') { staging = { exists: false }; return { source_domain: 'example.com', status: 'deleted', cleanup_failed: ['staging files'] } }
    if (path.endsWith('/staging')) return staging
    if (path.endsWith('/force-https')) return { domain: 'example.com', enabled: false, active: false, certificate_valid: true, covered_hosts: [], uncovered_hosts: [], reason: null }
    if (path.endsWith('/htaccess-status')) return { access_rules: [], unsupported_access_directives: [], access_scan_complete: true }
    if (path.endsWith('/records')) return { zone: 'example.com', managed: false }
    if (path === '/api/v1/accounts/alpha/domains') return domainRows
  } })
  await page.goto('/app/domains/example.com')
  createAnswer = reply(409, { detail: 'The folder staging.example.com/public_html in the home directory already contains files. Move or delete them, then create the staging site again.' })
  await page.getByRole('button', { name: 'Create staging site' }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'already contains files' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Open the File Manager' })).toHaveAttribute('href', '/app/files')

  createAnswer = reply(409, { detail: "a staging environment for 'example.com' already exists (id 3) -- use sync to re-clone it" })
  await page.getByRole('button', { name: 'Create staging site' }).click()
  await page.getByRole('button', { name: 'Sync the staging site' }).click()
  await expect.poll(() => writes(state, 'POST', '/staging/sync').length).toBe(1)

  createAnswer = null
  staging = { exists: false }
  await page.reload()
  await page.getByRole('button', { name: 'Create staging site' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'The staging site uses http://staging.example.com because staging.example.com does not point to this server yet.' })).toBeVisible()
  await expect(page.getByRole('link', { name: /http:\/\/staging\.example\.com/ })).toBeVisible()
  await page.screenshot({ path: info.outputPath('domain-page-staging.png'), fullPage: true })

  await page.getByRole('button', { name: 'Delete staging site' }).click()
  await expect(page.getByRole('alertdialog').or(page.getByRole('dialog'))).toContainText('Delete the staging site? Its domain, database and copied files are deleted. Your live site is not changed.')
  await page.getByRole('dialog').getByRole('button', { name: 'Delete staging site' }).click()
  await expect(page.getByText('The staging site was deleted, but some parts could not be removed')).toBeVisible()
  await expect(page.getByText('staging files', { exact: true })).toBeVisible()
  await page.setViewportSize({ width: 375, height: 812 })
  expect(await fits(page)).toBe(true)
})

test('nameservers of a name without its own zone point to the parent zone', async ({ page }) => {
  await session(page, { handle: ({ path }) => {
    if (path.endsWith('/nameservers')) return { domain: 'blog.example.com', provider: null, managed: false, parent_zone: 'example.com', nameservers: [], glue: {} }
    if (path.endsWith('/records')) return { zone: 'blog.example.com', managed: true, provider: 'local', records: [] }
    if (path.endsWith('/wildcard')) return { enabled: false }
    if (path.endsWith('/staging')) return { exists: false }
    if (path.endsWith('/force-https')) return { domain: 'blog.example.com', enabled: false, active: false, certificate_valid: false, covered_hosts: [], uncovered_hosts: [], reason: null }
    if (path.endsWith('/htaccess-status')) return { access_rules: [], unsupported_access_directives: [], access_scan_complete: true }
    if (path === '/api/v1/accounts/alpha/domains') return { domains: [{ domain: 'blog.example.com', kind: 'subdomain' }, { domain: 'example.com', kind: 'primary' }] }
  } })
  await page.goto('/app/domains/blog.example.com')
  await expect(page.getByText('blog.example.com has no DNS zone of its own; it uses the nameservers of example.com.')).toBeVisible()
  await expect(page.getByRole('link', { name: 'example.com', exact: true })).toHaveAttribute('href', '/app/domains/example.com#nameservers')
  await expect(page.getByRole('button', { name: 'Save nameservers' })).toHaveCount(0)
})

test('.htaccess notes without a line number and a reused access check', async ({ page }) => {
  await session(page, { skin: 'paper-lantern', handle: ({ path }) => {
    if (path.endsWith('/htaccess-status')) return {
      access_rules: [{ directory: '/private', mode: 'deny', allow: [], deny: [] }],
      unsupported_access_directives: [
        { path: 'private/.htaccess', line: 4, directive: 'SetEnvIf Referer example bad' },
        { path: 'assets/.htaccess', line: 0, directive: "this directory's access restriction is not enforced: a <Files>, <FilesMatch> or <Limit> section here or in a parent folder allows some files, and OpenLiteSpeed cannot limit that to those files" },
      ],
      access_scan_complete: false, access_scan_reused_previous: true,
    }
    if (path.endsWith('/staging')) return { exists: false }
    if (path.endsWith('/force-https')) return { domain: 'example.com', enabled: false, active: false, certificate_valid: false, covered_hosts: [], uncovered_hosts: [], reason: null }
    if (path.endsWith('/records')) return { zone: 'example.com', managed: false }
    if (path === '/api/v1/accounts/alpha/domains') return domainRows
  } })
  await page.goto('/app/domains/example.com')
  await expect(page.getByText('private/.htaccess, line 4')).toBeVisible()
  const notes = page.getByRole('status').filter({ hasText: 'Access rules the server enforces differently' })
  await expect(notes).toContainText("assets/.htaccess: this directory's access restriction is not enforced")
  await expect(notes).not.toContainText('line 0')
  await expect(page.getByText('This site has more folders than the server re-reads at once; rules found by the last full check stay enforced for folders that were not re-read.')).toBeVisible()
})

test('LSCache shows a bounded object count and why a purge was refused', async ({ page }) => {
  await session(page, { skin: 'evolution', theme: 'dark', handle: ({ method, path }) => {
    if (path.endsWith('/lscache/purge')) return reply(409, { detail: 'LSCache is not enabled for example.com, so there is no cache to purge. Turn LSCache on first.' })
    if (path.endsWith('/lscache/stats')) return { domain: 'example.com', cached_object_count: 2000, cached_object_count_complete: false, last_purged_at: null }
    if (path.endsWith('/lscache') && method === 'GET') return { domain: 'example.com', enabled: false, ttl_seconds: 3600, exclude_paths: [], last_purged_at: null }
    if (path === '/api/v1/accounts/alpha/domains') return domainRows
  } })
  await page.goto('/app/cache')
  await expect(page.getByText('Cached objects: 2,000+.', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: 'Purge cache' }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'LSCache is not enabled for example.com, so there is no cache to purge. Turn LSCache on first.' })).toBeVisible()
})

// --- SSL ---------------------------------------------------------------------

test('an issued certificate lists the names it covers and the ones left out', async ({ page }) => {
  await session(page, { handle: ({ method, path }) => {
    if (method === 'POST' && path.endsWith('/ssl/issue')) return { domain: 'example.com', challenge: 'http-01', status: 'issued', names: ['example.com', 'shop.example.com'], skipped_names: [{ name: 'www.example.com', reason: 'does not point to this server' }] }
    if (path.endsWith('/force-https')) return { domain: 'example.com', enabled: false, active: false, certificate_valid: false, covered_hosts: [], uncovered_hosts: [], reason: null }
    if (path === '/api/v1/accounts/alpha/ssl') return { certbot_timer_active: true, domains: [{ domain: 'example.com', cert_status: 'missing' }] }
  } })
  await page.goto('/app/ssl')
  await page.getByRole('button', { name: 'SSL actions for example.com' }).click()
  await page.getByRole('menuitem', { name: 'Issue certificate' }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Issue certificate' }).click()
  await expect(page.getByText('Certificate covers: example.com, shop.example.com').first()).toBeVisible()
  await expect(page.getByText('www.example.com was left out because it does not point to this server.')).toBeVisible()
  await page.getByRole('button', { name: 'Manage SSL for example.com' }).click()
  await expect(page.getByRole('dialog').getByRole('switch', { name: 'Force HTTPS' })).toBeVisible()
})

// --- PHP and apps ------------------------------------------------------------

test('the PHP memory limit stays within the account memory limit', async ({ page }) => {
  const state = await session(page, { skin: 'paper-lantern', theme: 'dark', handle: ({ method, path }) => {
    if (path === '/api/v1/accounts/alpha/php-ini' && method === 'GET') return {
      username: 'alpha', memory_limit_max_mb: 512, php_versions: ['8.3'], limit_presets: [{ id: 'large', name: 'Large site', description: 'More memory', directives: { memory_limit: '1024M', max_execution_time: 300 } }],
      directives: [{ name: 'memory_limit', type: 'size', value: null, default: '256M', max_mb: 2048 }, { name: 'max_execution_time', type: 'int', value: null, default: 30, min: 1, max: 3600 }],
    }
    if (path.endsWith('/php-ini')) return { status: 'saved' }
    if (path === '/api/v1/accounts/alpha/domains') return domainRows
  } })
  await page.goto('/app/php')
  await expect(page.getByText("Up to 512M, this account's memory limit.", { exact: false })).toBeVisible()
  await page.getByLabel(/Memory limit/).fill('1G')
  await expect(page.getByText("This account's memory limit is 512M", { exact: false })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Save changes' })).toBeDisabled()
  await page.getByLabel('PHP limits template').selectOption('large')
  await expect(page.getByLabel(/Memory limit/)).toHaveValue('512M')
  await expect(page.getByText("The memory limit is set to 512M, this account's memory limit, instead of the template's 1024M.")).toBeVisible()
  await page.getByRole('button', { name: 'Save changes' }).click()
  await expect.poll(() => writes(state, 'PATCH', '/php-ini').length).toBe(1)
  expect(writes(state, 'PATCH', '/php-ini')[0].body.directives.memory_limit).toBe('512M')
})

test('a failed npm install shows where its log is', async ({ page }) => {
  await session(page, { handle: ({ method, path }) => {
    if (method === 'POST' && path.endsWith('/npm-install')) return reply(400, { detail: "npm install failed (exit status 1). Check package.json; npm's full log is in nodeapps/api/.npm/_logs." })
    if (path === '/api/v1/accounts/alpha/apps/node') return { apps: [{ id: 4, name: 'api', domain: 'app.example.com', port: 3001, active: 'inactive', entry_point: 'server.js', node_version: '20', env_vars: {} }] }
  } })
  await page.goto('/app/node-apps')
  await page.getByRole('button', { name: 'Manage application api' }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Run npm install' }).click()
  await expect(page.getByRole('dialog').getByRole('alert')).toHaveText("npm install failed (exit status 1). Check package.json; npm's full log is in nodeapps/api/.npm/_logs.")
})

// --- WordPress clone ---------------------------------------------------------

test('a WordPress clone explains an http:// address and links to it', async ({ page }) => {
  await session(page, { username: 'hostingdemo', handle: ({ method, path }) => {
    if (path === '/api/v1/wordpress') return { installs: [{ id: 'example.com', username: 'hostingdemo', domain: 'example.com', path: '', wp_version: '6.8', admin_user: 'siteadmin', url: 'https://example.com' }], domains: [{ username: 'hostingdemo', domain: 'example.com' }, { username: 'hostingdemo', domain: 'clone.example.com' }], errors: [] }
    if (method === 'POST' && path.endsWith('/manage')) return { id: 9, status: 'pending', url: 'http://www.clone.example.com', notice: 'The clone uses http://www.clone.example.com because www.clone.example.com does not point to this server yet. Issue an SSL certificate for clone.example.com, then change the WordPress address to https.' }
    if (path.includes('/actions/runs/')) return { id: 9, status: 'completed', stdout: JSON.stringify({ message: 'Done' }) }
  } })
  await page.goto('/app/wordpress')
  await page.getByRole('button', { name: 'Manage website', exact: true }).click()
  await page.getByRole('tab', { name: 'Clone site', exact: true }).click()
  await page.getByRole('combobox', { name: 'Clone destination', exact: true }).selectOption('clone.example.com')
  await page.getByRole('button', { name: 'Clone website', exact: true }).click()
  await expect(page.getByText('The clone uses http://www.clone.example.com because', { exact: false })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Open cloned website' })).toHaveAttribute('href', 'http://www.clone.example.com')
})

// --- backups -----------------------------------------------------------------

test('a customer downloads an archive, and a refusal is shown', async ({ page }) => {
  let refuse = true
  await session(page, { handle: ({ path }) => {
    if (path.endsWith('/backups/7/download')) return refuse
      ? reply(400, { detail: "A download of this account's backups is already in progress. Try again when it has finished." })
      : { fulfill: { status: 200, body: 'archive-bytes', headers: { 'content-type': 'application/octet-stream', 'content-disposition': 'attachment; filename="alpha-full-7.tar.gz"' } } }
    if (path === '/api/v1/accounts/alpha/backups') return { jobs: [{ id: 7, kind: 'full', status: 'completed', size_bytes: 1048576, started_at: '2026-11-01T10:00:00Z', progress_message: 'Completed' }] }
    if (path.endsWith('/restores/list')) return { restore_jobs: [{ id: 2, kind: 'full', status: 'completed', started_at: '2026-11-01T11:00:00Z', completed_at: '2026-11-01T11:05:00Z', progress_message: 'completed with warnings', error: 'Recreated mailboxes have new random passwords (backups never store passwords); set new ones for: info@example.com.' }] }
    if (path.endsWith('/snapshots/runs')) return { runs: [] }
    if (path.endsWith('/snapshots/restores')) return { restores: [] }
  } })
  await page.goto('/app/backups')
  const link = page.getByRole('link', { name: 'Download archive 7' })
  await expect(link).toHaveAttribute('href', '/api/v1/accounts/alpha/backups/7/download')
  await link.click()
  await expect(toasts(page)).toContainText("A download of this account's backups is already in progress. Try again when it has finished.")
  refuse = false
  const download = page.waitForEvent('download')
  await link.click()
  expect((await download).suggestedFilename()).toBe('alpha-full-7.tar.gz')

  await page.getByRole('tab', { name: 'Restore history' }).click()
  const warning = page.getByText('Recreated mailboxes have new random passwords', { exact: false }).filter({ visible: true }).first()
  await expect(warning).toBeVisible()
  await expect(warning).not.toHaveClass(/text-danger/)
})

test('a terminated account offers restore only for full archives in the backup manager', async ({ page }) => {
  await session(page, { role: 'admin', username: 'admin', handle: ({ path }) => {
    if (path === '/api/v1/backups/snapshots/catalog/accounts') return { accounts: [{ username: 'gone', primary_domain: 'gone.example', account_status: 'terminated', account_group: 'former', availability: 'available', recovery_point_count: 2, latest_recovery_at: null }], counts: { active: 0, former: 1 } }
    if (path === '/api/v1/backups/jobs') return { jobs: [{ id: 11, kind: 'full', status: 'completed', started_at: '2026-10-01T10:00:00Z' }, { id: 12, kind: 'database', item_ref: 'gone_wp', status: 'completed', started_at: '2026-10-02T10:00:00Z' }] }
    if (path.endsWith('/destinations')) return { destinations: [] }
    if (path.endsWith('/policies')) return { policies: [] }
    if (path === '/api/v1/accounts') return []
    if (path.endsWith('/destination-operations')) return { operations: [] }
    if (path.endsWith('/snapshots/runs')) return { runs: [] }
  } })
  await page.goto('/app/backup-jobs')
  await page.getByRole('button', { name: /Former accounts/ }).click()
  await page.getByRole('button', { name: 'gone', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Restore account' })).toHaveCount(1)
})

test('a destination still used by disabled jobs is deleted together with them', async ({ page }) => {
  let destinations = [{ id: 5, name: 'Offsite', kind: 'local', path: '/srv/backup', status: 'ready', enabled: true, customer_visible: true, connection: {} }, { id: 6, name: 'Old', kind: 'local', path: 'configuration-removed', status: 'deleted', enabled: false, customer_visible: false, connection: {} }]
  const state = await session(page, { role: 'admin', username: 'admin', handle: ({ method, path, search }) => {
    if (method === 'DELETE' && path === '/api/v1/backups/snapshots/destinations/5') {
      if (!search.includes('delete_jobs=true')) return reply(409, { detail: 'Disabled backup jobs still use this destination: Nightly. Delete them together with the destination, or delete them first.' })
      destinations = destinations.filter(row => row.id !== 5)
      return { id: 5, status: 'configuration_removed', remote_data_deleted: false, jobs_deleted: ['Nightly'], jobs_updated: [] }
    }
    if (path.endsWith('/destinations')) return { destinations }
    if (path.endsWith('/policies')) return { policies: [] }
    if (path === '/api/v1/accounts') return []
    if (path.endsWith('/catalog/accounts')) return { accounts: [], counts: { active: 0, former: 0 } }
    if (path.endsWith('/destination-operations')) return { operations: [] }
    if (path.endsWith('/snapshots/runs')) return { runs: [] }
  } })
  await page.goto('/app/backup-jobs?tab=destinations')
  await expect(page.getByRole('button', { name: 'Old', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Offsite', exact: true }).click()
  await page.getByRole('button', { name: 'Remove configuration' }).click()
  const confirm = page.getByRole('dialog').filter({ hasText: 'Remove backup destination?' })
  await confirm.getByRole('button', { name: 'Remove configuration' }).click()
  await expect(confirm).toContainText('Disabled backup jobs still use this destination: Nightly.')
  await confirm.getByRole('button', { name: 'Delete jobs and destination' }).click()
  await expect(toasts(page)).toContainText('Backup jobs deleted: Nightly.')
  expect(state.requests.filter(item => item.method === 'DELETE').map(item => item.search)).toEqual(['', '?delete_jobs=true'])
})

// --- administrator: recovery copies, phpMyAdmin, plans, suspension -----------

const account = { id: 1, username: 'alpha', status: 'active', primary_domain: 'example.com', php_version: '8.3', plan_id: 1, limits_source: 'plan', quota_hard_mb: 10240, inode_enforcement_state: 'warning', inode_enforce_at: '2026-11-09T10:00:00Z' }

for (const [skin, theme] of [['evolution', 'light'], ['paper-lantern', 'dark']]) {
  test(`${skin} ${theme}: an administrator restores and releases database recovery copies`, async ({ page }, info) => {
    let copies = [
      { id: '20261101-a1', db_name: 'alpha_wp', created_at: '2026-11-01T10:00:00+00:00', size_bytes: 2048, origin: 'rollback_failed', description: 'Taken before an import that failed and could not be rolled back', import_error: "ERROR 1064 (42000) at line 3: You have an error in your SQL syntax", rollback_error: 'ERROR 2013: Lost connection during restore', complete: true, database_exists: true, restorable: true, reason: null },
      { id: '20261101-b2', db_name: 'alpha_old', created_at: '2026-11-01T09:00:00+00:00', size_bytes: 0, origin: 'incomplete_import', description: 'Left by an import that was cut short', import_error: null, rollback_error: null, complete: false, database_exists: false, restorable: false, reason: "Database 'alpha_old' no longer exists in this account, so this copy cannot be restored. Release it, or create the database again first" },
    ]
    let failRestore = true
    const state = await session(page, { skin, theme, role: 'admin', username: 'admin', handle: ({ method, path }) => {
      if (path.endsWith('/recovery-copies/20261101-a1/restore')) {
        if (failRestore) { copies = [...copies, { ...copies[0], id: '20261102-c3', origin: 'before_restore', description: 'What the database held before a restore', import_error: null, rollback_error: null }]; return reply(400, { detail: 'The recovery copy could not be restored. MariaDB reported: ERROR 1146. The copy and the database\'s contents before this attempt (copy 20261102-c3) were both kept' }) }
        copies = copies.filter(copy => copy.id !== '20261101-a1')
        return { status: 'restored', db_name: 'alpha_wp', copy_id: '20261101-a1', previous_contents_copy_id: '20261102-d4', remaining: copies.length, imports_blocked: false, warning: "'alpha_wp' now holds the contents from 2026-11-01T10:00:00+00:00. What it held before this restore was kept as recovery copy 20261102-d4; release it once it is no longer needed." }
      }
      if (method === 'DELETE' && path.includes('/recovery-copies/')) { copies = copies.filter(copy => !path.endsWith(copy.id)); return { status: 'released', copy_id: path.split('/').at(-1), remaining: copies.length, imports_blocked: copies.length >= 3 } }
      if (path.endsWith('/recovery-copies')) return { username: 'alpha', copies, limit: 3, imports_blocked: copies.length >= 3 }
      if (path === '/api/v1/accounts/alpha') return account
      if (path.endsWith('/databases')) return { databases: [], pma_available: true }
      if (path.endsWith('/databases/users')) return { users: [], remote_access: { available: false } }
      if (path === '/api/v1/admin/plans') return { plans: [] }
    } })
    await page.goto('/app/accounts/alpha?tab=databases')
    const card = page.getByRole('list', { name: 'Recovery copies' })
    await expect(card).toContainText('MariaDB reported: ERROR 1064 (42000) at line 3')
    await expect(card).toContainText("Database 'alpha_old' no longer exists in this account")
    await card.getByText('Why the automatic rollback failed').click()
    await expect(card).toContainText('ERROR 2013: Lost connection during restore')
    await expect(card.getByRole('button', { name: 'Restore' }).nth(1)).toBeDisabled()
    await page.screenshot({ path: info.outputPath(`${skin}-${theme}-recovery-copies.png`), fullPage: true })

    await card.getByRole('button', { name: 'Restore' }).first().click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toContainText('This replaces everything alpha_wp holds now with the copy taken')
    await expect(dialog).toContainText('What it holds now is kept as a new recovery copy first.')
    await dialog.getByRole('button', { name: 'Restore copy' }).click()
    await expect(dialog.getByRole('alert')).toContainText('(copy 20261102-c3) were both kept')
    // The failed attempt kept the database's contents as a third copy.
    await expect(page.getByText('SQL imports for this account are blocked until recovery copies are restored or released.')).toBeVisible()
    expect(writes(state, 'POST', '/recovery-copies/20261101-a1/restore')[0].body).toEqual({ confirm: true })

    failRestore = false
    await dialog.getByRole('button', { name: 'Restore copy' }).click()
    await expect(toasts(page)).toContainText('What it held before this restore was kept as recovery copy 20261102-d4')

    await card.getByRole('button', { name: 'Release' }).first().click()
    await expect(page.getByRole('dialog')).toContainText('Permanently delete this copy of alpha_old taken')
    await page.getByRole('dialog').getByRole('button', { name: 'Delete copy' }).click()
    await expect.poll(() => state.requests.some(item => item.method === 'DELETE' && item.search === '?confirm=true')).toBe(true)
    await page.setViewportSize({ width: 375, height: 812 })
    expect(await fits(page)).toBe(true)
  })
}

test('the suspend confirmation says what suspension stops and keeps', async ({ page }) => {
  await session(page, { role: 'admin', username: 'admin', handle: ({ path }) => {
    if (path === '/api/v1/accounts/alpha') return account
    if (path === '/api/v1/admin/plans') return { plans: [] }
  } })
  await page.goto('/app/accounts/alpha')
  await expect(page.getByText('Warning period, enforced from', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: 'More' }).click()
  await page.getByRole('menuitem', { name: 'Suspend' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('Suspending ends everything the account is running: SSH sessions and what was started from them, running cron jobs and PHP workers.')
  await expect(dialog).toContainText("Suspending also blocks the account's email logins (IMAP, POP3, SMTP and webmail). Incoming mail is still accepted and delivered.")
})

test('server setup follows the phpMyAdmin installation', async ({ page }) => {
  let pma = { installed: false, published: false, hostname: null, target_hostname: 'pma.example.com', provisioning: 'installing', last_error: null, last_stage: null, last_source: 'setup', last_attempt_at: '2026-11-02T10:00:00Z' }
  const state = await session(page, { role: 'admin', username: 'admin', handle: ({ path }) => {
    if (path === '/api/v1/admin/phpmyadmin') return pma
    if (path === '/api/v1/admin/server-setup') return { system: { panel_port: 2222, panel_hostname: 'panel.example.com', pma_hostname: '' }, dns: {}, current_step: 4, completed_steps: 3, step_results: {}, steps: Array.from({ length: 13 }, (_, i) => ({ number: i + 1, title: i === 3 ? 'Service hostnames' : i === 5 ? 'Certificates' : `Setup section ${i + 1}`, dependencies: [] })) }
  } })
  await page.goto('/app/server-setup')
  await expect(page.getByText('Installing phpMyAdmin for pma.example.com. It is published when the installation finishes.')).toBeVisible()
  await expect(page.getByPlaceholder('phpmyadmin.example.com')).toHaveValue('pma.example.com')
  pma = { ...pma, installed: true, published: true, hostname: 'pma.example.com', provisioning: 'published' }
  await expect(page.getByText('phpMyAdmin is published at https://pma.example.com.')).toBeVisible({ timeout: 12_000 })
  expect(state.requests.filter(item => item.path === '/api/v1/admin/phpmyadmin').length).toBeGreaterThan(1)
  await page.getByRole('navigation', { name: 'Setup steps' }).getByRole('button', { name: /Certificates/ }).click()
  await expect(page.getByText('phpMyAdmin', { exact: true })).toBeVisible()

  pma = { ...pma, published: false, hostname: null, provisioning: 'failed', last_stage: 'install', last_error: "E: Unable to locate package phpmyadmin" }
  await page.reload()
  await page.getByRole('navigation', { name: 'Setup steps' }).getByRole('button', { name: /Service hostnames/ }).click()
  await expect(page.getByText('phpMyAdmin could not be installed: E: Unable to locate package phpmyadmin. Save the hostname again to retry.')).toBeVisible()
  await page.getByRole('navigation', { name: 'Setup steps' }).getByRole('button', { name: /Certificates/ }).click()
  await expect(page.getByText('phpMyAdmin', { exact: true })).toHaveCount(0)
})

test('accounts a plan change could not update can be brought onto the plan again', async ({ page }) => {
  const plan = { id: 3, name: 'Business', cpu_pct: 100, cpu_cores: 1, mem_mb: 2048, io_mb: 100, pids_max: 200, quota_soft_mb: 25600, quota_hard_mb: 30720 }
  const state = await session(page, { role: 'admin', username: 'admin', handle: ({ method, path }) => {
    if (method === 'PATCH' && path === '/api/v1/admin/plans/3') return { ...plan, accounts_updated: 1, propagation_failures: [{ username: 'beta', error: 'A database backup or management operation is in progress. Try again shortly.' }] }
    if (method === 'POST' && path === '/api/v1/admin/accounts/beta/apply-plan/3') return { status: 'applied' }
    if (path === '/api/v1/admin/plans/3') return plan
    if (path === '/api/v1/admin/resources') return { host: { cpu_cores: 8, memory_gb: 16 } }
  } })
  await page.goto('/app/plans/3/edit')
  await page.getByLabel('Plan name').fill('Business plus')
  await page.getByRole('button', { name: 'Save plan' }).first().click()
  const failures = page.getByRole('alert', { name: 'Accounts not updated' })
  await expect(failures).toContainText('beta: A database backup or management operation is in progress. Try again shortly.')
  await failures.getByRole('button', { name: 'Re-apply plan' }).click()
  await expect(failures).toContainText('Every assigned account now uses the saved plan.')
  expect(writes(state, 'POST', '/apply-plan/3')).toHaveLength(1)
})
