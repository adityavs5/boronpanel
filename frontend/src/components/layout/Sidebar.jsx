import { useEffect, useMemo, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { ChevronDown, PanelLeftClose, PanelLeftOpen, Search, LayoutDashboard, ListChecks, X, Boxes, GitBranch, Code, Braces, Database } from 'lucide-react'
import { useUI } from '@/store/ui'
import { useAuth } from '@/store/auth'
import { getToolGroups, TASKS_GROUP } from '@/config/toolGroups'
import { toolGroupMeta } from '@/config/toolIcons'
import { resellerNav } from '@/config/nav'
import { filterNavigation } from '@/config/capabilities'
import { searchEntries } from '@/config/search'
import { Tooltip } from '@/components/ui/Tooltip'
import { activeTheme } from '@/themes/runtime'

const lineIcons = {'/wordpress':Boxes,'/redis':Database,'/git':GitBranch,'/node-apps':Code,'/python-apps':Braces,'/software/node-apps':Code,'/software/python-apps':Braces}

// The navigation tree of the signed-in role, shared by every theme's menu.
export function useNavGroups() {
  const role=useAuth(s=>s.role),capabilities=useAuth(s=>s.capabilities)
  return useMemo(()=>{
    if(role==='reseller')return [{title:'Reseller',items:filterNavigation(resellerNav,capabilities).filter(item=>item.to)}]
    const items=getToolGroups(role,capabilities)
    const target=items.find(g=>g.title===(role==='admin'?TASKS_GROUP.admin:TASKS_GROUP.customer))
    target?.items.unshift({label:'Tasks',to:'/tasks',icon:ListChecks})
    return items
  },[role,capabilities])
}

// The longest matching entry is the current page.
export function useActiveNavItem(groups) {
  const location=useLocation()
  const matching=groups.flatMap(group=>group.items).filter(item=>{
    const url=new URL(item.to,'https://panel.invalid')
    return !item.external&&(location.pathname===url.pathname||location.pathname.startsWith(url.pathname+'/'))&&[...url.searchParams].every(([key,value])=>new URLSearchParams(location.search).get(key)===value)
  }).sort((a,b)=>b.to.length-a.to.length)
  return matching[0]
}

export function Sidebar({ mobile = false, onNavigate, brand }) {
  const role=useAuth(s=>s.role),location=useLocation()
  const { sidebar = {} } = activeTheme()
  const storedCollapsed=useUI(s=>s.sidebarCollapsed),toggle=useUI(s=>s.toggleSidebar)
  const collapsed=!mobile&&storedCollapsed&&sidebar.collapsible!==false
  const closed=useUI(s=>s.navigationGroups),setGroup=useUI(s=>s.setNavigationGroup)
  const [query,setQuery]=useState('')
  const groups=useNavGroups()
  const current=useActiveNavItem(groups)
  const active=current?.to,activeLabel=current?.label
  useEffect(()=>{
    const open=groups.find(group=>group.items.some(item=>item.to===active))
    groups.forEach(group=>setGroup(`${role}:${group.title}`,group.title!==open?.title))
  },[active,role,groups,setGroup])
  useEffect(()=>setQuery(''),[location.pathname,location.search])
  const key=title=>`${role}:${title}`
  const home=role==='admin'?'/overview':role==='reseller'?'/reseller':'/dashboard'
  const isClosed=group=>closed[key(group.title)] ?? !group.items.some(item=>item.to===active)
  return <aside className="hosting-sidebar" data-tone={sidebar.tone||'rail'} data-collapsed={collapsed} data-mobile={mobile||undefined} aria-label={mobile?'Mobile tools':'Tools sidebar'}>
    {brand&&!collapsed&&<div className="sidebar-brand">{brand}</div>}
    <div className="sidebar-toolbar">
      {!collapsed&&<strong>{role==='admin'?'Administration':role==='reseller'?'Reseller panel':'Hosting tools'}</strong>}
      {mobile?<button type="button" aria-label="Close navigation" onClick={onNavigate}><X/></button>:<button type="button" aria-label={collapsed?'Expand sidebar':'Collapse sidebar'} aria-expanded={!collapsed} onClick={toggle}>{collapsed?<PanelLeftOpen/>:<PanelLeftClose/>}</button>}
    </div>
    {!collapsed&&<div className="sidebar-search"><Search aria-hidden="true"/><input aria-label="Search navigation" placeholder="Search tools…" value={query} onChange={e=>setQuery(e.target.value)}/></div>}
    <nav aria-label="Primary navigation">
      {role!=='reseller'&&<Link to={home} className="sidebar-tool sidebar-home" onClick={onNavigate} aria-label={sidebar.homeLabel||'Dashboard'} aria-current={location.pathname===home?'page':undefined}><LayoutDashboard aria-hidden="true"/>{!collapsed&&<span>{sidebar.homeLabel||'Dashboard'}</span>}</Link>}
      {groups.map((group,index)=>{
        const items=query.trim()?searchEntries(group.items.map(item=>({...item,section:group.title})),query):group.items
        if(!items.length)return null
        const id=`navigation-${mobile?'mobile':'desktop'}-${index}`
        const GroupIcon=sidebar.groupIcons&&toolGroupMeta(group.title,role,index).icon
        return <section key={group.title}>
          {!collapsed&&<button type="button" className="sidebar-section" aria-expanded={!!query||!isClosed(group)} aria-controls={id} onClick={()=>setGroup(key(group.title),!isClosed(group))}>{GroupIcon&&<GroupIcon className="sidebar-section-icon" aria-hidden="true"/>}<span>{group.title}</span><ChevronDown data-closed={isClosed(group)&&!query}/></button>}
          <div id={id} hidden={!collapsed&&!query&&isClosed(group)}>
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
