import {test,expect} from '@playwright/test'
for(const role of ['admin','customer']) for(const skin of ['evolution','paper-lantern']) {
 test(`${role} ${skin}: readable labels and local font budget`,async({page},info)=>{
  const remote=[]
  page.on('request',request=>{if(!request.url().startsWith('http://127.0.0.1:4173')&&!request.url().startsWith('data:'))remote.push(request.url())})
  await page.addInitScript(({role,skin})=>{
   localStorage.setItem('boron.ui',JSON.stringify({state:{skin,theme:'light'},version:0}))
   localStorage.setItem('boron.auth',JSON.stringify({state:{role,username:'qa'},version:0}))
  },{role,skin})
  await page.route('**/api/**',async route=>{
   const path=new URL(route.request().url()).pathname;let data={}
   if(path.endsWith('/whoami'))data={role,username:'qa'}
   else if(path.endsWith('/onboarding'))data={completed:true}
   else if(path==='/api/v1/accounts')data=[]
   else if(path.endsWith('/accounts/qa'))data={username:'qa',primary_domain:'example.test',php_version:'8.3',status:'active'}
   else if(path.endsWith('/domains'))data={domains:[]}
   else if(path.endsWith('/alerts'))data={active:[]}
   else if(path.endsWith('/plans'))data=[]
   else if(path.endsWith('/health'))data={cpu_pct:1,mem_pct:2,disks:[]}
   else if(path.endsWith('/usage'))data={current:{}}
   await route.fulfill({json:data})
  })
  await page.goto(role==='admin'?'/app/overview':'/app/dashboard')
  const labels=page.locator('.tool-label')
  await expect(labels.first()).toBeVisible()
  const metrics=await page.evaluate(async()=>{
   await document.fonts.ready
   return {family:getComputedStyle(document.body).fontFamily,
    sizes:[...document.querySelectorAll('.tool-label')].map(el=>parseFloat(getComputedStyle(el).fontSize)),
    fonts:performance.getEntriesByType('resource').filter(r=>r.name.includes('.woff2')).map(r=>({url:r.name,bytes:r.encodedBodySize}))}
  })
  expect(metrics.family).toContain('Open Sans')
  expect(metrics.sizes.every(size=>size>=14)).toBe(true)
  expect(metrics.fonts.length).toBeGreaterThan(0)
  expect(metrics.fonts.length).toBeLessThanOrEqual(4)
  expect(metrics.fonts.reduce((sum,font)=>sum+font.bytes,0)).toBeLessThanOrEqual(80000)
  expect(remote).toEqual([])
  await page.setViewportSize({width:390,height:844})
  expect(await labels.first().evaluate(el=>parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThanOrEqual(13)
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await page.screenshot({path:info.outputPath(`${role}-${skin}-typography.png`),fullPage:true})
  await info.attach('font-budget',{body:JSON.stringify(metrics,null,2),contentType:'application/json'})
 })
}
