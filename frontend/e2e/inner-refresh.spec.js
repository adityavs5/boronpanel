import { test, expect } from '@playwright/test'

// Each case has isolated browser storage and API fixtures; two workers can share this matrix safely.
test.describe.configure({mode:'parallel'})

async function fixture(page, {skin='evolution',mode='light',role='admin'}={}) {
  const writes=[]
  const account={id:1,username:'alpha',status:'active',uid:2000,gid:2000,primary_domain:'example.com',php_version:'8.3',email:'owner@example.com',cpu_cores:2,cpu_pct:200,host_cpu_cores:12,host_memory_gb:7.7,mem_mb:1024,io_mb:50,pids_max:100,quota_soft_mb:10240,quota_hard_mb:12288,plan_id:1}
  const domains=[{id:1,domain:'example.com',kind:'primary',docroot:'/home/alpha/public_html',php_version:'8.3',ssl_status:'issued',created_at:'2026-09-30'}, {id:2,domain:'blog.example.com',kind:'subdomain',docroot:'/home/alpha/example.com/subdomains/blog/very-long-folder-name/public_html',php_version:'8.3',ssl_status:'none',created_at:'2026-09-30'}]
  const config={admin_port:2222,customer_port:2222,hostname:'panel.example.com',jobs:[],telemetry:{enabled:false}}
  const usageLimits={bandwidth_limit_mb:102400,database_limit:10,email_account_limit:20,subdomain_limit:20,auto_suspend_at_100:false}
  await page.addInitScript(({skin,mode,role})=>{
    if(!localStorage.getItem('boron.ui'))localStorage.setItem('boron.ui',JSON.stringify({state:{skin,theme:mode},version:0}))
    localStorage.setItem('boron.auth',JSON.stringify({state:{role,username:role==='admin'?'admin':'alpha'},version:0}))
  },{skin,mode,role})
  await page.route('**/api/**',async route=>{
    const request=route.request(),path=new URL(request.url()).pathname,method=request.method()
    if(!['GET','HEAD'].includes(method))writes.push({path,method,body:request.postDataJSON()})
    let data={}
    if(path.endsWith('/whoami'))data={role,username:role==='admin'?'admin':'alpha'}
    else if(path.endsWith('/branding'))data={panel_name:'Boron'}
    else if(path.endsWith('/onboarding'))data={completed:true}
    else if(path==='/api/v1/accounts')data=[account]
    else if(path==='/api/v1/accounts/alpha')data=account
    else if(path==='/api/v1/admin/plans')data={plans:[{id:1,name:'Starter',cpu_pct:200,mem_mb:1024}]}
    else if(path==='/api/v1/admin/panel-config')data=config
    else if(path==='/api/v1/admin/panel-config/certificate')data={hostname:config.hostname,certificate_present:true,valid_for_hostname:true,expires_at:'2026-12-30',days_remaining:90}
    else if(path==='/api/v1/admin/panel-config/ports'){Object.assign(config,request.postDataJSON());data={id:2,status:'completed',...config}}
    else if(path==='/api/v1/admin/panel-config/hostname'){Object.assign(config,request.postDataJSON());data=config}
    else if(path.endsWith('/domains'))data={domains}
    else if(path.endsWith('/parked-domains'))data={parked_domains:[]}
    else if(path.endsWith('/namespace'))data={enabled:true,status:'verified'}
    else if(path.endsWith('/usage-limits')){if(method==='PATCH')Object.assign(usageLimits,request.postDataJSON());data=usageLimits}
    else if(path.endsWith('/limits')){Object.assign(account,request.postDataJSON());account.cpu_cores=account.cpu_pct/100;data=account}
    else if(path.endsWith('/usage'))data={current:{disk_total_bytes:1073741824,inode_count:10000},bandwidth_month_to_date_bytes:2147483648,resources:{sampled_at:new Date().toISOString(),cpu_usage_usec:2000000,cpu_limit_cores:2,memory_current_bytes:268435456,memory_limit_bytes:1073741824,bandwidth_limit_bytes:107374182400}}
    else if(path.endsWith('/alerts'))data={active:[]}
    else if(path.endsWith('/php-functions'))data={default_disable_functions:['exec'],domain_overrides:[]}
    else if(path.endsWith('/suspension')){const row=domains.find(d=>path.includes(`/domains/${d.domain}/`));if(row)Object.assign(row,request.postDataJSON());data=row||{}}
    await route.fulfill({json:data})
  })
  return {writes,account,domains,config}
}

async function noOverflow(page) {
  const measured=await page.evaluate(()=>({document:document.documentElement.scrollWidth,viewport:innerWidth,main:document.querySelector('main').scrollWidth,content:document.querySelector('main').clientWidth}))
  expect(measured.document).toBeLessThanOrEqual(measured.viewport)
  expect(measured.main).toBeLessThanOrEqual(measured.content)
}
async function sampledContrast(page) {
  return page.evaluate(()=>{
    const parse=v=>(v.match(/[\d.]+/g)||[]).map(Number)
    const over=(fg,bg)=>{const alpha=fg[3]??1;return fg.slice(0,3).map((v,i)=>v*alpha+bg[i]*(1-alpha))}
    const light=rgb=>rgb.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0)
    const background=el=>{if(!el)return[255,255,255];const bg=parse(getComputedStyle(el).backgroundColor);return over(bg,background(el.parentElement))}
    const nodes=[...document.querySelectorAll('.sidebar-tool,.sidebar-section,.setting-row-label,.setting-row-description,.account-facts strong,.account-meter strong,[data-ui-badge],.danger-zone h2,.danger-zone p,.panel-page-heading h1,.settings-subnav button[aria-selected=true]')]
    return nodes.filter(el=>el.getClientRects().length&&getComputedStyle(el).visibility!=='hidden').map(el=>{const fg=over(parse(getComputedStyle(el).color),background(el));const a=light(fg),b=light(background(el));return {text:el.textContent.trim().slice(0,65),ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)}})
  })
}
for(const skin of ['evolution','paper-lantern'])for(const mode of ['light','dark'])for(const width of [1280,768,375])for(const reference of ['account','settings','domains']){
 test(`${skin} ${mode} ${width}: ${reference} reference layout`,async({page},info)=>{
  const errors=[];page.on('pageerror',e=>errors.push(e.message))
  await page.setViewportSize({width,height:960})
  await fixture(page,{skin,mode,role:reference==='domains'?'customer':'admin'})
  await page.goto(reference==='account'?'/app/accounts/alpha':reference==='settings'?'/app/panel-settings':'/app/domains')
  await expect(page.locator('main h1').first()).toBeVisible()
  if(reference==='account')await expect(page.getByLabel('CPU cores',{exact:true})).toHaveValue('2')
  if(reference==='domains')await expect(page.getByRole('button',{name:'Manage',exact:true}).first()).toBeVisible()
  if(reference==='domains'&&width>=640){
    const row=page.locator('.domains-reference-table tbody tr').first()
    expect((await row.boundingBox()).height).toBeLessThanOrEqual(44)
    await expect(page.locator('.domains-reference-table th').filter({hasText:'Website'})).toHaveCSS('white-space','nowrap')
  }
  await noOverflow(page)
  const header=await page.locator('header').evaluate(el=>{const brand=el.querySelector('.panel-brand').getBoundingClientRect(),search=el.querySelector('.global-search-trigger').getBoundingClientRect();return {brandRight:brand.right,searchLeft:search.left,searchWidth:search.width,searchRight:search.right}})
  expect(header.searchWidth).toBeGreaterThanOrEqual(30)
  expect(header.searchLeft).toBeGreaterThanOrEqual(header.brandRight)
  expect(header.searchRight).toBeLessThanOrEqual(width)
  if(width>=1024)await expect(page.getByRole('complementary',{name:'Tools sidebar'})).toBeVisible()
  else {await expect(page.getByRole('complementary',{name:'Tools sidebar'})).toBeHidden();await page.getByRole('button',{name:'Menu',exact:true}).click();await expect(page.getByRole('dialog')).toBeVisible();await expect(page.getByRole('complementary',{name:'Mobile tools'})).toBeVisible();await page.keyboard.press('Escape');await expect(page.getByRole('button',{name:'Menu',exact:true})).toBeFocused()}
  for(const sample of await sampledContrast(page))expect(sample.ratio,`${sample.text} contrast`).toBeGreaterThanOrEqual(4.5)
  await page.screenshot({path:info.outputPath(`${reference}-${skin}-${mode}-${width}.png`)})
  expect(errors).toEqual([])
 })
}

test('navigation persists, keyboard search uses synonyms and opens same-page settings',async({page})=>{
 await fixture(page)
 await page.goto('/app/panel-settings')
 await page.getByRole('button',{name:'Collapse sidebar',exact:true}).click()
 await page.reload()
 await expect(page.getByRole('button',{name:'Expand sidebar',exact:true})).toBeVisible()
 await page.getByRole('button',{name:'Expand sidebar',exact:true}).click()
 await page.keyboard.press('Control+k')
 await page.getByRole('combobox',{name:'Search tools and settings'}).fill('panel ports')
 await expect(page.getByRole('option',{name:'Panel access ports',exact:true})).toBeVisible()
 await page.keyboard.press('Escape')
 await page.locator('main').focus();await page.keyboard.press('/')
 await page.getByRole('combobox',{name:'Search tools and settings'}).fill('panel hostname')
 await page.keyboard.press('Enter')
 await expect(page).toHaveURL(/section=hostname/)
 await expect(page.getByLabel('Panel hostname',{exact:true})).toBeVisible()
 await page.getByRole('tab',{name:'Error telemetry',exact:true}).focus()
 await page.keyboard.press('Home')
 await expect(page.getByRole('tab',{name:'Panel access',exact:true})).toBeFocused()
})

test('settings validate, retain drafts, discard and send unchanged payloads',async({page})=>{
 const {writes}=await fixture(page)
 await page.goto('/app/panel-settings')
 const port=page.getByLabel('Administrator port',{exact:true})
 await port.fill('10');await port.blur()
 await expect(page.getByText('Enter a whole port number from 1024 to 65535.')).toBeVisible()
 await expect(page.getByRole('region',{name:'Unsaved changes'}).getByRole('button',{name:'Save',exact:true})).toBeDisabled()
 await port.fill('3333')
 await page.getByRole('checkbox',{name:'External firewall confirmed'}).check()
 await page.getByRole('link',{name:'Manage Accounts',exact:true}).first().click()
 await expect(page.getByRole('dialog',{name:'Discard unsaved changes?'})).toBeVisible()
 await page.getByRole('button',{name:'Keep editing',exact:true}).click()
 await expect(port).toHaveValue('3333')
 await page.getByRole('region',{name:'Unsaved changes'}).getByRole('button',{name:'Save',exact:true}).click()
 await expect(page.getByRole('region',{name:'Unsaved changes'})).toHaveCount(0)
 expect(writes).toEqual([{path:'/api/v1/admin/panel-config/ports',method:'POST',body:{admin_port:3333,customer_port:2222,confirm:true}}])
 await port.fill('4444')
 await page.getByRole('region',{name:'Unsaved changes'}).getByRole('button',{name:'Discard',exact:true}).click()
 await expect(port).toHaveValue('3333')
 await port.focus();await page.keyboard.type('/')
 await expect(page.getByRole('dialog',{name:'Search the panel'})).toHaveCount(0)
})

test('account summary, usage save, independent section save and typed danger confirmation',async({page})=>{
 const {writes}=await fixture(page)
 await page.goto('/app/accounts/alpha')
 const summary=page.getByRole('region',{name:'Account summary'})
 await expect(summary.getByRole('button',{name:'Suspend',exact:true})).toHaveCount(0)
 await expect(page.getByRole('button',{name:'Login as user',exact:true})).toBeVisible()
 await page.getByLabel('CPU cores',{exact:true}).fill('999')
 await page.getByLabel('CPU cores',{exact:true}).blur()
 await expect(page.getByText('Enter a core count from 0.01 to 12.')).toBeVisible()
 await expect(page.getByRole('button',{name:'Update limits',exact:true})).toBeDisabled()
 await page.getByLabel('CPU cores',{exact:true}).fill('3')
 await page.getByLabel('Bandwidth limit (GB/month)',{exact:true}).fill('200')
 const bar=page.getByRole('region',{name:'Unsaved changes'})
 await bar.getByRole('combobox',{name:'Section to save'}).click()
 await page.getByRole('option',{name:'Usage limits',exact:true}).click()
 await bar.getByRole('button',{name:'Save',exact:true}).click()
 await expect.poll(()=>writes.length).toBe(1)
 expect(writes[0]).toEqual({path:'/api/v1/accounts/alpha/usage-limits',method:'PATCH',body:{bandwidth_limit_mb:204800}})
 await bar.getByRole('button',{name:'Discard',exact:true}).click()
 await page.getByRole('button',{name:'Suspend',exact:true}).click()
 const dialog=page.getByRole('dialog',{name:'Suspend alpha?'})
 await expect(dialog.getByRole('button',{name:'Suspend account',exact:true})).toBeDisabled()
 await dialog.getByLabel('Type alpha to confirm',{exact:true}).fill('alpha')
 await expect(dialog.getByRole('button',{name:'Suspend account',exact:true})).toBeEnabled()
 await page.keyboard.press('Escape')
 expect(writes).toHaveLength(1)
})

test('domain columns, copy/expand and confirmed bulk suspension retain account scope',async({page})=>{
 const {writes}=await fixture(page,{role:'customer'})
 await page.goto('/app/domains')
 await page.locator('.panel-data-table').first().getByRole('button',{name:'Columns',exact:true}).click()
 await page.getByRole('menuitemcheckbox',{name:'Created',exact:true}).click()
 await page.keyboard.press('Escape')
 await expect(page.getByRole('columnheader',{name:'Created',exact:true})).toHaveCount(0)
 await page.context().grantPermissions(['clipboard-read','clipboard-write'])
 await page.getByRole('button',{name:'Copy blog.example.com document root',exact:true}).click()
 expect(await page.evaluate(()=>navigator.clipboard.readText())).toBe('/home/alpha/example.com/subdomains/blog/very-long-folder-name/public_html')
 await page.getByRole('button',{name:'Expand blog.example.com document root',exact:true}).click()
 await expect(page.getByRole('button',{name:'Collapse blog.example.com document root',exact:true})).toBeVisible()
 await page.getByRole('checkbox',{name:'Select blog.example.com',exact:true}).check()
 await page.getByRole('button',{name:'Suspend',exact:true}).click()
 const dialog=page.getByRole('dialog',{name:'Suspend selected domains?'})
 await dialog.getByLabel('Type alpha to confirm',{exact:true}).fill('alpha')
 await dialog.getByRole('button',{name:'Suspend domains',exact:true}).click()
 await expect(page.getByRole('region',{name:'Bulk domain results'})).toBeVisible()
 expect(writes).toEqual([{path:'/api/v1/accounts/alpha/domains/blog.example.com/suspension',method:'PATCH',body:{suspended:true,reason:''}}])
})

test('server errors use safe copy and the server-issued reference, failed drafts stay editable',async({page})=>{
 await fixture(page)
 await page.route('**/api/v1/admin/panel-config/hostname',route=>route.fulfill({status:502,headers:{'X-Boron-Error-Reference':'1234567890abcdef'},json:{detail:'internal operation failure: object of type Database'}}))
 await page.goto('/app/panel-settings?section=hostname')
 await page.getByLabel('Panel hostname',{exact:true}).fill('new.example.com')
 await page.getByRole('button',{name:'Save panel hostname',exact:true}).click()
 await expect(page.getByRole('region',{name:'Notifications (F8)'}).getByText(/server could not complete this operation/)).toBeVisible()
 await expect(page.getByRole('region',{name:'Notifications (F8)'}).getByText(/reference: 1234567890abcdef/)).toBeVisible()
 await expect(page.getByText(/object of type Database/)).toHaveCount(0)
 await expect(page.getByLabel('Panel hostname',{exact:true})).toHaveValue('new.example.com')
 await expect(page.getByRole('region',{name:'Unsaved changes'})).toBeVisible()
})
