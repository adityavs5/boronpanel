import {test,expect} from '@playwright/test'
for(const skin of ['evolution','paper-lantern']) for(const mode of ['light','dark']) {
 test(`${skin} ${mode}: clock failure and recovery are visible`,async({page},info)=>{
  await page.addInitScript(({skin,mode})=>{
   localStorage.setItem('boron.ui',JSON.stringify({state:{skin,theme:mode},version:0}))
   localStorage.setItem('boron.auth',JSON.stringify({state:{role:'admin',username:'admin'},version:0}))
  },{skin,mode})
  let healthy=false
  await page.route('**/api/**',async route=>{
   const p=new URL(route.request().url()).pathname;let data={}
   if(p.endsWith('/whoami'))data={role:'admin',username:'admin'}
   else if(p.endsWith('/onboarding'))data={completed:true}
   else if(p==='/api/v1/health')data={cpu_pct:1,mem_pct:10,disks:[],clock:{status:healthy?'healthy':'critical',message:healthy?'Clock is synchronized for time-based authentication.':'Clock is not synchronized to an external time source.',source:'162.159.200.1',offset_seconds:.001,error_bound_seconds:.07}}
   else if(p.endsWith('/history'))data={points:[],services:[]}
   await route.fulfill({json:data})
  })
  await page.goto('/app/health')
  await expect(page.getByText('Clock synchronization needs attention',{exact:false})).toBeVisible()
  await expect(page.getByRole('heading',{name:'Clock & 2FA health',exact:true})).toBeVisible()
  healthy=true
  await page.getByRole('button',{name:'Refresh',exact:true}).first().click()
  await expect(page.getByText('Clock is synchronized for time-based authentication.',{exact:true})).toBeVisible()
  await expect(page.getByText('Clock synchronization needs attention',{exact:false})).not.toBeVisible()
  await page.setViewportSize({width:390,height:844})
  await page.getByRole('heading',{name:'Clock & 2FA health',exact:true}).scrollIntoViewIfNeeded()
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await page.screenshot({path:info.outputPath(`${skin}-${mode}-clock-health.png`),fullPage:true})
 })
}
