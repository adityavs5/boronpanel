import {test,expect} from '@playwright/test'
for(const skin of ['evolution','paper-lantern']) for(const mode of ['light','dark']) {
 test(`${skin} ${mode}: select snapshot databases, restore and recover`,async({page},info)=>{
  const errors=[];page.on('pageerror',e=>errors.push(e.message))
  await page.addInitScript(({skin,mode})=>{
   localStorage.setItem('boron.ui',JSON.stringify({state:{skin,theme:mode},version:0}))
   localStorage.setItem('boron.auth',JSON.stringify({state:{role:'customer',username:'alpha'},version:0}))
  },{skin,mode})
  let restoreRequest=null,undoRequest=null,restores=[]
  await page.route('**/api/**',async route=>{
   const p=new URL(route.request().url()).pathname;const method=route.request().method();let data={}
   if(p.endsWith('/whoami'))data={role:'customer',username:'alpha'}
   else if(p.endsWith('/onboarding'))data={completed:true}
   else if(p.endsWith('/snapshots/runs'))data={runs:[{id:1,username:'alpha',status:'completed',snapshot_id:'a'.repeat(64),started_at:'2026-09-13T12:00:00Z',summary:{data_added:1024},options:{components:['databases']}}]}
   else if(p.endsWith('/databases'))data={databases:[{name:'alpha_wp',size:1024,available:true},{name:'alpha_old',size:512,available:false,reason:'This recovery point lacks reconstruction metadata.'},{name:'alpha_deleted',size:2048,available:true,action:'recreate',reason:'Deleted database: recreate with its backed-up login and password.'}]}
   else if(p.endsWith('/runs/1/restore')&&method==='POST'){
    restoreRequest=route.request().postDataJSON();data={id:10,status:'pending'}
    restores=[{id:10,status:'completed',selection:{kind:'databases',databases:['alpha_wp']},safety_snapshot_id:'b'.repeat(64),progress_message:'Selected databases restored',started_at:'2026-09-13T12:00:10Z'}]
   }
   else if(p.endsWith('/restores/10/undo')&&method==='POST'){undoRequest=route.request().postDataJSON();data={id:11,status:'pending'}}
   else if(p.endsWith('/snapshots/restores'))data={restores}
   else if(p.endsWith('/backups'))data={jobs:[]}
   else if(p.endsWith('/restores/list'))data={restore_jobs:[]}
   await route.fulfill({json:data})
  })
  await page.goto('/app/backups')
  await page.getByRole('button',{name:'View details',exact:true}).click()
  const detail=page.getByRole('dialog').filter({has:page.getByRole('heading',{name:'Recovery point #1',exact:true})})
  await detail.getByRole('button',{name:'Restore databases',exact:true}).click()
  const form=detail.getByRole('form',{name:'Database restore'})
  await expect(form.getByText(/including removal of tables created afterward/)).toBeVisible()
  await expect(form.getByRole('checkbox',{name:/alpha_old/})).toBeDisabled()
  await expect(form.getByRole('button',{name:'Restore selected databases',exact:true})).toBeDisabled()
  await form.getByRole('checkbox',{name:/alpha_wp/}).check()
  await form.getByRole('checkbox',{name:/alpha_deleted/}).check()
  await expect(form.getByText(/Deleted database: recreate/)).toBeVisible()
  await expect(form.getByRole('button',{name:'Restore selected databases',exact:true})).toBeDisabled()
  await form.getByLabel('Type alpha to confirm database restore',{exact:true}).fill('alpha')
  await page.setViewportSize({width:390,height:844})
  await form.getByRole('button',{name:'Restore selected databases',exact:true}).scrollIntoViewIfNeeded()
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await page.screenshot({path:info.outputPath(`${skin}-${mode}-database-restore.png`),fullPage:true})
  await detail.getByRole('button',{name:'Restore selected databases',exact:true}).click()
  await expect(detail.getByRole('button',{name:'Recover previous databases',exact:true})).toBeVisible()
  expect(restoreRequest).toEqual({kind:'databases',confirmation:'alpha',databases:['alpha_wp','alpha_deleted']})
  await detail.getByRole('button',{name:'Recover previous databases',exact:true}).click()
  const undo=page.getByRole('dialog').filter({has:page.getByRole('heading',{name:'Recover previous database versions?',exact:true})})
  await expect(undo.getByRole('button',{name:'Recover previous databases',exact:true})).toBeDisabled()
  await undo.getByRole('textbox').fill('alpha')
  await undo.getByRole('button',{name:'Recover previous databases',exact:true}).click()
  await expect(undo).not.toBeVisible()
  expect(undoRequest).toEqual({confirmation:'alpha'})
  expect(errors).toEqual([])
 })
}
