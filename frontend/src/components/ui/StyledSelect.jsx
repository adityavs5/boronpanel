import { Children, forwardRef } from 'react'
import * as Primitive from '@radix-ui/react-select'
import { Check, ChevronDown, ChevronUp } from 'lucide-react'
import { cn } from '@/lib/cn'
const empty='__boron_empty_option__'
// Native-compatible event shape keeps existing page payloads unchanged.
export const StyledSelect=forwardRef(function StyledSelect({children,value,onChange,className,disabled,name,required,invalid,...props},ref){
 const options=Children.toArray(children).filter(Boolean)
 return <Primitive.Root value={String(value??'')||empty} onValueChange={next=>onChange?.({target:{value:next===empty?'':next}})} disabled={disabled} name={name} required={required}>
  <Primitive.Trigger ref={ref} className={cn('tool-select',className)} aria-invalid={invalid||undefined} {...props}><Primitive.Value/><Primitive.Icon><ChevronDown aria-hidden="true"/></Primitive.Icon></Primitive.Trigger>
  <Primitive.Portal><Primitive.Content className="tool-select-menu" position="popper" sideOffset={4}><Primitive.ScrollUpButton><ChevronUp/></Primitive.ScrollUpButton><Primitive.Viewport>{options.map(option=><Primitive.Item className="tool-select-option" key={String(option.props.value)} value={String(option.props.value??'')||empty} disabled={option.props.disabled}><Primitive.ItemText>{option.props.children}</Primitive.ItemText><Primitive.ItemIndicator className="tool-select-option-indicator"><Check/></Primitive.ItemIndicator></Primitive.Item>)}</Primitive.Viewport><Primitive.ScrollDownButton><ChevronDown/></Primitive.ScrollDownButton></Primitive.Content></Primitive.Portal>
 </Primitive.Root>
})
