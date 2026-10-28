// URL-addressable state: every screen, tab and filter lives in the hash (#/queue/ch_123?tab=drafts),
// so a human or an AI agent can deep-link straight to any state without clicking through menus.
import { useEffect, useState } from 'react'

export function parseHash() {
  const raw = window.location.hash.replace(/^#/, '') || '/home'
  const [path, qs] = raw.split('?')
  const parts = path.split('/').filter(Boolean)
  return { path: '/' + parts.join('/'), parts, query: Object.fromEntries(new URLSearchParams(qs || '')) }
}

export function href(path, query = {}) {
  const q = new URLSearchParams(Object.entries(query).filter(([, v]) => v !== undefined && v !== null && v !== ''))
  const s = q.toString()
  return '#' + path + (s ? '?' + s : '')
}

export function go(path, query) { window.location.hash = href(path, query).slice(1) }

export function setQuery(patch) {
  const { path, query } = parseHash()
  const next = { ...query, ...patch }
  window.history.replaceState(null, '', href(path, next))
  window.dispatchEvent(new HashChangeEvent('hashchange'))
}

export function useRoute() {
  const [r, setR] = useState(parseHash())
  useEffect(() => {
    const on = () => setR(parseHash())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return r
}
