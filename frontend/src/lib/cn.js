import { clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

// Merge conditional class names and de-dupe conflicting Tailwind utilities
// (the standard shadcn/ui helper). Used by every UI primitive.
export function cn(...inputs) {
  return twMerge(clsx(inputs))
}
