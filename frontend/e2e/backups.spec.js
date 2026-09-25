import {test,expect} from '@playwright/test'
for(const skin of ['evolution','paper-lantern']) for(const mode of ['light','dark']) {
 test(`${skin} ${mode}: backup jobs, destinations and recovery points`,async({page},info)=>{
  const errors=[];page.on('pageerror',e=>errors.push(e.message))
  await page.addInitScript(({skin,mode})=>{
   localStorage.setItem('boron.ui',JSON.stringify({state:{skin,theme:mode},version:0}))
   localStorage.setItem('boron.auth',JSON.stringify({state:{role:'admin',username:'admin'},version:0}))
  },{skin,mode})
  let savedJob=null,recoveryRequests=0
  const dest={id:1,name:'Offsite daily',kind:'ssh',path:'/backups/boron',status:'ready',connection:{user:'backup',host:'storage.example.com',port:22},ssh_public_key:'ssh-ed25519 AAAATEST boron-backup'}
  await page.route('**/api/**',async route=>{
   const p=new URL(route.request().url()).pathname;const method=route.request().method();let data={}
   if(p.endsWith('/whoami'))data={role:'admin',username:'admin'}
   else if(p.endsWith('/onboarding'))data={completed:true}
   else if(p==='/api/v1/accounts')data={accounts:[{username:'alpha'},{username:'bravo'}]}
   else if(p.endsWith('/snapshots/destinations'))data={destinations:[dest]}
   else if(p.endsWith('/recovery-key')){recoveryRequests++;data={password:'TEST-ONLY-RECOVERY-KEY',kind:'ssh',path:dest.path,namespace:'testnamespace'}}
   else if(p.endsWith('/initialize'))data=dest
   else if(p.endsWith('/snapshots/policies')&&method==='POST'){savedJob=route.request().postDataJSON();data={id:1,...savedJob}}
   else if(p.endsWith('/snapshots/policies'))data={policies:savedJob?[{id:1,...savedJob,options:savedJob}]:[]}
   else if(p.endsWith('/policies/1/run'))data={run_ids:[10],skipped_busy_accounts:[]}
   else if(p.endsWith('/snapshots/runs'))data={runs:[{id:10,username:'alpha',account_id:1,status:'completed',snapshot_id:'a'.repeat(64),started_at:'2026-09-13T12:00:00Z',progress_message:'Snapshot ready',summary:{total_files_processed:100,total_bytes_processed:4096,data_added:128,files_unmodified:98},options:{components:['files','databases']}}]}
   else if(p.endsWith('/browse'))data={entries:[{name:'public_html',path:'/home/alpha/public_html',type:'dir'},{name:'site.txt',path:'/home/alpha/site.txt',type:'file',size:512}]}
   await route.fulfill({json:data})
  })
  await page.goto('/app/backup-jobs')
  await expect(page.getByRole('heading',{name:'Backup Manager',exact:true})).toBeVisible()
  await page.getByRole('button',{name:'Create job',exact:true}).first().click()
  const dialog=page.getByRole('dialog')
  await dialog.getByLabel('Job name',{exact:true}).fill('Daily WordPress')
  await dialog.getByText('File filters (optional)',{exact:true}).click()
  await dialog.getByLabel('Exclude matching paths',{exact:true}).fill('**/cache/**\n**/node_modules/**')
  await dialog.getByText('Exclude specific accounts',{exact:true}).click()
  await dialog.getByLabel('bravo',{exact:true}).check()
  await dialog.getByLabel('Email',{exact:true}).check()
  await page.screenshot({path:info.outputPath(`${skin}-${mode}-backup-job.png`),fullPage:true})
  await page.setViewportSize({width:390,height:844})
  const saveBox=await dialog.getByRole('button',{name:'Save job',exact:true}).boundingBox()
  expect(saveBox.y+saveBox.height).toBeLessThanOrEqual(844)
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await page.screenshot({path:info.outputPath(`${skin}-${mode}-backup-job-mobile.png`),fullPage:true})
  await page.setViewportSize({width:1440,height:1000})
  await dialog.getByRole('button',{name:'Save job',exact:true}).click()
  await expect(page.getByRole('table').getByRole('button',{name:'Daily WordPress',exact:true})).toBeVisible()
  expect(savedJob.accounts).toEqual([])
  expect(savedJob.excluded_accounts).toEqual(['bravo'])
  expect(savedJob.exclude_patterns).toEqual(['**/cache/**','**/node_modules/**'])
  expect(savedJob.notification_channels).toEqual(['email'])
  await page.getByRole('tab',{name:'Destinations',exact:true}).click()
  await page.getByRole('button',{name:'Manage',exact:true}).click()
  await expect(dialog.getByLabel('Backup SSH public key')).toHaveValue(dest.ssh_public_key)
  expect(recoveryRequests).toBe(0)
  await dialog.getByRole('button',{name:'Reveal recovery key',exact:true}).click()
  await expect(dialog.getByLabel('Backup recovery key')).toHaveValue('TEST-ONLY-RECOVERY-KEY')
  expect(recoveryRequests).toBe(1)
  const downloadPromise=page.waitForEvent('download')
  await dialog.getByRole('button',{name:'Download recovery information',exact:true}).click()
  expect((await downloadPromise).suggestedFilename()).toBe('boron-backup-recovery-1.json')
  await dialog.getByRole('button',{name:'Done',exact:true}).click()
  await page.getByRole('tab',{name:'Backup jobs',exact:true}).click()
  await page.getByRole('button',{name:'Run now',exact:true}).click()
  await page.getByRole('button',{name:'View details',exact:true}).click()
  await dialog.getByRole('button',{name:'Browse backed-up files',exact:true}).click()
  await expect(dialog.getByRole('button',{name:'public_html',exact:true})).toBeVisible()
  await page.screenshot({path:info.outputPath(`${skin}-${mode}-backup-history.png`),fullPage:true})
  await dialog.getByRole('button',{name:'Done',exact:true}).click()
  await page.setViewportSize({width:390,height:844})
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  expect(errors).toEqual([])
 })
}

for(const skin of ['evolution','paper-lantern']) {
 test(`${skin}: customer snapshot history preserves archive backups`,async({page},info)=>{
  const archiveRequests=[]
  const errors=[];page.on('pageerror',e=>errors.push(e.message))
  await page.addInitScript(skin=>{
   localStorage.setItem('boron.ui',JSON.stringify({state:{skin,theme:'light'},version:0}))
   localStorage.setItem('boron.auth',JSON.stringify({state:{role:'customer',username:'alpha'},version:0}))
  },skin)
  await page.route('**/api/**',async route=>{
   const p=new URL(route.request().url()).pathname;let data={}
   if(p.endsWith('/whoami'))data={role:'customer',username:'alpha'}
   else if(p.endsWith('/onboarding'))data={completed:true}
   else if(p.endsWith('/snapshots/runs'))data={runs:[{id:1,username:'alpha',status:'completed',snapshot_id:'a'.repeat(64),started_at:'2026-09-13T12:00:00Z',progress_message:'Snapshot ready',summary:{data_added:4096},options:{components:['files']}}]}
   else if(p.endsWith('/databases'))data={databases:[{db_name:'alpha_blog'},{db_name:'alpha_shop'}]}
   else if(p.endsWith('/backups')&&route.request().method()==='POST'){archiveRequests.push(route.request().postDataJSON());data={id:91,status:'pending'}}
   else if(p.endsWith('/backups'))data={jobs:[]}
   else if(p.endsWith('/restores/list'))data={restore_jobs:[]}
   else if(p.endsWith('/browse'))data={entries:[{name:'site.txt',path:'/home/alpha/site.txt',type:'file',size:100}]}
   await route.fulfill({json:data})
  })
  await page.goto('/app/backups')
  await expect(page.getByRole('heading',{name:'Scheduled recovery points',exact:true})).toBeVisible()
  await expect(page.getByRole('heading',{name:'On-demand archive backups',exact:true})).toBeVisible()
  await page.getByRole('button',{name:'View details',exact:true}).click()
  await page.getByRole('button',{name:'Browse backed-up files',exact:true}).click()
  await expect(page.getByRole('dialog').getByRole('cell',{name:'site.txt',exact:true})).toBeVisible()
  await page.screenshot({path:info.outputPath(`${skin}-customer-backup.png`),fullPage:true})
  await page.getByRole('button',{name:'Done',exact:true}).click()
  await page.getByRole('button',{name:'Create backup',exact:true}).first().click()
  await page.getByRole('dialog').getByRole('button',{name:'Back up now',exact:true}).click()
  await expect(page.getByRole('dialog')).not.toBeVisible()
  expect(archiveRequests[0].kind).toBe('full')
  await page.getByRole('button',{name:'Create backup',exact:true}).first().click()
  const create=page.getByRole('dialog')
  await create.getByLabel('What to back up').selectOption('database')
  await expect(create.getByLabel('Database')).toHaveValue('')
  await expect(create.getByLabel('Database').locator('option')).toHaveCount(3)
  await create.getByLabel('Database').selectOption('alpha_shop')
  await create.getByRole('button',{name:'Back up now',exact:true}).click()
  expect(archiveRequests[1]).toEqual({kind:'database',item_ref:'alpha_shop'})
  await page.getByRole('button',{name:'Create backup',exact:true}).first().click()
  await page.getByRole('dialog').getByLabel('What to back up').selectOption('databases')
  await page.getByRole('dialog').getByRole('button',{name:'Back up now',exact:true}).click()
  expect(archiveRequests[2]).toEqual({kind:'databases'})
  await page.setViewportSize({width:390,height:844})
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  expect(errors).toEqual([])
 })
}

test('S3-compatible destination uses write-only credentials and provider fields',async({page})=>{
 let submitted=null
 await page.addInitScript(()=>{
  localStorage.setItem('boron.ui',JSON.stringify({state:{skin:'evolution',theme:'light'},version:0}))
  localStorage.setItem('boron.auth',JSON.stringify({state:{role:'admin',username:'admin'},version:0}))
 })
 await page.route('**/api/**',async route=>{
  const p=new URL(route.request().url()).pathname;const method=route.request().method();let data={}
  if(p.endsWith('/whoami'))data={role:'admin',username:'admin'}
  else if(p.endsWith('/onboarding'))data={completed:true}
  else if(p==='/api/v1/accounts')data={accounts:[]}
  else if(p.endsWith('/snapshots/destinations')&&method==='POST'){
   submitted=route.request().postDataJSON()
   data={id:9,name:submitted.name,kind:'s3',path:submitted.s3_prefix,status:'draft',connection:{provider:submitted.s3_provider,endpoint:submitted.s3_endpoint,bucket:submitted.s3_bucket,region:submitted.s3_region},ssh_public_key:null}
  }
  else if(p.endsWith('/snapshots/destinations'))data={destinations:[]}
  else if(p.endsWith('/snapshots/policies'))data={policies:[]}
  else if(p.endsWith('/snapshots/runs'))data={runs:[]}
  await route.fulfill({json:data})
 })
 await page.goto('/app/backup-jobs?tab=destinations&action=create')
 const dialog=page.getByRole('dialog')
 await expect(dialog).toBeVisible()
 await dialog.getByLabel('Destination name').fill('Cloudflare archive')
 await dialog.getByLabel('Storage type').selectOption('s3')
 await dialog.getByLabel('Provider').selectOption('cloudflare')
 await dialog.getByLabel('Region').fill('auto')
 await dialog.getByLabel('HTTPS endpoint').fill('https://account.r2.cloudflarestorage.com')
 await dialog.getByLabel('Bucket').fill('hosting-backups')
 await dialog.getByLabel('Repository prefix').fill('boron/daily')
 await dialog.getByLabel('Access key ID').fill('write-only-access')
 await dialog.getByLabel('Secret access key').fill('write-only-secret')
 await dialog.getByRole('button',{name:'Save destination'}).click()
 await expect(dialog.getByText('hosting-backups/boron/daily')).toBeVisible()
 expect(submitted).toMatchObject({kind:'s3',s3_provider:'cloudflare',s3_region:'auto',s3_bucket:'hosting-backups',s3_prefix:'boron/daily',s3_access_key:'write-only-access',s3_secret_key:'write-only-secret'})
 await expect(page.getByText('write-only-secret')).toHaveCount(0)
})
