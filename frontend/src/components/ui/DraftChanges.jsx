import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useBlocker } from 'react-router-dom'
import { Button } from './Button'
import { ConfirmDialog } from './Dialog'
import { StyledSelect } from './StyledSelect'
const Context=createContext(null)
export function useDraftSection(key,draft){
 const register=useContext(Context)
 useEffect(()=>register?.(key,draft),[register,key,draft.dirty,draft.busy,draft.onSave,draft.onDiscard,draft.label,draft.disabled])
}
export function DraftChanges({children}){
 const drafts=useRef(new Map()),[,refresh]=useState(0),[saving,setSaving]=useState(false),[selected,setSelected]=useState('')
 const register=useCallback((key,draft)=>{drafts.current.set(key,draft);refresh(v=>v+1);return()=>{drafts.current.delete(key);refresh(v=>v+1)}},[])
 const context=useMemo(()=>register,[register])
 const active=[...drafts.current.entries()].filter(([,d])=>d.dirty).map(([key,draft])=>({...draft,key}))
 const savable=active.filter(d=>typeof d.onSave==='function')
 const chosen=savable.find(d=>d.key===selected)||savable[0]
 const dirty=active.length>0,busy=saving||active.some(d=>d.busy)
 const blocker=useBlocker(dirty)
 useEffect(()=>{if(!dirty)return;const guard=e=>{e.preventDefault();e.returnValue=''};window.addEventListener('beforeunload',guard);return()=>window.removeEventListener('beforeunload',guard)},[dirty])
 function discard(){active.forEach(d=>d.onDiscard())}
 async function save(){setSaving(true);try{await chosen?.onSave()}catch{/* Existing mutation shows the operation's specific error. */}finally{setSaving(false)}}
 const region=document.getElementById('panel-save-region')
 return <Context.Provider value={context}>{children}{dirty&&region&&createPortal(<div className="sticky-save-bar" role="region" aria-label="Unsaved changes"><strong>Unsaved changes{active.length>1?` (${active.length} sections)`:''}</strong><div className="draft-save-controls">{savable.length>1&&<StyledSelect aria-label="Section to save" value={chosen?.key} onChange={e=>setSelected(e.target.value)} disabled={busy}>{savable.map(d=><option key={d.key} value={d.key}>{d.label}</option>)}</StyledSelect>}<Button variant="secondary" onClick={discard} disabled={busy}>Discard</Button>{savable.length>0&&<Button onClick={save} loading={busy} disabled={!!chosen?.disabled}>Save</Button>}</div></div>,region)}<ConfirmDialog open={blocker.state==='blocked'} onOpenChange={open=>{if(!open&&blocker.state==='blocked')blocker.reset()}} title="Discard unsaved changes?" description="Your unsaved edits will be lost. Saved settings will stay unchanged." variant="primary" confirmLabel="Discard and leave" cancelLabel="Keep editing" onConfirm={()=>{discard();blocker.proceed()}}/></Context.Provider>
}
