import { cloneElement, isValidElement, useId } from 'react'
export function SettingRow({label,description,children,error,htmlFor}){
 const generated=useId(),id=htmlFor||generated
 const described=[description?`${id}-description`:null,error?`${id}-error`:null].filter(Boolean).join(' ')||undefined
 return <div className="setting-row"><div><label htmlFor={id} className="setting-row-label">{label}</label>{description&&<p id={`${id}-description`} className="setting-row-description">{description}</p>}</div><div className="setting-row-control">{isValidElement(children)?cloneElement(children,{id,'aria-describedby':described,'aria-invalid':error?true:undefined}):children}{error&&<p id={`${id}-error`} className="setting-row-error" role="alert">{error}</p>}</div></div>
}
