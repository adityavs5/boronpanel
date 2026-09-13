import {test,expect} from '@playwright/test'
for(const skin of ['evolution','paper-lantern']) for(const mode of ['light','dark']) {
 test(`${skin} ${mode}: inner DNS and database pages, forms and mobile`,async({page},info)=>{
  const errors=[];page.on('pageerror',e=>errors.push(e.message))
  await page.addInitScript(({skin,mode})=>{
   localStorage.setItem('boron.ui',JSON.stringify({state:{skin,theme:mode},version:0}))
   localStorage.setItem('boron.auth',JSON.stringify({state:{role:'customer',username:'hostingdemo'},version:0}))
  },{skin,mode})
  await page.route('**/api/**',async route=>{
   const p=new URL(route.request().url()).pathname;let data={}
   if(p.endsWith('/whoami'))data={role:'customer',username:'hostingdemo'}
   else if(p.endsWith('/onboarding'))data={completed:true}
   else if(p.endsWith('/domains'))data={domains:[{domain:'example.com',is_primary:true}]}
   else if(p.endsWith('/records'))data={zone:'example.com',records:[{name:'example.com.',type:'A',ttl:3600,values:['192.0.2.10']},{name:'www.example.com.',type:'CNAME',ttl:3600,values:['example.com.']}]}
   else if(p.endsWith('/ssl'))data={certbot_timer_active:true,domains:[{domain:'example.com',cert_status:'active',issuer:'Lets Encrypt',expiry_date:'2026-12-01',days_remaining:70}]}
   else if(p.endsWith('/databases'))data={databases:[{db_name:'hostingdemo_wordpress',db_user:'hostingdemo_wordpress',created_at:'2026-09-13T12:00:00'}]}
   await route.fulfill({json:data})
  })
  await page.goto('/app/dns');await expect(page.getByRole('table').getByText('192.0.2.10',{exact:true})).toBeVisible()
  await page.screenshot({path:info.outputPath(`${skin}-${mode}-dns.png`),fullPage:true})
  await page.getByRole('button',{name:'Add record',exact:true}).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByRole('dialog').locator('select')).toHaveCSS('background-repeat','no-repeat')
  await page.screenshot({path:info.outputPath(`${skin}-${mode}-dns-form.png`),fullPage:true})
  await page.getByRole('button',{name:'Cancel',exact:true}).click()
  await page.goto('/app/databases');await expect(page.getByRole('table').getByText('hostingdemo_wordpress',{exact:true}).first()).toBeVisible()
  await page.screenshot({path:info.outputPath(`${skin}-${mode}-databases.png`),fullPage:true})
  await page.getByRole('table').getByRole('button',{name:'Manage database hostingdemo_wordpress',exact:true}).click()
  await expect(page.getByRole('dialog').getByRole('button',{name:'Open phpMyAdmin',exact:true})).toBeVisible()
  await page.getByRole('button',{name:'Done',exact:true}).click()
  await page.goto('/app/ssl')
  await page.getByRole('table').getByRole('button',{name:'Manage SSL for example.com',exact:true}).click()
  await expect(page.getByRole('dialog').getByRole('button',{name:'Renew certificate',exact:true})).toBeVisible()
  await page.getByRole('button',{name:'Done',exact:true}).click()
  await page.setViewportSize({width:390,height:844})
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  expect(errors).toEqual([])
 })
}
