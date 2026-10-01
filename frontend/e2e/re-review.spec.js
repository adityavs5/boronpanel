import {test,expect} from '@playwright/test'
test.describe.configure({mode:'parallel'})
async function fixture(page,{skin,mode,role='admin'}){
 const writes=[],account={id:1,username:'alpha',status:'active',uid:2000,gid:2000,primary_domain:'example.com',php_version:'8.3',cpu_pct:100,cpu_cores:1,host_cpu_cores:12,host_memory_gb:7.7,mem_mb:512,io_mb:50,pids_max:50}
 const redis={id:2,account_id:1,enabled:false,provisioned:true,active:'inactive',mem_mb:96,socket_path:'/home/alpha/.redis/redis.sock',used_memory_human:null}
 await page.addInitScript(({skin,mode,role})=>{localStorage.setItem('boron.ui',JSON.stringify({state:{skin,theme:mode},version:0}));localStorage.setItem('boron.auth',JSON.stringify({state:{role,username:role==='admin'?'admin':'alpha'},version:0}))},{skin,mode,role})
 await page.route('**/api/**',async route=>{
  const req=route.request(),path=new URL(req.url()).pathname,method=req.method();let data={}
  if(!['GET','HEAD'].includes(method))writes.push({path,method,body:req.postDataJSON()})
  if(path.endsWith('/whoami'))data={role,username:role==='admin'?'admin':'alpha'}
  else if(path.endsWith('/branding'))data={panel_name:'Boron'}
  else if(path.endsWith('/onboarding'))data={completed:true}
  else if(path==='/api/v1/accounts')data=[account]
  else if(path==='/api/v1/accounts/alpha')data=account
  else if(path.endsWith('/domains'))data={domains:[{id:1,domain:'example.com',kind:'primary',docroot:'/home/alpha/public_html'}]}
  else if(path==='/api/v1/admin/resellers')data={resellers:[{id:42,username:'reseller42',status:'active'}]}
  else if(path.endsWith('/redis')){if(method==='POST')Object.assign(redis,{enabled:true,active:'active'});if(method==='DELETE')Object.assign(redis,{enabled:false,active:'inactive'});data=redis}
  else if(path.endsWith('/records'))data={zone:'example.com',managed:true,records:[]}
  else if(path==='/api/v1/admin/isolation')data={accounts:[{username:'alpha',uid:2000,web_php:{status:'verified'},terminal:{status:'verified'},app_services:{status:'verified'},ssh_sftp:{status:'unverified'}}],capabilities:{mount_namespace:{status:'verified'},private_tmp:{status:'verified'},resource_enforcement:{status:'verified'},pid_namespace:{status:'degraded'}},warning:'Runtime measurements are separate from configuration.'}
  else if(path.endsWith('/self-test'))data={username:'alpha',passed:false,status:'failed',namespace_enabled:true,own_home_readable:true,other_home_hidden:true,process_isolation:'failed',process_isolation_workers:[{pid:123,private_pid_namespace:false,status:'failed'}],skipped:[]}
  else if(path.endsWith('/backups'))data=[]
  else if(path.endsWith('/usage'))data={resources:{},current:{}}
  await route.fulfill({json:data})
 })
 return {writes,redis}
}
for(const skin of ['evolution','paper-lantern'])for(const mode of ['light','dark']){
 test(`${skin} ${mode}: disabled Redis enables its saved instance and cannot flush`,async({page})=>{
  const {writes}=await fixture(page,{skin,mode,role:'customer'})
  await page.goto('/app/redis')
  await expect(page.getByRole('button',{name:'Enable Redis',exact:true})).toBeVisible()
  await expect(page.getByRole('button',{name:'Disable',exact:true})).toHaveCount(0)
  await expect(page.getByRole('button',{name:'Flush',exact:true})).toBeDisabled()
  await expect(page.getByText(/saved memory limit and files are retained/)).toBeVisible()
  await page.getByRole('button',{name:'Enable Redis',exact:true}).click()
  await expect(page.getByRole('button',{name:'Disable',exact:true})).toBeVisible()
  expect(writes.find(x=>x.method==='POST').body).toEqual({mem_mb:96})
  await page.getByRole('button',{name:'Disable',exact:true}).click()
  const dialog=page.getByRole('dialog')
  await expect(dialog.getByText(/keeps its configuration and files/)).toBeVisible()
  await dialog.getByLabel('Type alpha to confirm').fill('alpha')
  await dialog.getByRole('button',{name:'Disable Redis',exact:true}).click()
  await expect(page.getByRole('button',{name:'Enable Redis',exact:true})).toBeVisible()
  await expect(page.getByRole('button',{name:'Flush',exact:true})).toBeDisabled()
  expect(writes.filter(x=>x.path.endsWith('/redis')&&x.method==='DELETE')).toHaveLength(1)
 })
 test(`${skin} ${mode}: ambiguous DNS owner needs explicit relative confirmation`,async({page})=>{
  const {writes}=await fixture(page,{skin,mode,role:'customer'})
  await page.goto('/app/dns');await page.getByRole('button',{name:'Add record',exact:true}).click()
  const dialog=page.getByRole('dialog'),name=dialog.getByRole('textbox',{name:'DNS record name'}),save=dialog.getByRole('button',{name:'Save record',exact:true})
  await name.fill('x.other-zone.com');await dialog.locator('textarea').fill('192.0.2.77')
  await expect(dialog.getByText('x.other-zone.com.example.com',{exact:true})).toBeVisible()
  await expect(save).toBeDisabled();expect(writes).toHaveLength(0)
  await dialog.getByRole('checkbox',{name:'Confirm relative DNS name'}).check();await expect(save).toBeEnabled()
  await name.fill('x.other-zone.com.');await expect(save).toBeDisabled();await expect(dialog.getByText(/This hostname is outside example.com/)).toBeVisible()
  await name.fill('x.other-zone.com');await expect(save).toBeDisabled()
  await dialog.getByRole('checkbox',{name:'Confirm relative DNS name'}).check();await save.click()
  await expect(dialog).toBeHidden();expect(writes.at(-1).body.subdomain).toBe('x.other-zone.com')
 })
 test(`${skin} ${mode}: Accounts reseller uses a styled select and row menu retains scope`,async({page})=>{
  const {writes}=await fixture(page,{skin,mode})
  await page.goto('/app/accounts')
  const reseller=page.getByRole('combobox',{name:'Reseller for alpha',exact:true})
  await expect(reseller).toBeVisible();expect(await reseller.evaluate(el=>el.tagName)).toBe('BUTTON')
  await reseller.click();await page.getByRole('option',{name:'reseller42',exact:true}).click()
  expect(writes.find(x=>x.path.endsWith('/resellers/accounts/alpha')).body).toEqual({reseller_id:42})
  await page.getByRole('button',{name:'Actions for alpha',exact:true}).click();await page.getByRole('menuitem',{name:'Backups',exact:true}).click()
  await expect(page).toHaveURL(/accounts\/alpha\?tab=backups$/)
 })
 test(`${skin} ${mode}: isolation failure identifies the exact shared-namespace worker`,async({page})=>{
  await fixture(page,{skin,mode});await page.goto('/app/filesystem-isolation')
  await page.getByRole('button',{name:'Test',exact:true}).first().click()
  await expect(page.getByLabel('PHP worker checks for alpha')).toContainText('Worker 123: Failed — shares the host process namespace')
  await expect(page.getByText('Isolation self-test failed',{exact:true})).toBeVisible()
 })
}
