// shadcn/ui's `cn` helper: join class names and let later Tailwind classes win over earlier ones.
import { clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs) {
  return twMerge(clsx(inputs))
}
