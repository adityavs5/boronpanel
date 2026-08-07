import { Children, cloneElement, forwardRef, isValidElement, useId } from 'react'
import * as LabelPrimitive from '@radix-ui/react-label'
import { cn } from '@/lib/cn'

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

// A labeled field wrapper with optional hint + inline validation error.
export function FormField({ label, htmlFor, required, error, hint, children, className }) {
  const generated = useId()
  const fieldId = htmlFor || (isValidElement(children) ? children.props.id : null) || `field-${generated.replace(/:/g, '')}`
  const messageId = error || hint ? `${fieldId}-message` : undefined
  const onlyChild = Children.count(children) === 1 ? Children.only(children) : null
  let control = children
  if (isValidElement(onlyChild)) {
    const intrinsicGroup = typeof onlyChild.type === 'string' && ['div', 'fieldset'].includes(onlyChild.type)
    control = cloneElement(onlyChild, intrinsicGroup ? {
      role: onlyChild.props.role || 'group',
      'aria-labelledby': onlyChild.props['aria-labelledby'] || `${fieldId}-label`,
      'aria-describedby': onlyChild.props['aria-describedby'] || messageId,
    } : {
      id: onlyChild.props.id || fieldId,
      invalid: onlyChild.props.invalid || !!error,
      'aria-describedby': onlyChild.props['aria-describedby'] || messageId,
    })
  }
  return (
    <div className={cn('space-y-1.5', className)}>
      {label && (
        <Label id={`${fieldId}-label`} htmlFor={fieldId} required={required}>
          {label}
        </Label>
      )}
      {control}
      {error ? (
        <p id={messageId} className="text-xs text-danger" role="alert">{error}</p>
      ) : hint ? (
        <p id={messageId} className="text-xs text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  )
}
