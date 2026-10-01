import {test,expect} from '@playwright/test'
test.describe.configure({mode:'parallel'})

async function setup(page,skin,mode,role) {
 await page.addInitScript(({skin,mode,role})=>{localStorage.setItem('boron.ui',JSON.stringify({state:{skin,theme:mode},version:0}));localStorage.setItem('boron.auth',JSON.stringify({state:{role,username:role==='admin'?'admin':'alpha'},version:0}))},{skin,mode,role})
 const account={username:'alpha',uid:2000,status:'active',primary_domain:'example.com',php_version:'8.3',cpu_cores:2,cpu_pct:200,host_cpu_cores:12,host_memory_gb:8,mem_mb:1024,io_mb:50,pids_max:100,quota_hard_mb:10240}
 await page.route('**/api/**',route=>{
  const path=new URL(route.request().url()).pathname
  const data=path.endsWith('/whoami')?{role,username:role==='admin'?'admin':'alpha'}:path.endsWith('/branding')?{panel_name:'Boron'}:path.endsWith('/onboarding')?{completed:true}:path==='/api/v1/accounts'?[account]:path==='/api/v1/accounts/alpha'?account:path.endsWith('/domains')?{domains:[{domain:'example.com',kind:'primary',docroot:'/home/alpha/public_html',ssl_status:'issued'},{domain:'blog.example.com',kind:'subdomain',docroot:'/home/alpha/blog/public_html'}]}:path.endsWith('/parked-domains')?{parked_domains:[]}:path.endsWith('/panel-config')?{admin_port:2222,customer_port:2222,hostname:'panel.example.com',jobs:[]}:path.endsWith('/namespace')?{enabled:true}:path.endsWith('/usage')?{current:{},resources:{}}:path.endsWith('/plans')?{plans:[]}:{}
  return route.fulfill({json:data})
 })
}
async function contrast(page,selector,type='text') {
 return page.locator(selector).evaluateAll((nodes,type)=>{
  const parse=v=>(v.match(/[\d.]+/g)||[]).map(Number)
  const blend=(fg,bg)=>{const a=fg[3]??1;return fg.slice(0,3).map((v,i)=>v*a+bg[i]*(1-a))}
  const lum=rgb=>rgb.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0)
  const background=el=>el?blend(parse(getComputedStyle(el).backgroundColor),background(el.parentElement)):[255,255,255]
  return nodes.filter(el=>el.getClientRects().length&&!el.disabled&&getComputedStyle(el).opacity==='1').map(el=>{
   const style=getComputedStyle(el,type==='placeholder'?'::placeholder':null),bg=background(el),fg=blend(parse(type==='border'?style.borderTopColor:type==='focus'?style.outlineColor:style.color),bg),a=lum(fg),b=lum(bg)
   return {label:el.getAttribute('aria-label')||el.textContent.trim().slice(0,60)||el.placeholder,type,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)}
  })
 },type)
}
for(const skin of ['evolution','paper-lantern'])for(const mode of ['light','dark']){
 test(`${skin} ${mode}: enabled controls, placeholders, focus and table keyboard`,async({browser})=>{
  test.setTimeout(90000)
  for(const reference of ['account','settings','domains']){
   const context=await browser.newContext({viewport:{width:1280,height:960}}),page=await context.newPage()
   await setup(page,skin,mode,reference==='domains'?'customer':'admin')
   await page.goto(reference==='account'?'/app/accounts/alpha':reference==='settings'?'/app/panel-settings':'/app/domains')
   await expect(page.locator('main h1').first()).toBeVisible()
   await page.keyboard.press('Tab')
   await page.getByRole('button',{name:'Collapse sidebar',exact:true}).focus()
   for(const sample of await contrast(page,'[aria-label="Collapse sidebar"]','focus'))expect(sample.ratio,`${reference} ${sample.label}: focus`).toBeGreaterThanOrEqual(3)
   for(const sample of await contrast(page,'main [data-ui-button]:not(:disabled)'))expect(sample.ratio,`${reference} ${sample.label}: text`).toBeGreaterThanOrEqual(4.5)
   for(const sample of await contrast(page,'main input[placeholder]','placeholder'))expect(sample.ratio,`${reference} ${sample.label}: placeholder`).toBeGreaterThanOrEqual(4.5)
   for(const sample of await contrast(page,'main input:not([type=checkbox]):not([type=radio]):not([type=hidden])','border'))expect(sample.ratio,`${reference} ${sample.label}: input boundary`).toBeGreaterThanOrEqual(3)
   if(reference==='domains'){
    await page.getByRole('checkbox',{name:'Select blog.example.com',exact:true}).focus();await page.keyboard.press('Space')
    await expect(page.getByRole('checkbox',{name:'Select blog.example.com',exact:true})).toBeChecked()
    await expect(page.getByRole('checkbox',{name:'Select all domains',exact:true})).toHaveAttribute('aria-checked','mixed')
    await expect(page).toHaveURL(/\/app\/domains$/)
    await page.getByRole('button',{name:'Search the panel',exact:true}).focus();await page.keyboard.press('Enter')
    await expect(page.getByRole('combobox',{name:'Search tools and settings'})).toBeFocused()
    await page.getByRole('combobox',{name:'Search tools and settings'}).fill('dns zone edito')
    await expect(page.getByRole('option',{name:'DNS Management',exact:true})).toBeVisible()
    await expect(page.getByRole('option',{name:'Panel Settings',exact:true})).toHaveCount(0)
    await page.keyboard.press('Escape')
    await expect(page.getByRole('button',{name:'Search the panel',exact:true})).toBeFocused()
    await page.setViewportSize({width:375,height:960});await page.getByRole('button',{name:'Menu',exact:true}).click()
    await page.keyboard.press('Control+k')
    await expect(page.getByRole('dialog',{name:'Panel navigation'})).toHaveCount(0)
    await expect(page.getByRole('combobox',{name:'Search tools and settings'})).toBeFocused()
    await page.keyboard.press('Escape');await expect(page.getByRole('button',{name:'Menu',exact:true})).toBeFocused()
   }
   await context.close()
  }
 })
}
