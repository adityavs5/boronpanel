import { useState } from 'react'
import { Copy, Check, ChevronDown, ChevronUp } from 'lucide-react'
import { toast } from './Toast'
export function LongValue({value,label='value'}){
 const [expanded,setExpanded]=useState(false),[copied,setCopied]=useState(false)
 async function copy(){try{await navigator.clipboard.writeText(String(value));setCopied(true);setTimeout(()=>setCopied(false),2000)}catch{toast.error('Could not copy', 'Select the expanded value and copy it manually.')}}
 return <div className="long-value" data-expanded={expanded}><span className="long-value-text">{value||'—'}</span>{value&&<><button type="button" aria-label={`Copy ${label}`} onClick={e=>{e.stopPropagation();copy()}}>{copied?<Check/>:<Copy/>}</button><button type="button" aria-label={`${expanded?'Collapse':'Expand'} ${label}`} aria-expanded={expanded} onClick={e=>{e.stopPropagation();setExpanded(!expanded)}}>{expanded?<ChevronUp/>:<ChevronDown/>}</button></>}</div>
}
