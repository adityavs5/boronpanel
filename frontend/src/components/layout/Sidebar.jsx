import { useEffect, useMemo, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { ChevronDown, PanelLeftClose, PanelLeftOpen, Search, LayoutDashboard, X, Boxes, GitBranch, Code, Braces, Database } from 'lucide-react'
import { useUI } from '@/store/ui'
import { useAuth } from '@/store/auth'
import { getToolGroups } from '@/config/toolGroups'
import { resellerNav } from '@/config/nav'
import { searchEntries } from '@/config/search'
import { Tooltip } from '@/components/ui/Tooltip'

const lineIcons = {'/wordpress':Boxes,'/redis':Database,'/git':GitBranch,'/node-apps':Code,'/python-apps':Braces,'/software/node-apps':Code,'/software/python-apps':Braces}
export function Sidebar({ mobile = false, onNavigate }) {
  const role=useAuth(s=>s.role),skin=useUI(s=>s.skin),location=useLocation()
  const storedCollapsed=useUI(s=>s.sidebarCollapsed),toggle=useUI(s=>s.toggleSidebar)
  const collapsed=!mobile&&storedCollapsed
  const closed=useUI(s=>s.navigationGroups),setGroup=useUI(s=>s.setNavigationGroup)
  const [query,setQuery]=useState('')
  const groups=useMemo(()=>role==='reseller'?[{title:'Reseller',items:resellerNav.filter(item=>item.to)}]:getToolGroups(role,skin),[role,skin])
  const candidates=groups.flatMap(group=>group.items)
  const matching=candidates.filter(item=>{
    const url=new URL(item.to,'https://panel.invalid')
    return !item.external&&(location.pathname===url.pathname||location.pathname.startsWith(url.pathname+'/'))&&[...url.searchParams].every(([key,value])=>new URLSearchParams(location.search).get(key)===value)
  }).sort((a,b)=>b.to.length-a.to.length)
  const active=matching[0]?.to,activeLabel=matching[0]?.label
  useEffect(()=>{
    const group=groups.find(group=>group.items.some(item=>item.to===active))
    if(group)setGroup(`${role}:${skin}:${group.title}`,false)
  },[active,role,skin,groups,setGroup])
  useEffect(()=>setQuery(''),[location.pathname,location.search])
  const key=title=>`${role}:${skin}:${title}`
  const home=role==='admin'?'/overview':role==='reseller'?'/reseller':'/dashboard'
  return <aside className="hosting-sidebar" data-collapsed={collapsed} aria-label={mobile?'Mobile tools':'Tools sidebar'}>
    <div className="sidebar-toolbar">
      {!collapsed&&<strong>{role==='admin'?'Administration':role==='reseller'?'Reseller panel':'Hosting tools'}</strong>}
      {mobile?<button type="button" aria-label="Close navigation" onClick={onNavigate}><X/></button>:<button type="button" aria-label={collapsed?'Expand sidebar':'Collapse sidebar'} aria-expanded={!collapsed} onClick={toggle}>{collapsed?<PanelLeftOpen/>:<PanelLeftClose/>}</button>}
    </div>
    {!collapsed&&<div className="sidebar-search"><Search aria-hidden="true"/><input aria-label="Search navigation" placeholder="Search tools…" value={query} onChange={e=>setQuery(e.target.value)}/></div>}
    <nav aria-label="Primary navigation">
      <Link to={home} className="sidebar-tool sidebar-home" onClick={onNavigate} aria-label="Dashboard"><LayoutDashboard aria-hidden="true"/>{!collapsed&&<span>Dashboard</span>}</Link>
      {groups.map((group,index)=>{
        const items=query.trim()?searchEntries(group.items.map(item=>({...item,section:group.title})),query):group.items
        if(!items.length)return null
        const id=`navigation-${mobile?'mobile':'desktop'}-${index}`
        return <section key={group.title}>
          {!collapsed&&<button type="button" className="sidebar-section" aria-expanded={!!query||!closed[key(group.title)]} aria-controls={id} onClick={()=>setGroup(key(group.title),!closed[key(group.title)])}><span>{group.title}</span><ChevronDown data-closed={!!closed[key(group.title)]&&!query}/></button>}
          <div id={id} hidden={!collapsed&&!query&&closed[key(group.title)]}>
            {items.map(item=>{
              const Icon=lineIcons[item.to.split('?')[0]]||item.icon
              const content=<><Icon aria-hidden="true"/>{!collapsed&&<span>{item.label}</span>}</>
              const props={className:'sidebar-tool','aria-label':item.label,'aria-current':active===item.to&&activeLabel===item.label?'page':undefined,onClick:onNavigate}
              const link=item.external?<a {...props} href={item.to} target="_blank" rel="noopener noreferrer">{content}</a>:<Link {...props} to={item.to}>{content}</Link>
              return collapsed?<Tooltip key={`${item.to}:${item.label}`} content={item.label} side="right">{link}</Tooltip>:<span className="sidebar-link-wrapper" key={`${item.to}:${item.label}`}>{link}</span>
            })}
          </div>
        </section>
      })}
      {query.trim()&&!groups.some(g=>searchEntries(g.items,query).length>0)&&<p className="sidebar-empty">No matching tools.</p>}
    </nav>
    {!collapsed&&<div className="sidebar-footer"><button type="button" onClick={()=>groups.forEach(g=>setGroup(key(g.title),false))}>Expand all</button><button type="button" onClick={()=>groups.forEach(g=>setGroup(key(g.title),true))}>Collapse all</button></div>}
  </aside>
}
