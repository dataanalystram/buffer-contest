// Light / dark theme: a class on <html>, remembered in localStorage, defaulting to the OS setting.
import { useEffect, useState } from 'react'

const KEY = 'huhq-theme'
export const getTheme = () => localStorage.getItem(KEY) || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
export function applyTheme(t) {
  document.documentElement.classList.toggle('dark', t === 'dark')
  document.documentElement.dataset.theme = t
}
export function setTheme(t) {
  localStorage.setItem(KEY, t)
  applyTheme(t)
  window.dispatchEvent(new CustomEvent('huhq:theme', { detail: t }))
}
export const toggleTheme = () => setTheme(getTheme() === 'dark' ? 'light' : 'dark')
export function useTheme() {
  const [t, setT] = useState(getTheme())
  useEffect(() => { const f = (e) => setT(e.detail); window.addEventListener('huhq:theme', f); return () => window.removeEventListener('huhq:theme', f) }, [])
  return t
}
