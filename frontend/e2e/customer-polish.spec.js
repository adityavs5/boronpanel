import { test, expect } from '@playwright/test'

const domain = 'a-very-long-customer-domain-for-context.example.com'
const account = 'customer'
async function mockCustomer(page, skin, theme) {
  const writes = []
  await page.addInitScript(({skin, theme}) => {
    localStorage.setItem('boron.ui', JSON.stringify({state:{skin,theme},version:0}))
    localStorage.setItem('boron.auth', JSON.stringify({state:{role:'customer',username:'customer'},version:0}))
  }, {skin, theme})
  await page.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    let data = {}
    if (request.method() !== 'GET') writes.push({path, body:request.postDataJSON()})
    if (path.endsWith('/whoami')) data = {role:'customer',username:account,impersonating:true,impersonator:'administrator'}
    else if (path.endsWith('/onboarding')) data = {completed:true}
    else if (path.endsWith('/notification-prefs')) data = {customer_email:'owner@example.com',events:{}}
    else if (path.endsWith('/snapshots/runs')) data = {runs:[{id:42,status:'completed',started_at:'2026-10-18T08:00:00Z',snapshot_id:'test-snapshot',options:{components:['files','databases','mail','config']},summary:{total_bytes_processed:1024,data_added:512},progress_message:'Recovery point ready'}]}
    else if (path.endsWith('/snapshots/restores')) data = {restores:[{id:7,status:'completed',started_at:'2026-10-18T09:00:00Z',selection:{kind:'files'},safety_snapshot_id:'safety',progress_message:'Scheduled restore complete'}]}
    else if (path.endsWith('/backups/restores/list')) data = {restore_jobs:[{id:7,kind:'full',status:'completed',started_at:'2026-10-18T10:00:00Z',progress_message:'Archive restore complete'}]}
    else if (path.endsWith('/backups')) data = {jobs:[{id:42,kind:'full',status:'completed',started_at:'2026-10-18T07:00:00Z',size_bytes:2048,progress_message:'Archive ready'}]}
    else if (path.endsWith('/downloads')) data = {downloads:[]}
    else if (path.endsWith('/mailboxes')) data = request.method() === 'POST' ? {local_part:'hello',quota_mb:1536} : {mailboxes:[{local_part:'existing',quota_mb:1024,active:true}]}
    else if (path.endsWith('/domains')) data = {domains:[{domain,is_primary:true}]}
    else if (path.endsWith('/records')) data = {zone:domain,records:[],managed:true}
    else if (path.endsWith('/php-ini')) data = {php_versions:['8.3','8.4'],php_lifecycle:[{version:'8.3',eol_date:'2027-12-31'}],directives:[],extensions:[]}
    else if (path.includes('/php-ini/effective/')) data = {php_version:'8.3',values:{}}
    else if (path === `/api/v1/accounts/${account}`) data = {username:account,php_version:'8.3'}
    else if (path === '/api/v1/wordpress') data = {installs:[{id:1,username:account,domain,path:'',url:`https://${domain}`,wp_version:'6.8',admin_user:'admin'}],domains:[{domain,username:account}],errors:[]}
    else if (path.endsWith('/databases')) data = {databases:[]}
    else if (path.endsWith('/usage')) data = {current:{}}
    await route.fulfill({json:data})
  })
  return writes
}

for (const skin of ['evolution', 'paper-lantern']) for (const theme of ['light', 'dark']) {
  test(`${skin} ${theme}: domain context, mailbox policy and compact tools`, async ({page}, info) => {
    test.setTimeout(90_000)
    const writes = await mockCustomer(page, skin, theme)
    await page.setViewportSize({width:375,height:900})
    await page.goto('/app/dns')
    await expect(page.locator('.customer-domain-context')).toHaveText(domain)
    await expect(page.locator('.customer-domain-context')).not.toHaveAttribute('aria-label')
    expect(await page.locator('.customer-domain-context').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
    expect((await page.getByRole('combobox',{name:'Domain',exact:true}).boundingBox()).width).toBeGreaterThan(280)
    await page.screenshot({path:info.outputPath('dns-mobile.png'),fullPage:true})
    await page.goto('/app/email')
    await expect(page.locator('.customer-domain-context')).toHaveText(domain)
    await expect(page.locator('.customer-domain-context')).not.toHaveAttribute('aria-label')
    await expect(page.locator('.page-heading-actions').getByRole('button',{name:'Create mailbox',exact:true})).toBeVisible()
    await page.screenshot({path:info.outputPath('email-mobile.png'),fullPage:true})
    await page.getByRole('button',{name:'Create mailbox',exact:true}).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toHaveAccessibleDescription('Choose an address, password, and storage limit for the new mailbox.')
    await expect(dialog.getByText('Minimum 12 characters.')).toBeVisible()
    await dialog.getByLabel(/^Mailbox/).fill('hello')
    await dialog.getByLabel(/^Password/).fill('ShortPass1!')
    await expect(dialog.getByRole('button',{name:'Create mailbox',exact:true})).toBeDisabled()
    const syntheticPassword = 'x'.repeat(12) + 'A1!'
    await dialog.getByLabel(/^Password/).fill(syntheticPassword)
    await dialog.getByLabel('Quota (GB)',{exact:true}).fill('1.5')
    await dialog.getByRole('button',{name:'Create mailbox',exact:true}).click()
    await expect(dialog).not.toBeVisible()
    expect(writes.at(-1).body).toEqual({domain,local_part:'hello',password:syntheticPassword,quota_mb:1536})
    await page.getByRole('button',{name:`Actions for existing@${domain}`}).click()
    await page.getByRole('menuitem',{name:'Manage',exact:true}).click()
    await expect(page.getByRole('dialog')).toHaveAccessibleDescription('Update this mailbox’s storage limit or password.')
    await expect(page.getByRole('spinbutton',{name:'Mailbox quota (GB)',exact:true})).toHaveValue('1')
    await page.getByRole('button',{name:'Done',exact:true}).click()
    await page.goto('/app/ssh')
    await expect(page.getByRole('button',{name:'Add SSH key',exact:true})).toBeDisabled()
    await page.goto('/app/wordpress')
    await expect(page.locator('.wp-site')).toHaveCount(1)
    await expect(page.getByText('Your websites. Simply managed.')).toHaveCount(0)
    for (const width of [375,768,1440]) {
      await page.setViewportSize({width,height:900})
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      await expect(page.getByRole('button',{name:'Manage website',exact:true})).toBeVisible()
      await page.screenshot({path:info.outputPath(`wordpress-${width}.png`),fullPage:true})
    }
    await page.goto('/app/php')
    const cards = page.locator('.customer-php-versions > .panel-card')
    await expect(cards).toHaveCount(2)
    const first = await cards.nth(0).boundingBox(), second = await cards.nth(1).boundingBox()
    expect(second.y).toBeGreaterThan(first.y + first.height)
    expect((await page.getByRole('combobox',{name:'PHP version',exact:true}).boundingBox()).width).toBeGreaterThan(250)
    await page.screenshot({path:info.outputPath('php-desktop.png'),fullPage:true})
    await page.goto('/app/databases')
    const titleSize = await page.getByRole('heading',{name:'Databases',exact:true}).evaluate(el => parseFloat(getComputedStyle(el).fontSize))
    const sectionSize = await page.getByRole('heading',{name:'Current Databases',exact:true}).evaluate(el => parseFloat(getComputedStyle(el).fontSize))
    expect(sectionSize).toBeLessThan(titleSize)
    await page.goto('/app/cache')
    await expect(page.getByRole('switch',{name:'Enable full-page cache',exact:true})).toBeVisible()
  })

  test(`${skin} ${theme}: combined backup inventory and restore actions`, async ({page}, info) => {
    const writes = await mockCustomer(page, skin, theme)
    await page.goto('/app/backups')
    const table = page.getByRole('table')
    await expect(table).toHaveCount(1)
    await expect(table.getByText('Backup #42', {exact:true})).toBeVisible()
    await expect(table.getByText('Archive #42', {exact:true})).toBeVisible()
    await page.getByRole('combobox',{name:'Backup type',exact:true}).selectOption('snapshot')
    await expect(table.getByText('Archive #42',{exact:true})).toHaveCount(0)
    await page.getByRole('button',{name:'View details',exact:true}).click()
    await expect(page.getByRole('button',{name:'Restore full account',exact:true})).toBeVisible()
    await expect(page.getByRole('button',{name:'Browse backed-up files',exact:true})).toBeVisible()
    await expect(page.getByRole('button',{name:'Prepare download',exact:true})).toBeVisible()
    await page.getByRole('button',{name:'Done',exact:true}).click()
    await page.getByRole('combobox',{name:'Backup type',exact:true}).selectOption('archive')
    await page.getByRole('button',{name:'Restore',exact:true}).click()
    await expect(page.getByRole('dialog')).toBeVisible()
    await page.getByRole('button',{name:'Cancel',exact:true}).click()
    await page.getByRole('tab',{name:'Restore history',exact:true}).click()
    await expect(page.getByRole('table')).toHaveCount(1)
    await expect(page.getByText('Scheduled restore complete',{exact:true}).filter({visible:true})).toBeVisible()
    await expect(page.getByText('Archive restore complete',{exact:true}).filter({visible:true})).toBeVisible()
    await page.getByRole('button',{name:'Recover previous files',exact:true}).click()
    await expect(page.getByRole('dialog')).toBeVisible()
    await page.getByRole('button',{name:'Cancel',exact:true}).click()
    await page.getByRole('tab',{name:'Email notifications',exact:true}).click()
    await page.getByRole('textbox',{name:'Notification email',exact:true}).fill('draft@example.com')
    await page.getByRole('tab',{name:'Backups',exact:true}).click()
    await page.getByRole('tab',{name:'Email notifications',exact:true}).click()
    await expect(page.getByRole('textbox',{name:'Notification email',exact:true})).toHaveValue('draft@example.com')
    await page.getByRole('tab',{name:'Backups',exact:true}).click()
    await page.getByRole('combobox',{name:'Backup type',exact:true}).selectOption('all')
    for (const width of [1440,768,375]) {
      await page.setViewportSize({width,height:900})
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      await page.screenshot({path:info.outputPath(`backups-${width}.png`),fullPage:true})
    }
    expect(writes).toEqual([])
  })
}
