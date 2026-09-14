import { test, expect } from '@playwright/test'
for (const skin of ['evolution','paper-lantern']) for (const theme of ['light','dark']) for (const section of ['cron', 'php', 'dns']) {
  const label = section === 'dns' ? 'DNS records' : section === 'php' ? 'PHP settings' : 'scheduled tasks'
  test(`${skin} ${theme}: ${label} restore and undo`, async ({ page }) => {
    await page.addInitScript(({skin,theme}) => {
      localStorage.setItem('boron.ui',JSON.stringify({state:{skin,theme},version:0}))
      localStorage.setItem('boron.auth',JSON.stringify({state:{role:'customer',username:'alpha'},version:0}))
    },{skin,theme})
    let restored, undone, history=[]
    await page.route('**/api/**',async route=>{
      const path=new URL(route.request().url()).pathname
      let data={}
      if(path.endsWith('/whoami'))data={role:'customer',username:'alpha'}
      else if(path.endsWith('/onboarding'))data={completed:true}
      else if(path.endsWith('/snapshots/runs'))data={runs:[{id:1,status:'completed',snapshot_id:'a'.repeat(64),options:{components:['config']},summary:{},started_at:'2026-09-14T00:00:00Z'}]}
      else if(path.endsWith('/configuration'))data={cron_available:true,managed_jobs:1,php_available:true,php_sites:2,php_default_version:'8.3',dns_available:true,dns_zones:[{zone:'alpha.test',available:true,record_count:2},{zone:'deleted.test',available:false,reason:'Zone no longer owned'}]}
      else if(path.endsWith('/runs/1/restore')){
        restored=route.request().postDataJSON()
        history=[{id:10,status:'completed',selection:{kind:'config',config_sections:[section],...(section==='dns'?{dns_zones:['alpha.test']}:{})},safety_snapshot_id:'b'.repeat(64),progress_message:'Scheduled tasks restored',started_at:'2026-09-14T00:00:00Z'}]
        data={id:10,status:'pending'}
      }
      else if(path.endsWith('/restores/10/undo')){undone=route.request().postDataJSON();data={id:11,status:'pending'}}
      else if(path.endsWith('/snapshots/restores'))data={restores:history}
      else if(path.endsWith('/backups'))data={jobs:[]}
      else if(path.endsWith('/restores/list'))data={restore_jobs:[]}
      await route.fulfill({json:data})
    })
    await page.goto('/app/backups')
    await page.getByRole('button',{name:'View details',exact:true}).click()
    const detail=page.getByRole('dialog').filter({has:page.getByRole('heading',{name:'Recovery point #1',exact:true})})
    await detail.getByRole('button',{name:`Restore ${label}`,exact:true}).click()
    const form=detail.getByRole('form',{name:section === 'dns' ? 'DNS records restore' : section === 'php' ? 'PHP settings restore' : 'Scheduled-task restore'})
    await expect(form.getByText(section === 'dns' ? /This can affect websites and email/ : section === 'php' ? /Administrator function restrictions are kept/ : /complete crontab/)).toBeVisible()
    await expect(form.getByRole('button',{name:`Restore ${label} now`})).toBeDisabled()
    await form.getByRole('textbox',{name:'Confirm account username',exact:true}).fill('alpha')
    await detail.getByRole('button', {name: `Restore ${section === 'php' ? 'scheduled tasks' : 'PHP settings'}`, exact: true}).click()
    await detail.getByRole('button', {name: `Restore ${label}`, exact: true}).click()
    await expect(form.getByRole('button', {name: `Restore ${label} now`})).toBeDisabled()
    await form.getByRole('textbox', {name: 'Confirm account username', exact: true}).fill('alpha')
    if (section === 'dns') {
      await expect(form.getByRole('checkbox', {name: /deleted.test/})).toBeDisabled()
      await form.getByRole('checkbox', {name: /alpha.test/}).check()
      await expect(form.getByRole('button', {name: `Restore ${label} now`})).toBeDisabled()
      await form.getByRole('textbox', {name: 'Confirm account username', exact: true}).fill('alpha')
    }
    await page.setViewportSize({width:390,height:844})
    await form.getByRole('button',{name:`Restore ${label} now`}).click()
    expect(restored).toEqual({kind:'config',config_sections:[section],confirmation:'alpha',...(section==='dns'?{dns_zones:['alpha.test']}:{})})
    await detail.getByRole('button',{name:`Recover previous ${label}`,exact:true}).click()
    const undo=page.getByRole('dialog').filter({has:page.getByRole('heading',{name:`Recover previous ${label}?`,exact:true})})
    await expect(undo.getByRole('button',{name:`Recover previous ${label}`,exact:true})).toBeDisabled()
    await undo.getByRole('textbox').fill('alpha')
    await undo.getByRole('button',{name:`Recover previous ${label}`,exact:true}).click()
    await expect.poll(()=>undone).toEqual({confirmation:'alpha'})
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  })
}
