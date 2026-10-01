import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import * as Dialog from '@radix-ui/react-dialog'
import { useUI } from '@/store/ui'
import { Sidebar } from './Sidebar'
export function MobileNavDrawer(){
  const open=useUI(s=>s.mobileNavOpen),setOpen=useUI(s=>s.setMobileNavOpen),location=useLocation()
  useEffect(()=>setOpen(false),[location.pathname,location.search,setOpen])
  useEffect(()=>{
    const media=window.matchMedia('(min-width: 1024px)')
    const close=()=>{if(media.matches)setOpen(false)}
    media.addEventListener('change',close);return()=>media.removeEventListener('change',close)
  },[setOpen])
  return <Dialog.Root open={open} onOpenChange={setOpen}><Dialog.Portal><Dialog.Overlay className="inner-drawer-overlay"/><Dialog.Content className="inner-drawer" aria-describedby={undefined} onCloseAutoFocus={event=>{event.preventDefault();if(!useUI.getState().paletteOpen)document.querySelector('.inner-menu-button')?.focus()}}><Dialog.Title className="sr-only">Panel navigation</Dialog.Title><Sidebar mobile onNavigate={()=>setOpen(false)}/></Dialog.Content></Dialog.Portal></Dialog.Root>
}
