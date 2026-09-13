import {test,expect} from '@playwright/test'
for (const skin of ['evolution','paper-lantern']) for (const mode of ['light','dark']) {
 test(`${skin} ${mode}: database details open phpMyAdmin in a safe new tab`,async({page,context})=>{
  await page.addInitScript(({skin,mode})=>{
   localStorage.setItem('boron.ui',JSON.stringify({state:{skin,theme:mode},version:0}))
   localStorage.setItem('boron.auth',JSON.stringify({state:{role:'customer',username:'hostingdemo'},version:0}))
  },{skin,mode})
  let requests=0
  await page.route('**/api/**',async route=>{
   const p=new URL(route.request().url()).pathname;let data={}
   if(p.endsWith('/whoami'))data={role:'customer',username:'hostingdemo'}
   else if(p.endsWith('/onboarding'))data={completed:true}
   else if(p.endsWith('/databases'))data={databases:[{db_name:'hostingdemo_wordpress',db_user:'hostingdemo_wordpress',created_at:'2026-09-13T12:00:00'}]}
   else if(p.endsWith('/pma-token')){requests++;data={pma_url:'https://pma.example.test/boron_signon.php?token=test-only'}}
   await route.fulfill({json:data})
  })
  await context.route('https://pma.example.test/**',route=>route.fulfill({contentType:'text/html',body:'<h1>Database management</h1>'}))
  await page.goto('/app/databases')
  await page.getByRole('button',{name:'Manage database hostingdemo_wordpress',exact:true}).click()
  const opened=context.waitForEvent('page')
  await page.getByRole('button',{name:'Open phpMyAdmin',exact:true}).click()
  const popup=await opened
  await expect(popup.getByRole('heading',{name:'Database management'})).toBeVisible()
  expect(await popup.evaluate(()=>window.opener)).toBeNull()
  expect(requests).toBe(1)
  expect(page.url()).toContain('/app/databases')
 })
}
