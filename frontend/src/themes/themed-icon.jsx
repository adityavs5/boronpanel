import { forwardRef } from 'react'
import { activeTheme } from './runtime'

// Wraps one lucide icon so it draws from the active theme's icon set, which is
// how every page changes icons without knowing about themes. A theme without a
// set (Modern) keeps lucide.
export function themed(name, Lucide) {
  const Icon = forwardRef(function Icon({ size = 24, strokeWidth, absoluteStrokeWidth, className, ...props }, ref) {
    const Themed = activeTheme()?.icons?.[name]
    if (!Themed) return <Lucide ref={ref} size={size} strokeWidth={strokeWidth} absoluteStrokeWidth={absoluteStrokeWidth} className={className} {...props} />
    return <Themed size={size} className={className ? `theme-icon ${className}` : 'theme-icon'} {...props} />
  })
  Icon.displayName = name
  return Icon
}
