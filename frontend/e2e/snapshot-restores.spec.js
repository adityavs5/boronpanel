import {test,expect} from '@playwright/test'
for(const skin of ['evolution','paper-lantern']) for(const mode of ['light','dark']) {
 test(`${skin} ${mode}: select snapshot files, restore and undo`,async({page},info)=>{
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
   else if(p.endsWith('/snapshots/runs'))data={runs:[{id:1,username:'alpha',status:'completed',snapshot_id:'a'.repeat(64),started_at:'2026-09-13T12:00:00Z',summary:{data_added:1024},options:{components:['files']}}]}
   else if(p.endsWith('/browse'))data={entries:[{name:'site.txt',path:'/home/alpha/site.txt',restore_path:'site.txt',type:'file',size:128}]}
   else if(p.endsWith('/runs/1/restore')&&method==='POST'){
    restoreRequest=route.request().postDataJSON();data={id:10,status:'pending'}
    restores=[{id:10,status:'completed',safety_snapshot_id:'b'.repeat(64),progress_message:'Selected files restored; unrelated files retained',started_at:'2026-09-13T12:00:10Z'}]
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
  await detail.getByRole('button',{name:'Browse backed-up files',exact:true}).click()
  await detail.getByRole('button',{name:'Select for restore',exact:true}).click()
  await expect(detail.getByLabel('Selected files or folders',{exact:true})).toHaveValue('site.txt')
  await expect(detail.getByRole('button',{name:'Restore selected files',exact:true})).toBeDisabled()
  await detail.getByLabel('Type alpha to confirm',{exact:true}).fill('alpha')
  await page.screenshot({path:info.outputPath(`${skin}-${mode}-file-restore.png`),fullPage:true})
  await detail.getByRole('button',{name:'Restore selected files',exact:true}).click()
  await expect(detail.getByRole('button',{name:'Recover previous files',exact:true})).toBeVisible()
  expect(restoreRequest).toEqual({kind:'files',confirmation:'alpha',paths:['site.txt']})
  await detail.getByRole('button',{name:'Recover previous files',exact:true}).click()
  const undo=page.getByRole('dialog').filter({has:page.getByRole('heading',{name:'Recover previous file versions?',exact:true})})
  await expect(undo.getByRole('button',{name:'Recover previous files',exact:true})).toBeDisabled()
  await undo.getByRole('textbox').fill('alpha')
  await undo.getByRole('button',{name:'Recover previous files',exact:true}).click()
  await expect(undo).not.toBeVisible()
  expect(undoRequest).toEqual({confirmation:'alpha'})
  expect(errors).toEqual([])
 })
}
