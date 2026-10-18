import { Children, Fragment, cloneElement, forwardRef, isValidElement, useId } from 'react'
import * as LabelPrimitive from '@radix-ui/react-label'
import { cn } from '@/lib/cn'
import { Select } from './Select'
import { StyledSelect } from './StyledSelect'
import { Checkbox, Switch } from './Toggle'

const baseField =
  'flex w-full rounded-btn border border-input bg-input-surface px-3 text-sm text-foreground shadow-sm placeholder:text-muted-foreground/70 transition-[border-color,box-shadow] focus-visible:outline-none focus-visible:border-accent focus-visible:ring-[3px] focus-visible:ring-ring/25 disabled:cursor-not-allowed disabled:opacity-50'

export const Input = forwardRef(function Input({ className, invalid, type = 'text', ...props }, ref) {
  return (
    <input
      ref={ref}
      type={type}
      aria-invalid={invalid || undefined}
      className={cn(baseField, 'h-9', invalid && 'border-danger focus-visible:border-danger focus-visible:ring-danger/20', className)}
      {...props}
    />
  )
})

export const Textarea = forwardRef(function Textarea({ className, invalid, rows = 4, ...props }, ref) {
  return (
    <textarea
      ref={ref}
      rows={rows}
      aria-invalid={invalid || undefined}
      className={cn(baseField, 'py-2 min-h-[72px] font-mono text-xs leading-relaxed', invalid && 'border-danger focus-visible:border-danger focus-visible:ring-danger/20', className)}
      {...props}
    />
  )
})

export const Label = forwardRef(function Label({ className, required, ...props }, ref) {
  return (
    <LabelPrimitive.Root
      ref={ref}
      className={cn('text-sm font-medium text-foreground leading-none flex items-center gap-1', className)}
      {...props}
    >
      {props.children}
      {required && <><span className="text-danger" aria-hidden="true">*</span><span className="sr-only">required</span></>}
    </LabelPrimitive.Root>
  )
})

const mergeIds = (...values) => [...new Set(values.filter(Boolean).join(' ').split(/\s+/))].join(' ') || undefined
const isFieldControl = node => isValidElement(node) && (
  ['input', 'select', 'textarea'].includes(node.type) && node.props.type !== 'hidden' ||
  [Input, Textarea, Select, StyledSelect, Checkbox, Switch].includes(node.type)
)

// Descend through layout elements and fragments, but never through another
// field or a component's implementation. Composite controls can opt in by ID.
function fieldControls(children, htmlFor, result, implicitLabels, insideLabel = false) {
  Children.forEach(children, child => {
    if (!isValidElement(child)) return
    if (isFieldControl(child) || htmlFor && child.props.id === htmlFor) {
      result.push(child)
      if (insideLabel) implicitLabels.add(child)
    } else if (typeof child.type === 'string' || child.type === Fragment) fieldControls(child.props.children, htmlFor, result, implicitLabels, insideLabel || child.type === 'label')
  })
  return result
}

// A single control keeps its visible label through layout wrappers. Multiple
// controls share a named group and retain their own individual accessible names.
export function FormField({ label, htmlFor, required, optional, error, hint, children, className }) {
  const generated = useId()
  const implicitLabels = new Set()
  const controls = fieldControls(children, htmlFor, [], implicitLabels)
  const singleControl = controls.length === 1 && !implicitLabels.has(controls[0]) ? controls[0] : null
  const target = htmlFor ? controls.find(control => control.props.id === htmlFor) || singleControl : singleControl
  // Preserve the direct custom-control contract for consumers outside this kit.
  const onlyChild = Children.count(children) === 1 && isValidElement(children) ? children : null
  const directControl = !controls.length && onlyChild && typeof onlyChild.type !== 'string' && onlyChild.type !== Fragment ? onlyChild : null
  const labelledControl = target || directControl
  const fieldId = htmlFor || labelledControl?.props.id || `field-${generated.replace(/:/g, '')}`
  const messageId = error || hint ? `${fieldId}-message` : undefined
  function bind(nodes) {
    return Children.map(nodes, child => {
      if (!isValidElement(child)) return child
      if (child === labelledControl || controls.includes(child)) return cloneElement(child, {
        ...(child === labelledControl ? { id: child.props.id || fieldId } : {}),
        ...(typeof child.type !== 'string' && [Input, Textarea, Select, StyledSelect].includes(child.type) ? { invalid: child.props.invalid || !!error } : {}),
        'aria-labelledby': child.props['aria-labelledby'] || (child === labelledControl && label && !child.props['aria-label'] ? `${fieldId}-label` : undefined),
        'aria-invalid': child.props['aria-invalid'] || child.props.invalid || !!error || undefined,
        'aria-describedby': mergeIds(child.props['aria-describedby'], messageId),
      })
      if (typeof child.type === 'string' || child.type === Fragment) return cloneElement(child, {}, bind(child.props.children))
      return child
    })
  }
  return (
    <div className={cn('form-field space-y-1.5', className)} role={!labelledControl && label ? 'group' : undefined} aria-labelledby={!labelledControl && label ? `${fieldId}-label` : undefined}>
      {label && (
        <Label id={`${fieldId}-label`} htmlFor={labelledControl || htmlFor ? fieldId : undefined} required={required}>
          {label}{optional && <span className="field-optional"> (optional)</span>}
        </Label>
      )}
      {bind(children)}
      {error ? (
        <p id={messageId} className="text-xs text-danger" role="alert">{error}</p>
      ) : hint ? (
        <p id={messageId} className="text-xs text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  )
}
