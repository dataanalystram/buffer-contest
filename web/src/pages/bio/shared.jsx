// Shared constants and helpers for the link-in-bio editor (web/src/pages/bio/*).
import {
  AtSign, Contact, Heading, Image as ImageIcon, LayoutGrid, Link2, Mail, Minus, Play, Sparkles, Type,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { get, upload } from '../../api'
import { useToast } from '../../ui'

// [type, label, icon, one-line description, tile colour]
export const BLOCKS = [
  ['link', 'Link', Link2, 'A button to any page', 'bg-zinc-900 text-white'],
  ['brand', 'Brand button', Sparkles, '90+ brand colours and logos', 'bg-gradient-to-br from-amber-400 via-pink-500 to-purple-600 text-white'],
  ['heading', 'Header', Heading, 'A title to group your links', 'bg-lav-200 text-lav-700'],
  ['embed', 'YouTube / Spotify', Play, 'Play a video or track on your page', 'bg-red-600 text-white'],
  ['email', 'Email signup', Mail, 'Collect newsletter subscribers', 'bg-sky-600 text-white'],
  ['contact', 'Contact card', Contact, 'Visitors save your vCard', 'bg-emerald-600 text-white'],
  ['image', 'Image', ImageIcon, 'A picture, optionally linked', 'bg-indigo-500 text-white'],
  ['posts', 'Latest posts', LayoutGrid, 'Your newest sent posts as a grid', 'bg-fuchsia-600 text-white'],
  ['socials', 'Social icons', AtSign, 'A row of icons to your profiles', 'bg-pink-500 text-white'],
  ['text', 'Text', Type, 'A short paragraph', 'bg-zinc-200 text-zinc-700'],
  ['spacer', 'Spacer', Minus, 'Space or a divider line', 'bg-zinc-200 text-zinc-700'],
]
export const META = Object.fromEntries(BLOCKS.map(([k, l, I, d, c]) => [k, { label: l, icon: I, desc: d, tone: c }]))

export const NEW = {
  link: { label: '', url: '' },
  brand: { url: '' },
  image: { src: 'https://', alt: '', url: '' },
  embed: { url: '' },
  email: { title: 'Get the newsletter', text: '', button: 'Subscribe', placeholder: 'you@example.com', success: 'Thanks! You’re on the list.', ask_name: false },
  contact: { name: 'Your name', org: '', job_title: '', email: '', phone: '', website: '', button: 'Save my contact' },
  posts: { count: 6, title: 'Latest posts' },
  socials: { items: [], auto: true },
  heading: { text: 'New section' },
  text: { text: '' },
  spacer: { size: 'm', divider: false },
}

export const SOCIAL_NETS = ['instagram', 'tiktok', 'youtube', 'x', 'threads', 'linkedin', 'facebook', 'github', 'bluesky', 'mastodon', 'pinterest', 'twitch', 'discord', 'spotify', 'website', 'email']
// social network -> LittleLink icon file (same map as services/bio.py SOCIAL_ICON)
export const SOCIAL_ICON = { website: 'generic-website', email: 'generic-email' }
export const socialIcon = (n) => `/static/bio/icons/${SOCIAL_ICON[n] || n}.svg`

// Pasted URL host -> brand key (anything else falls back to "second-level label == brand key or alias").
const HOST_BRAND = {
  'youtu.be': 'yt', 'youtube.com': 'yt', 'x.com': 'x', 'twitter.com': 'x', 'linkedin.com': 'linked', 'facebook.com': 'faceb',
  'fb.com': 'faceb', 'open.spotify.com': 'spotify', 'music.apple.com': 'apple-music', 'wa.me': 'whatsapp', 't.me': 'telegram',
  'buymeacoffee.com': 'coffee', 'cash.app': 'cash-app', 'apps.apple.com': 'appstore', 'play.google.com': 'playstore',
}
export const uid = () => 'b' + Math.random().toString(16).slice(2, 10)
export const isUrl = (s) => /^(https?:\/\/)?[^\s/]+\.[a-z]{2,}(\/\S*)?$/i.test((s || '').trim())
export const withScheme = (s) => (/^https?:\/\//i.test(s.trim()) ? s.trim() : `https://${s.trim()}`)
export const hostOf = (u) => { try { return new URL(withScheme(u)).hostname.replace(/^www\./, '') } catch { return '' } }
export const isEmbed = (u) => /(youtube\.com\/(watch|shorts|live)|youtu\.be\/|open\.spotify\.com\/)/i.test(u || '')

export function brandFor(url, brands) {
  const h = hostOf(url)
  if (!h) return null
  const key = HOST_BRAND[h] || Object.entries(HOST_BRAND).find(([d]) => h.endsWith('.' + d))?.[1] || h.split('.').slice(-2)[0]
  return brands.find((b) => b.key === key || b.aliases.includes(key)) || null
}

export function blockTitle(b) {
  if (b.type === 'link' || b.type === 'brand') return b.label || hostOf(b.url) || ''
  if (b.type === 'heading' || b.type === 'text') return b.text || ''
  if (b.type === 'image') return b.alt || b.caption || 'Image'
  if (b.type === 'embed') return b.url ? hostOf(b.url) : 'Paste a YouTube or Spotify link'
  if (b.type === 'email' || b.type === 'posts') return b.title || ''
  if (b.type === 'contact') return b.name || 'Contact card'
  if (b.type === 'socials') return b.auto && !b.items?.length ? 'Your connected channels' : `${b.items?.length || 0} profiles`
  if (b.type === 'spacer') return b.divider ? 'Divider line' : 'Space'
  return ''
}

// Brand catalogue + design catalogue, fetched once per session.
let brandsCache = null
let themesCache = null
export function useBrands() {
  const [b, setB] = useState(brandsCache || [])
  useEffect(() => { if (!brandsCache) get('/api/bio/brands').then((d) => { brandsCache = d.brands; setB(d.brands) }).catch(() => {}) }, [])
  return b
}
export function useThemes() {
  const [t, setT] = useState(themesCache)
  useEffect(() => { if (!themesCache) get('/api/bio/themes').then((d) => { themesCache = d; setT(d) }).catch(() => {}) }, [])
  return t
}

// @font-face for the font tiles (same self-hosted files the public page uses).
export function FontFaces({ fonts }) {
  if (!fonts) return null
  const css = fonts.map((f) => f.weights.map((w) => `@font-face{font-family:'${f.label}';font-weight:${w};font-display:swap;src:url(/bio/fonts/${f.key}-${w}.woff2) format('woff2')}`).join('')).join('')
  return <style>{css}</style>
}

// Upload one image through /api/media; crop='square' centre-crops it to 400x400 on the server. Returns its /m/... path.
export async function uploadSquare(file, crop = 'square') {
  const r = await upload([file], crop ? { crop } : {})
  if (r.errors?.length) throw new Error(r.errors[0])
  return r.media[0].public_path
}

export function useCopy() {
  const toast = useToast()
  return async (text, what = 'Link') => {
    try { await navigator.clipboard.writeText(text); toast(`${what} copied`) } catch { toast(text) }
  }
}

export function useDebounced(fn, ms, deps) {
  const t = useRef(null)
  const f = useRef(fn)
  f.current = fn
  useEffect(() => {
    clearTimeout(t.current)
    t.current = setTimeout(() => f.current(), ms)
    return () => clearTimeout(t.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
}

export function download(name, text, type = 'application/json') {
  const a = document.createElement('a')
  a.href = URL.createObjectURL(new Blob([text], { type }))
  a.download = name
  a.click()
  setTimeout(() => URL.revokeObjectURL(a.href), 1000)
}

// Downloads through a detached <a> (an <a download> inside an animated dialog is ignored by Chromium).
export function fetchFile(href, name) {
  const a = document.createElement('a')
  a.href = href
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
}

// A big rounded card section, Linktree-style.
export function Panel({ title, action, children, className = '', testid }) {
  return (
    <section data-testid={testid} className={`rounded-3xl border border-lav-200/70 bg-surface p-5 shadow-soft ${className}`}>
      {(title || action) && <header className="mb-4 flex items-center justify-between gap-2"><h3 className="text-[15px] font-semibold">{title}</h3>{action}</header>}
      {children}
    </section>
  )
}
