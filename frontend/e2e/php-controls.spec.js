import {test,expect} from '@playwright/test'
for(const skin of ['evolution','paper-lantern']) for(const mode of ['light','dark']) {
 test(`${skin} ${mode}: PHP defaults, site overrides and editable presets`,async({page},info)=>{
  const errors=[];page.on('pageerror',e=>errors.push(e.message))
  await page.addInitScript(({skin,mode})=>{
   localStorage.setItem('boron.ui',JSON.stringify({state:{skin,theme:mode},version:0}))
   localStorage.setItem('boron.auth',JSON.stringify({state:{role:'customer',username:'alpha'},version:0}))
  },{skin,mode})
  let defaultVersion='8.3',override=null,settingsRequest=null,siteRequests=[]
  const defaults={memory_limit:'128M',upload_max_filesize:'2M',post_max_size:'8M',max_execution_time:30,max_input_vars:1000}
  const presets=[{id:'lite',name:'Lite',directives:{memory_limit:'128M'}},{id:'moderate',name:'Moderate',description:'Typical WordPress sites.',directives:{memory_limit:'256M',upload_max_filesize:'64M',post_max_size:'80M',max_execution_time:120,max_input_vars:3000}},{id:'max',name:'Max',directives:{memory_limit:'512M'}}]
  await page.route('**/api/**',async route=>{
   const p=new URL(route.request().url()).pathname,method=route.request().method();let data={}
   if(p.endsWith('/whoami'))data={role:'customer',username:'alpha'}
   else if(p.endsWith('/onboarding'))data={completed:true}
   else if(p.endsWith('/domains/example.com/php-version')){const body=route.request().postDataJSON();siteRequests.push(body);override=body.php_version}
   else if(p.endsWith('/php-version'))defaultVersion=route.request().postDataJSON().php_version
   else if(p.endsWith('/accounts/alpha'))data={username:'alpha',php_version:defaultVersion}
   else if(p.endsWith('/domains'))data={domains:[{domain:'example.com',php_version:override}]}
   else if(p.endsWith('/php-ini')){
    if(method==='PATCH')settingsRequest=route.request().postDataJSON()
    data={php_versions:['8.1','8.3'],limit_presets:presets,directives:Object.entries(defaults).map(([name,value])=>({name,type:typeof value==='number'?'int':'size',default:value,value:settingsRequest?.directives[name]??null,min:1,max:10000,max_mb:2048}))}
   }
   else if(p.endsWith('/php-extensions'))data={extensions:[],enabled:[]}
   await route.fulfill({json:data})
  })
  await page.goto('/app/php')
  await expect(page.getByRole('heading',{name:'PHP version per site',exact:true})).toBeVisible()
  await expect(page.getByLabel('PHP limits template',{exact:true})).toHaveValue('custom')
  await page.getByLabel('PHP limits template',{exact:true}).selectOption('moderate')
  await expect(page.getByLabel('Memory limit',{exact:true})).toHaveValue('256M')
  await page.getByLabel('Memory limit',{exact:true}).fill('384M')
  await expect(page.getByLabel('PHP limits template',{exact:true})).toHaveValue('custom')
  expect(settingsRequest).toBeNull()
  await page.getByRole('button',{name:'Save changes',exact:true}).click()
  await expect.poll(()=>settingsRequest?.directives.memory_limit).toBe('384M')
  expect(settingsRequest.directives.max_execution_time).toBe(120)
  await page.getByLabel('PHP version for example.com',{exact:true}).selectOption('8.1')
  await page.getByRole('button',{name:'Save PHP version for example.com',exact:true}).click()
  await expect(page.getByText('Currently PHP 8.1 · Site override',{exact:true})).toBeVisible()
  await page.getByLabel('PHP version',{exact:true}).selectOption('8.1')
  await page.getByRole('button',{name:'Change version',exact:true}).click()
  await expect.poll(()=>defaultVersion).toBe('8.1')
  await page.getByLabel('PHP version for example.com',{exact:true}).selectOption('')
  await page.getByRole('button',{name:'Save PHP version for example.com',exact:true}).click()
  await expect(page.getByText('Currently PHP 8.1 · Account default',{exact:true})).toBeVisible()
  expect(siteRequests).toEqual([{php_version:'8.1'},{php_version:null}])
  while(await page.getByRole('button',{name:'Dismiss notification',exact:true}).count())await page.getByRole('button',{name:'Dismiss notification',exact:true}).first().click()
  await page.screenshot({path:info.outputPath(`${skin}-${mode}-php-settings.png`),fullPage:true})
  await page.setViewportSize({width:390,height:844})
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await page.screenshot({path:info.outputPath(`${skin}-${mode}-php-mobile.png`),fullPage:true})
  await page.getByLabel('PHP limits template',{exact:true}).scrollIntoViewIfNeeded()
  await page.screenshot({path:info.outputPath(`${skin}-${mode}-php-limits-mobile.png`),fullPage:true})
  expect(errors).toEqual([])
 })
}
