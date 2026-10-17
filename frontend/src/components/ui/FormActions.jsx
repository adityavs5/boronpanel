import { useLayoutEffect, useRef } from 'react'

// Keep form actions directly above the responsive bottom navigation.
export function FormActions({ children, className = '' }) {
  const ref = useRef(null)
  useLayoutEffect(() => {
    const navigation = document.querySelector('[aria-label="Mobile navigation"]')
    const update = () => ref.current?.style.setProperty('--form-sticky-offset', `${navigation?.getBoundingClientRect().height || 0}px`)
    update()
    const observer = new ResizeObserver(update)
    if (navigation) observer.observe(navigation)
    return () => observer.disconnect()
  }, [])
  return <div ref={ref} className={`form-actions ${className}`}>{children}</div>
}
