import { test, expect } from '@playwright/test'

// Round 2 customer follow-ups: DNS editor, domain page (nameservers,
// wildcard, .htaccess), domain list, redirects and application start
// failures, against the API shapes in api/routers and the daemon.
const combos = [['modern', 'light'], ['classic', 'dark']]
const fits = (page) => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)

async function customer(page, { skin, mode }, handler, username = 'alpha') {
  await page.addInitScript(({ skin, mode, username }) => {
    localStorage.setItem('boron.public-branding.v1',JSON.stringify({panel_name:'Boron',theme:skin}));localStorage.setItem('boron.ui', JSON.stringify({ state: { skin, theme: mode }, version: 0 }))
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

const zone = (name, records) => ({ zone: name, managed: true, provider: 'local', proxy_available: false, records })

for (const [skin, mode] of combos) {
  test(`${skin} ${mode}: the apex NS set is left to the Nameservers section`, async ({ page }, info) => {
    let saves = 0
    const calls = await customer(page, { skin, mode }, ({ method, path }) => {
      if (path.endsWith('/dns/zones/example.com/records') && method === 'PUT') {
        saves++
        return { status: 400, json: { detail: 'mx.example.com MX: priority must be between 0 and 65535' } }
      }
      if (path.endsWith('/dns/zones/example.com/records')) return zone('example.com', [
        { name: 'example.com.', type: 'NS', ttl: 3600, values: ['ns1.example.com.', 'ns2.example.com.'] },
        { name: 'www.example.com.', type: 'A', ttl: 3600, values: ['192.0.2.10'] },
      ])
      if (path.endsWith('/domains')) return { domains: [{ domain: 'example.com', kind: 'primary' }] }
      return {}
    })
    await page.goto('/app/dns')
    const nsLink = page.getByRole('link', { name: 'Manage the nameservers of example.com' })
    await expect(nsLink).toHaveAttribute('href', '/app/domains/example.com#nameservers')
    await expect(page.getByRole('button', { name: 'Edit NS record @' })).toHaveCount(0)

    await page.getByRole('button', { name: 'Add record' }).click()
    const dialog = page.getByRole('dialog')
    const type = dialog.getByLabel('Type', { exact: true })
    await expect(type.locator('option', { hasText: /^NS$/ })).toHaveCount(0)
    await dialog.getByLabel('DNS record name').fill('lab')
    await type.selectOption('NS')
    await dialog.getByLabel('DNS record name').fill('@')
    await expect(dialog.getByRole('status').filter({ hasText: "The zone's own NS records are managed on the" })).toBeVisible()
    await expect(dialog.getByRole('link', { name: 'Nameservers page' })).toHaveAttribute('href', '/app/domains/example.com#nameservers')
    await expect(dialog.getByRole('button', { name: 'Save record' })).toBeDisabled()
    await page.screenshot({ path: info.outputPath(`${skin}-${mode}-apex-ns.png`) })

    await dialog.getByLabel('DNS record name').fill('mx')
    await type.selectOption('MX')
    await dialog.getByLabel(/^Value\(s\)/).fill('70000 mail.example.com')
    await dialog.getByRole('button', { name: 'Save record' }).click()
    await expect(dialog.getByRole('alert')).toHaveText('mx.example.com MX: priority must be between 0 and 65535')
    expect(saves).toBe(1)
    expect(calls.find((call) => call.method === 'PUT').body).toMatchObject({ subdomain: 'mx', type: 'MX', values: ['70000 mail.example.com'], mode: 'add' })
    await page.setViewportSize({ width: 375, height: 812 })
    expect(await fits(page)).toBe(true)
  })

  test(`${skin} ${mode}: a hosted name without its own zone links to its parent zone`, async ({ page }) => {
    await customer(page, { skin, mode }, ({ path }) => {
      if (path.endsWith('/dns/zones/blog.example.com/records')) return { zone: 'blog.example.com', managed: false, parent_zone: 'example.com', records: [], provider: null, cloudflare: null }
      if (path.endsWith('/dns/zones/example.com/records')) return zone('example.com', [
        { name: 'blog.example.com.', type: 'A', ttl: 3600, values: ['192.0.2.20'] },
        { name: 'www.example.com.', type: 'A', ttl: 3600, values: ['192.0.2.10'] },
      ])
      if (path.endsWith('/domains')) return { domains: [{ domain: 'example.com', kind: 'primary' }, { domain: 'blog.example.com', kind: 'subdomain' }] }
      return {}
    })
    await page.goto('/app/dns')
    const picker = page.getByRole('combobox', { name: 'Domain', exact: true })
    await picker.selectOption('blog.example.com')
    await expect(page.getByRole('heading', { name: 'Managed under a different zone' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Add record' })).toBeDisabled()
    await page.getByRole('button', { name: 'Edit blog in example.com' }).click()
    await expect(picker).toHaveValue('example.com')
    await expect(page.getByRole('status').filter({ hasText: 'Showing the records named blog' })).toBeVisible()
    await expect(page.getByRole('table').getByText('www', { exact: true })).toHaveCount(0)
    await expect(page.getByRole('table').getByRole('button', { name: 'Edit A record blog' })).toBeVisible()
    await page.getByRole('button', { name: 'Add record' }).click()
    await expect(page.getByRole('dialog').getByLabel('DNS record name')).toHaveValue('blog')
  })

  test(`${skin} ${mode}: zone editor errors stay in the editor`, async ({ page }) => {
    const fingerprint = 'a'.repeat(64)
    await customer(page, { skin, mode }, ({ path }) => {
      if (path.endsWith('/advanced/preview')) return { status: 400, json: { detail: 'mx.example.com MX: an IP address is not a valid MX target' } }
      if (path.endsWith('/advanced')) return { zone: 'example.com', text: '$ORIGIN example.com.\nmx 3600 IN MX 10 192.0.2.1\n', fingerprint, templates: [{ id: 'hosting', name: 'Website, email and FTP records' }] }
      if (path.endsWith('/records')) return zone('example.com', [])
      if (path.endsWith('/domains')) return { domains: [{ domain: 'example.com', kind: 'primary' }] }
      return {}
    })
    await page.goto('/app/dns')
    await page.getByRole('button', { name: 'Zone editor and templates' }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByRole('button', { name: 'Preview changes' }).click()
    await expect(dialog.getByRole('alert')).toHaveText('mx.example.com MX: an IP address is not a valid MX target')
  })

  test(`${skin} ${mode}: the domain page shows .htaccess rules, glue changes and wildcard conflicts`, async ({ page }, info) => {
    let nameservers = ['ns1.example.com', 'ns2.example.com']
    const calls = await customer(page, { skin, mode }, ({ method, path, body }) => {
      if (path.endsWith('/htaccess-status')) return {
        domain: 'example.com',
        access_rules: [
          { directory: '/private', mode: 'deny', allow: [], deny: [] },
          { directory: '/admin', mode: 'ip', allow: ['203.0.113.5'], deny: ['*'] },
        ],
        unsupported_access_directives: [{ path: 'images/.htaccess', line: 3, directive: '<FilesMatch "\\.(jpg|png)$">' }],
        access_scan_complete: false,
      }
      if (path.endsWith('/reload-htaccess')) return { domain: 'example.com', reloaded: true, config_valid: true, access_rules: [], unsupported_access_directives: [], access_scan_complete: true }
      if (path.endsWith('/nameservers') && method === 'PUT') {
        nameservers = body.nameservers
        return { domain: 'example.com', nameservers, glue: {}, removed_glue: ['ns1.example.com A', 'ns2.example.com A'], kept_glue: [{ name: 'mail.example.com', types: ['A'], reason: 'example.com MX points at it' }] }
      }
      if (path.endsWith('/nameservers')) return { domain: 'example.com', provider: 'local', nameservers, glue: Object.fromEntries(nameservers.filter((ns) => ns.endsWith('.example.com')).map((ns) => [ns, { a: ['192.0.2.1'], aaaa: [] }])) }
      if (path.endsWith('/wildcard')) return {
        domain: 'example.com', enabled: true, zone_managed: true, dns_record_created: false, ssl_is_wildcard: false, existing_subdomains: [],
        dns_conflict: '*.example.com already has its own DNS record (A 198.51.100.7); it was kept. Remove or change it on the DNS page if wildcard names should reach this server.',
      }
      if (path.endsWith('/dns/zones/example.com/records')) return zone('example.com', [])
      if (path.endsWith('/domains')) return { domains: [{ domain: 'example.com', kind: 'primary', docroot: '/home/alpha/public_html' }] }
      return {}
    })
    await page.goto('/app/domains/example.com#nameservers')
    await expect(page.locator('#nameservers')).toBeFocused()
    await expect(page.getByRole('tab')).toHaveCount(0)

    const protectedRules = page.getByRole('region', { name: 'Protected folders' })
    await expect(protectedRules.getByText('/private', { exact: true })).toBeVisible()
    await expect(protectedRules.getByText('Blocked for every visitor')).toBeVisible()
    await expect(protectedRules.getByText('Only 203.0.113.5 can visit')).toBeVisible()
    await expect(page.getByRole('status').filter({ hasText: 'Directives the server cannot apply' })).toContainText('images/.htaccess, line 3')
    await expect(page.getByRole('status').filter({ hasText: 'The check did not reach every folder' })).toBeVisible()
    await protectedRules.scrollIntoViewIfNeeded()
    await page.screenshot({ path: info.outputPath(`${skin}-${mode}-domain-htaccess.png`) })
    await page.getByRole('button', { name: 'Reload .htaccess' }).click()
    await expect(page.getByText('No folder of this website restricts visitors through .htaccess', { exact: false })).toBeVisible()

    await expect(page.getByRole('status').filter({ hasText: 'Your own * DNS record was kept' })).toContainText('*.example.com already has its own DNS record (A 198.51.100.7); it was kept.')

    await page.getByLabel(/^Nameserver hostnames/).fill('ns1.example.net\nns2.example.net')
    await page.getByRole('button', { name: 'Save nameservers' }).click()
    const glue = page.getByRole('status').filter({ hasText: 'Removed glue records' })
    await expect(glue).toContainText('ns1.example.com A')
    await expect(glue).toContainText('mail.example.com A: example.com MX points at it')
    await expect(page.getByLabel(/^Nameserver hostnames/)).toHaveValue('ns1.example.net\nns2.example.net')
    expect(calls.find((call) => call.method === 'PUT').body).toMatchObject({ nameservers: ['ns1.example.net', 'ns2.example.net'] })
    await glue.scrollIntoViewIfNeeded()
    await page.screenshot({ path: info.outputPath(`${skin}-${mode}-domain-glue.png`) })
    await page.getByRole('button', { name: 'Reset to defaults' }).click()
    await expect(page.getByRole('dialog')).toContainText("This restores this server's default nameservers for example.com.")
    await page.setViewportSize({ width: 375, height: 812 })
    expect(await fits(page)).toBe(true)
  })

  test(`${skin} ${mode}: domain list reload, removal warnings and add errors`, async ({ page }) => {
    let tasks = 0
    await customer(page, { skin, mode }, ({ method, path }) => {
      if (path.endsWith('/reload-htaccess')) return {
        domain: 'example.com', reloaded: true, config_valid: true, access_scan_complete: true,
        access_rules: [{ directory: '/private', mode: 'deny', allow: [], deny: [] }],
        unsupported_access_directives: [{ path: '.htaccess', line: 7, directive: 'Satisfy any' }],
      }
      if (path.endsWith('/domains/shop.example.net') && method === 'DELETE') return { domain: 'shop.example.net', kind: 'addon', status: 'removed', cleanup_failed: ['DNS records', 'LSCache settings'] }
      if (path.endsWith('/task-operations/accounts/alpha/domains')) {
        tasks++
        return tasks === 1 ? { status: 400, json: { detail: "'panel.example.org' is reserved for this server" } } : { id: 9, status: 'pending', progress_message: 'Adding domain' }
      }
      if (path.endsWith('/task-operations/9')) return { id: 9, status: 'failed', progress_message: "domain 'taken.example.org' is already in use", error_reference: '0123456789abcdef' }
      if (path.endsWith('/parked-domains/alias.example.org') && method === 'DELETE') return { parked_domain: 'alias.example.org', status: 'removed', cleanup_failed: ['DNS records'] }
      if (path.endsWith('/parked-domains') && method === 'POST') return { status: 409, json: { detail: "domain 'other.example.org' already belongs to another account" } }
      if (path.endsWith('/parked-domains')) return { parked_domains: [{ parked_domain: 'alias.example.org', target_domain: 'example.com', effective_suspended: false, ssl_status: 'none' }] }
      if (path.endsWith('/domains')) return { domains: [{ domain: 'example.com', kind: 'primary' }, { domain: 'shop.example.net', kind: 'addon' }] }
      return {}
    })
    await page.goto('/app/domains')
    await page.getByRole('button', { name: 'Actions for example.com' }).click()
    await page.getByRole('menuitem', { name: 'Reload .htaccess' }).click()
    const report = page.getByRole('dialog', { name: '.htaccess for example.com' })
    await expect(report.getByText('/private', { exact: true })).toBeVisible()
    await expect(report.getByRole('status').filter({ hasText: 'Directives the server cannot apply' })).toContainText('Satisfy any')
    await expect(page.getByText('.htaccess reloaded with warnings', { exact: true })).toBeVisible()
    await page.setViewportSize({ width: 375, height: 812 })
    expect(await fits(page)).toBe(true)
    await page.setViewportSize({ width: 1440, height: 1000 })
    await report.getByRole('button', { name: 'Done' }).click()

    await page.getByRole('button', { name: 'Actions for shop.example.net' }).click()
    await page.getByRole('menuitem', { name: 'Delete' }).click()
    const confirm = page.getByRole('dialog')
    await confirm.getByRole('textbox').fill('shop.example.net')
    await confirm.getByRole('button', { name: 'Remove domain' }).click()
    await expect(page.getByText('shop.example.net was removed', { exact: true })).toBeVisible()
    await expect(page.getByText('These could not be cleaned up: DNS records, LSCache settings. Ask your administrator to remove them.', { exact: true })).toBeVisible()

    await page.getByRole('button', { name: 'Actions for alias.example.org' }).click()
    await page.getByRole('menuitem', { name: 'Remove' }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Remove' }).click()
    await expect(page.getByText('alias.example.org was removed', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Park a domain' }).click()
    const park = page.getByRole('dialog')
    await park.getByRole('textbox', { name: /^Domain to park/ }).fill('other.example.org')
    await park.getByRole('button', { name: 'Park domain' }).click()
    await expect(park.getByRole('alert')).toHaveText("domain 'other.example.org' already belongs to another account")
    await park.getByRole('button', { name: 'Cancel' }).click()

    await page.getByRole('button', { name: 'Add domain', exact: true }).first().click()
    const add = page.getByRole('dialog')
    await add.getByRole('textbox', { name: /^Domain/ }).fill('panel.example.org')
    await add.getByRole('button', { name: 'Add domain' }).click()
    await expect(add.getByRole('alert')).toContainText("'panel.example.org' is reserved for this server")
    await add.getByRole('textbox', { name: /^Domain/ }).fill('taken.example.org')
    await add.getByRole('button', { name: 'Add domain' }).click()
    await expect(add.getByRole('alert')).toContainText("domain 'taken.example.org' is already in use")
    await expect(add.getByRole('alert')).toContainText('0123456789abcdef')
  })

  test(`${skin} ${mode}: adding an existing redirect path offers to edit it`, async ({ page }, info) => {
    const calls = await customer(page, { skin, mode }, ({ method, path }) => {
      if (path.endsWith('/redirects') && method === 'POST') return { status: 409, json: { detail: "a redirect for path '/old' already exists on 'example.com'; edit it instead" } }
      if (path.endsWith('/redirects') && method === 'PUT') return { domain: 'example.com', path: '/old', target_url: 'https://example.com/newer', status_code: 301 }
      if (path.endsWith('/redirects')) return { redirects: [{ id: 1, domain: 'example.com', path: '/old', target_url: 'https://example.com/new', status_code: 301 }] }
      if (path.endsWith('/forwarding')) return { forwarding: null }
      if (path.endsWith('/domains')) return { domains: [{ domain: 'example.com', kind: 'primary' }] }
      return {}
    })
    await page.goto('/app/redirects')
    await page.getByRole('button', { name: 'Add redirect' }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByText('encoded characters such as %20 cannot be used', { exact: false })).toBeVisible()
    await dialog.getByLabel(/^Path/).fill('/old')
    await dialog.getByLabel(/^Target URL/).fill('https://example.com/a%20b')
    await expect(dialog.getByRole('alert')).toContainText('rewrite variables')
    await expect(dialog.getByRole('button', { name: 'Save redirect' })).toBeDisabled()
    await dialog.getByLabel(/^Target URL/).fill('https://example.com/newer')
    await dialog.getByRole('button', { name: 'Save redirect' }).click()
    const notice = dialog.getByRole('status').filter({ hasText: '/old already has a redirect' })
    await expect(notice).toContainText('https://example.com/new')
    await page.screenshot({ path: info.outputPath(`${skin}-${mode}-redirect-conflict.png`) })
    await notice.getByRole('button', { name: 'Edit the existing redirect' }).click()
    await expect(dialog.getByRole('heading', { name: 'Edit redirect' })).toBeVisible()
    await dialog.getByRole('button', { name: 'Save redirect' }).click()
    await expect(dialog).not.toBeVisible()
    expect(calls.find((call) => call.method === 'PUT').body).toEqual({ path: '/old', target_url: 'https://example.com/newer', status_code: 301 })
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(1)

    await page.goto('/app/forwarding')
    await page.getByLabel(/^Destination URL/).fill('https://example.org/$1')
    await expect(page.getByRole('alert')).toContainText('rewrite variables')
    await expect(page.getByRole('button', { name: 'Enable forwarding' })).toBeDisabled()
  })

  test(`${skin} ${mode}: an app that does not start shows its log lines`, async ({ page }, info) => {
    let lists = 0, running = false
    const failure = 'The application did not start (service state: failed, restarts: 3). Last log lines: Traceback (most recent call last): | File "/home/alpha/apps/api/app.py", line 1, in <module> | ModuleNotFoundError: No module named \'flask\''
    const calls = await customer(page, { skin, mode }, ({ method, path, body }) => {
      if (path.endsWith('/apps/python/3/start')) return { status: 400, json: { detail: failure }, headers: { 'x-boron-error-reference': 'fedcba9876543210' } }
      if (path.endsWith('/apps/python/3') && method === 'PATCH') {
        return body.env_vars?.MODE === 'broken'
          ? { status: 400, json: { detail: "env var name '1MODE' is invalid (letters, digits, underscore, must not start with a digit)" } }
          : { status: 400, json: { detail: failure } }
      }
      if (path.endsWith('/apps/python')) {
        lists++
        return { apps: [{ id: 3, name: 'api', domain: 'example.com', entry_point: 'app:app', app_type: 'wsgi', env_vars: { MODE: 'prod' }, enabled: running, active: running ? 'active' : 'inactive' }] }
      }
      if (path.endsWith('/domains')) return { domains: [{ domain: 'example.com', kind: 'primary' }] }
      return {}
    })
    await page.goto('/app/python-apps')
    await page.getByRole('button', { name: 'Actions for api' }).click()
    await page.getByRole('menuitem', { name: 'Start', exact: true }).click()
    const failed = page.getByRole('dialog', { name: 'api did not start' })
    await expect(failed).toBeVisible()
    await expect(failed.getByLabel('Last log lines')).toContainText('Traceback (most recent call last):')
    await expect(failed.getByLabel('Last log lines')).toContainText("ModuleNotFoundError: No module named 'flask'")
    await expect(failed).toContainText('service state: failed, restarts: 3')
    await expect(failed).toContainText('fedcba9876543210')
    await expect.poll(() => lists).toBeGreaterThan(1)
    await page.screenshot({ path: info.outputPath(`${skin}-${mode}-app-failure.png`) })
    await page.setViewportSize({ width: 375, height: 812 })
    expect(await fits(page)).toBe(true)
    await page.setViewportSize({ width: 1440, height: 1000 })
    await failed.getByRole('button', { name: 'Done', exact: true }).click()

    running = true
    await page.reload()
    await page.getByRole('button', { name: 'Manage application api' }).click()
    const manage = page.getByRole('dialog', { name: 'Manage api' })
    await expect(manage.getByText('Saving restarts the app with the new settings.')).toBeVisible()
    await expect(manage.getByLabel('Environment variables')).toHaveValue('MODE=prod')
    await page.setViewportSize({ width: 375, height: 812 })
    expect(await fits(page)).toBe(true)
    await page.setViewportSize({ width: 1440, height: 1000 })
    await manage.getByLabel('Environment variables').fill('MODE=debug')
    await manage.getByRole('button', { name: 'Save settings' }).click()
    await expect(page.getByRole('dialog', { name: 'api did not start' })).toContainText('The settings were saved, but the app stopped right after the restart.')
    expect(calls.find((call) => call.method === 'PATCH').body).toEqual({ env_vars: { MODE: 'debug' } })
    await page.getByRole('dialog', { name: 'api did not start' }).getByRole('button', { name: 'Done', exact: true }).click()
    await manage.getByLabel('Environment variables').fill('MODE=broken')
    await manage.getByRole('button', { name: 'Save settings' }).click()
    await expect(page.getByText("env var name '1MODE' is invalid (letters, digits, underscore, must not start with a digit)", { exact: true })).toBeVisible()
    await expect(page.getByRole('dialog', { name: 'api did not start' })).toHaveCount(0)
  })
}
