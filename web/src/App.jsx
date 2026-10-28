import { AnimatePresence, motion } from 'motion/react'
import {
  Bell, CalendarDays, ChartNoAxesColumn, ChevronDown, ChevronRight, Command, House, Lightbulb, LogOut, MessagesSquare, Moon, Plus, PlugZap,
  Search, Settings, ListOrdered, PenSquare, Lock, ArrowRight, Sun, PanelLeftClose, PanelLeftOpen, Bot, FileText, Contact, Images, Rocket,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { get, post } from './api'
import { go, href, parseHash, setQuery, useRoute } from './router'
import { StoreProvider, useStore } from './store'
import { Badge, Button, ChannelAvatar, cx, Input, Kbd, Skeleton, ToastProvider, useOutside, useToast, AskHost } from './ui'
import { toggleTheme, useTheme } from './theme'
import CircularText from './components/CircularText'
import Composer from './pages/Composer'
import Onboarding from './components/Onboarding'
import { AgentsBanner } from './components/AgentSafety'
import Library from './pages/Library'
import Contacts from './pages/Contacts'
import Home from './pages/Home'
import Channels from './pages/Channels'
import ChannelDetail from './pages/ChannelDetail'
import Queue from './pages/Queue'
import CalendarPage from './pages/Calendar'
import Ideas from './pages/Ideas'
import Community from './pages/Community'
import Insights from './pages/Insights'
import SettingsPage from './pages/Settings'
import Growth from './pages/Growth'
import { InviteAccept, ROLE_TONE, roleName } from './components/Team'

// [path, label, icon, shortcut, indent]  (Calendar is a view of Publish, like Buffer's List/Calendar toggle)
const NAV = [
  ['/home', 'Home', House, 'g h'],
  ['/ideas', 'Create', Lightbulb, 'g i'],
  ['/queue', 'Publish', ListOrdered, 'g p'],
  ['/calendar', 'Calendar', CalendarDays, 'g c', true],
  ['/community', 'Community', MessagesSquare, 'g m'],
  ['/contacts', 'Contacts', Contact, 'g o', true],
  ['/library', 'Library', Images, 'g l'],
  ['/insights', 'Insights', ChartNoAxesColumn, 'g a'],
  ['/growth', 'Growth', Rocket, 'g g'],
  ['/settings', 'Settings', Settings, 'g s'],
]
const logout = () => post('/api/auth/logout').then(() => { window.location.hash = '#/home'; window.location.reload() })

function Login({ onDone }) {
  const [status, setStatus] = useState(null)
  const [pw, setPw] = useState('')
  const [pw2, setPw2] = useState('')
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [need2fa, setNeed2fa] = useState(false)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { get('/api/auth/status').then(setStatus) }, [])
  const setup = status && !status.has_password
  async function submit(e) {
    e.preventDefault()
    setErr('')
    if (setup && pw !== pw2) return setErr('Passwords do not match')
    setBusy(true)
    try {
      await post(setup ? '/api/auth/setup' : '/api/auth/login', setup ? { password: pw } : { email: email.trim() || undefined, password: pw, code: need2fa ? code : undefined })
      onDone()
    } catch (e2) {
      if (e2.detail?.needs_2fa) setNeed2fa(true)
      setErr(e2.detail?.needs_2fa && !need2fa ? '' : e2.message)
    } finally { setBusy(false) }
  }
  return (
    <div className="bg-grid grid min-h-full place-items-center p-6">
      <motion.form onSubmit={submit} initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}
        className="w-full max-w-sm rounded-3xl border border-lav-200 bg-surface p-8 shadow-pop" aria-label="Log in" data-testid="login-form">
        <div className="mb-6 flex items-center gap-4">
          <CircularText size={84} text="HUHQUEUE • HUHBYTE • ">
            <img src="/static/mark.png" alt="" className="size-9" />
          </CircularText>
          <div>
            <h1 className="text-xl font-semibold">{setup ? 'Create your password' : 'Welcome back'}</h1>
            <p className="text-sm text-muted">{setup ? 'First run - protects this app on your laptop.' : 'Log in to HuhQueue.'}</p>
          </div>
        </div>
        <div className="space-y-3">
          {!setup && <Input type="email" placeholder="Email (teammates) - leave empty for the owner password" value={email} onChange={(e) => setEmail(e.target.value)} data-testid="login-email" aria-label="Email" autoComplete="username" />}
          <Input type="password" placeholder="Password" value={pw} onChange={(e) => setPw(e.target.value)} autoFocus data-testid="login-password" aria-label="Password" />
          {need2fa && <Input inputMode="numeric" autoComplete="one-time-code" placeholder="6-digit code or a recovery code" value={code} onChange={(e) => setCode(e.target.value)} autoFocus data-testid="login-code" aria-label="Two-factor code" />}
          {setup && <Input type="password" placeholder="Repeat password (8+ characters)" value={pw2} onChange={(e) => setPw2(e.target.value)} data-testid="login-password2" aria-label="Repeat password" />}
          {err && <p className="text-sm text-rose-600" role="alert">{err}</p>}
          <Button variant="primary" className="w-full justify-center" loading={busy} data-testid="login-submit"><Lock className="size-4" />{setup ? 'Create & continue' : 'Log in'}</Button>
        </div>
      </motion.form>
    </div>
  )
}

function ActionForm({ action, onRun, busy }) {
  const props = action.input_schema?.properties || {}
  const [vals, setVals] = useState({})
  const set = (k, v) => setVals({ ...vals, [k]: v })
  const parse = () => Object.fromEntries(Object.entries(vals).filter(([, v]) => v !== '' && v !== undefined).map(([k, v]) => {
    const t = props[k]?.type
    if (t === 'array') return [k, String(v).split(',').map((x) => x.trim()).filter(Boolean)]
    if (t === 'integer') return [k, Number(v)]
    if (t === 'object') { try { return [k, JSON.parse(v)] } catch { return [k, {}] } }
    return [k, v]
  }))
  return (
    <form className="space-y-2 p-4" data-testid="command-form" onSubmit={(e) => { e.preventDefault(); onRun(parse()) }}>
      <p className="text-sm font-semibold">{action.title} <Badge tone={action.confirm ? 'amber' : 'gray'}>{action.risk}</Badge></p>
      {Object.entries(props).map(([k, s]) => (
        <label key={k} className="flex items-center gap-2 text-xs text-ink-2">
          <span className="w-32 shrink-0">{k}{(action.input_schema.required || []).includes(k) && ' *'}</span>
          {s.type === 'boolean' ? <input type="checkbox" checked={!!vals[k]} onChange={(e) => set(k, e.target.checked)} data-testid={`command-field-${k}`} />
            : s.enum ? <select className="h-8 flex-1 rounded-lg border border-lav-200 bg-surface px-2" value={vals[k] || ''} onChange={(e) => set(k, e.target.value)} data-testid={`command-field-${k}`}><option value="" />{s.enum.map((x) => <option key={x}>{x}</option>)}</select>
              : <Input className="h-8 flex-1" placeholder={s.description || s.type} value={vals[k] || ''} onChange={(e) => set(k, e.target.value)} data-testid={`command-field-${k}`} />}
        </label>
      ))}
      <div className="flex justify-end"><Button size="sm" variant="primary" loading={busy} data-testid="command-form-run">{action.confirm ? 'Preview' : 'Run'}</Button></div>
    </form>
  )
}

function CommandPalette({ open, onClose }) {
  const { channels, reload } = useStore()
  const toast = useToast()
  const [q, setQ] = useState('')
  const [i, setI] = useState(0)
  const [acts, setActs] = useState([])
  const [panel, setPanel] = useState(null)   // {kind:'form'|'confirm'|'result'|'clarify', ...}
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (!open) return; const k = (e) => e.key === 'Escape' && onClose(); window.addEventListener('keydown', k); return () => window.removeEventListener('keydown', k) }, [open])
  useEffect(() => { if (open) { setQ(''); setI(0); setPanel(null); get('/api/actions?palette=1').then((r) => setActs(r.actions)).catch(() => {}) } }, [open])
  const finishRun = (r, label) => {
    if (r.status === 'needs_confirmation') return setPanel({ kind: 'confirm', preview: r.preview, token: r.confirm_token, message: r.message })
    if (r.status === 'client') { clientAction(r.nav); onClose(); return }
    if (r.status === 'clarify') return setPanel({ kind: 'clarify', message: r.message })
    if (r.status === 'error') return setPanel({ kind: 'clarify', message: r.message || 'That did not work.' })
    reload?.(); window.dispatchEvent(new Event('huhq:refresh'))
    setPanel({ kind: 'result', message: r.message || `${label || 'Done'}${r.status === 'pending_approval' ? ' - waiting for approval' : ''}`, output: r.output, status: r.status })
  }
  const clientAction = (nav) => { if (nav === 'theme') toggleTheme(); else if (nav?.startsWith('#')) window.location.hash = nav.slice(1); else if (nav) window.open(nav, '_blank') }
  const ask = async (text) => {
    setBusy(true)
    try { finishRun(await post('/api/assistant/command', { text, context: { route: window.location.hash } })) }
    catch (e) { setPanel({ kind: 'clarify', message: e.detail?.message || e.message }) } finally { setBusy(false) }
  }
  const runAction = async (a, input = {}) => {
    if (!a.method) { clientAction(a.nav); onClose(); return }
    setBusy(true)
    try { finishRun(await post(`/api/actions/${a.id}:run`, { input, dry_run: a.confirm }), a.title) }
    catch (e) { setPanel({ kind: 'clarify', message: e.detail?.message || e.message }) } finally { setBusy(false) }
  }
  const confirmRun = async () => {
    setBusy(true)
    try { finishRun(await post('/api/assistant/confirm', { confirm_token: panel.token }), 'Done') }
    catch (e) { setPanel({ kind: 'clarify', message: e.detail?.message || e.message }) } finally { setBusy(false) }
  }
  const items = useMemo(() => {
    const base = [
      ['New post', () => go(parseHash().path, { ...parseHash().query, compose: 'new' }), 'c'],
      ['New post to all channels', () => go(parseHash().path, { compose: 'new', channels: 'all' }), ''],
      ['New idea', () => go('/ideas', { new: '1' }), 'i'],
      ['Connect a channel', () => go('/channels'), ''],
      ['Toggle dark / light theme', () => toggleTheme(), 't'],
      ['Publish - list view', () => go('/queue'), ''],
      ['Calendar - week view', () => go('/calendar', { view: 'week' }), ''],
      ['Approvals waiting', () => go('/queue', { tab: 'approvals' }), ''],
      ['Insights - last 7 days', () => go('/insights', { range: '7' }), ''],
      ['Media library', () => go('/library'), ''],
      ['Contacts', () => go('/contacts'), ''],
      ['App keys (Instagram, YouTube, X…)', () => go('/settings', { section: 'app-keys' }), ''],
      ['AI provider (command bar, captions)', () => go('/settings', { section: 'ai' }), ''],
      ['For agents - OpenAPI, MCP, llms.txt', () => window.open('/agents', '_blank'), ''],
      ['Log out', () => logout(), ''],
      ...NAV.map(([p, l]) => [`Go to ${l}`, () => go(p), '']),
      ['Drafts', () => go('/queue', { tab: 'drafts' }), ''],
      ['Sent posts', () => go('/queue', { tab: 'sent' }), ''],
      ['Unanswered comments', () => go('/community', { status: 'open' }), ''],
      ['API tokens & MCP', () => go('/settings', { section: 'api' }), ''],
      ['Team - invite teammates & clients', () => go('/settings', { section: 'team' }), ''],
      ['Security - two-factor & sessions', () => go('/settings', { section: 'security' }), ''],
      ...channels.flatMap((c) => [
        [`Queue · @${c.username} (${c.provider})`, () => go(`/queue/${c.id}`), ''],
        [`New post for @${c.username}`, () => go(parseHash().path, { compose: 'new', channels: c.id }), ''],
        [`Posting goal & recommended times · @${c.username}`, () => go(`/channel/${c.id}`, { onboard: c.id }), ''],
      ]),
    ].map(([l, fn, k]) => ({ label: l, fn, k }))
    const reg = acts.filter((a) => a.method).map((a) => ({ label: a.title, sub: a.id, action: a, k: '' }))
    const words = q.toLowerCase().split(/\s+/).filter(Boolean)
    const hits = [...base, ...reg].filter((x) => words.every((w) => (x.label + ' ' + (x.sub || '')).toLowerCase().includes(w))).slice(0, 14)
    if (q.trim().split(/\s+/).length >= 2) hits.unshift({ label: `Ask HuhQueue: “${q.trim()}”`, ask: true, k: '↵' })
    return hits
  }, [q, channels, acts])
  if (!open) return null
  const run = (k) => {
    const it = items[k]
    if (!it) return
    if (it.ask) return ask(q.trim())
    if (it.action) {
      const req = it.action.input_schema?.required || []
      if (req.length || Object.keys(it.action.input_schema?.properties || {}).length) return setPanel({ kind: 'form', action: it.action })
      return runAction(it.action)
    }
    it.fn(); onClose()
  }
  return (
    <div className="fixed inset-0 z-[70] flex justify-center bg-night/40 pt-[12vh] backdrop-blur-sm" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <motion.div initial={{ opacity: 0, scale: 0.97, y: -8 }} animate={{ opacity: 1, scale: 1, y: 0 }} role="dialog" aria-label="Command palette" data-testid="command-palette"
        className="h-fit w-full max-w-xl overflow-hidden rounded-2xl border border-lav-200 bg-popover shadow-pop">
        <div className="flex items-center gap-2 border-b border-lav-100 px-4">
          <Search className="size-4 text-muted" />
          <input autoFocus value={q} onChange={(e) => { setQ(e.target.value); setI(0); setPanel(null) }} data-testid="command-input" aria-label="Search commands or type an instruction"
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown') { e.preventDefault(); setI((x) => Math.min(x + 1, items.length - 1)) }
              if (e.key === 'ArrowUp') { e.preventDefault(); setI((x) => Math.max(x - 1, 0)) }
              if (e.key === 'Enter') run(i)
              if (e.key === 'Escape') onClose()
            }}
            placeholder="Search, or tell HuhQueue what to do (“schedule the launch reel Friday 7pm on Instagram”)" className="h-14 flex-1 bg-transparent text-[15px] outline-none" />
          {busy ? <span className="size-4 animate-spin rounded-full border-2 border-lav-300 border-t-lav-600" data-testid="command-busy" /> : <Kbd>esc</Kbd>}
        </div>
        {panel?.kind === 'form' && <ActionForm action={panel.action} busy={busy} onRun={(inp) => runAction(panel.action, inp)} />}
        {panel?.kind === 'confirm' && (
          <div className="space-y-3 p-4" data-testid="command-confirm-panel">
            <p className="text-sm font-semibold">{panel.preview.title} <Badge tone="amber">{panel.preview.risk}</Badge></p>
            {panel.message && <p className="text-sm text-ink-2">{panel.message}</p>}
            <p className="rounded-xl bg-lav-50 p-3 text-xs text-ink-2" data-testid="command-preview">{panel.preview.summary}{panel.preview.count != null && ` · ${panel.preview.count} item(s)`}</p>
            {Object.values(panel.preview.issues || {}).flat().filter((x) => x.level === 'error').map((x, j) => <p key={j} className="text-xs text-rose-600">⚠ {x.message}</p>)}
            <div className="flex justify-end gap-2"><Button size="sm" variant="ghost" onClick={() => setPanel(null)} data-testid="command-cancel">Cancel</Button>
              <Button size="sm" variant="accent" loading={busy} onClick={confirmRun} data-testid="command-confirm">Confirm</Button></div>
          </div>
        )}
        {(panel?.kind === 'result' || panel?.kind === 'clarify') && (
          <div className="space-y-2 p-4" data-testid={panel.kind === 'result' ? 'command-result' : 'command-clarify'}>
            <p className="text-sm">{panel.kind === 'result' ? '✓ ' : ''}{panel.message}</p>
            {panel.kind === 'result' && Array.isArray(panel.output) && <ul className="max-h-48 space-y-1 overflow-y-auto text-xs text-ink-2">{panel.output.slice(0, 12).map((o, j) => <li key={j} className="truncate">• {o.caption || o.title || o.text || o.username || o.id}</li>)}</ul>}
          </div>
        )}
        {!panel && <ul className="scroll-thin max-h-96 overflow-y-auto p-2" role="listbox">
          {items.map((it, idx) => (
            <li key={it.label + idx} role="option" aria-selected={idx === i} data-testid={it.ask ? 'command-ask' : 'command-item'} onMouseEnter={() => setI(idx)} onClick={() => run(idx)}
              className={cx('flex cursor-pointer items-center justify-between rounded-xl px-3 py-2.5 text-sm', idx === i ? 'bg-lav-100 text-ink' : 'text-ink-2')}>
              <span className="flex items-center gap-2">{it.ask ? <Bot className="size-3.5 text-lav-600" /> : <ArrowRight className="size-3.5 text-lav-600" />}{it.label}{it.sub && <code className="text-[10px] text-muted">{it.sub}</code>}</span>
              {it.k && <Kbd>{it.k}</Kbd>}
            </li>
          ))}
          {!items.length && <li className="px-3 py-6 text-center text-sm text-muted">No matches - type a sentence to ask HuhQueue</li>}
        </ul>}
      </motion.div>
    </div>
  )
}

function Notifications() {
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState([])
  const ref = useRef(null)
  useOutside(ref, () => setOpen(false))
  const load = () => get('/api/notifications').then(setItems).catch(() => {})
  useEffect(() => { load(); const t = setInterval(load, 20000); return () => clearInterval(t) }, [])
  const unread = items.filter((i) => !i.read).length
  return (
    <div className="relative" ref={ref}>
      <Button variant="ghost" size="icon" aria-label={`Notifications (${unread} unread)`} data-testid="notifications-button" onClick={() => { setOpen(!open); if (!open && unread) post('/api/notifications/read').then(load) }}>
        <Bell className="size-[18px]" />
        {unread > 0 && <span className="absolute right-1.5 top-1.5 size-2 rounded-full bg-rose-500" />}
      </Button>
      <AnimatePresence>
        {open && (
          <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} data-testid="notifications-panel"
            className="absolute right-0 top-11 z-40 w-96 rounded-2xl border border-lav-200 bg-surface p-2 shadow-pop">
            <div className="px-3 py-2 text-sm font-semibold">Notifications</div>
            <ul className="max-h-96 space-y-1 overflow-y-auto">
              {items.map((n) => (
                <li key={n.id} className={cx('rounded-xl px-3 py-2 text-[13px] hover:bg-lav-50', !n.read && 'bg-lav-50/60')}>
                  <a href={n.target_id ? href('/queue', { post: n.target_id }) : n.kind === 'team' ? href('/settings', { section: 'team' }) : href('/home')} className="block" data-testid="notification-item">
                    <Badge tone={{ error: 'red', success: 'green', warning: 'amber' }[n.level] || 'lav'}>{n.kind}</Badge>
                    <p className="mt-1 text-ink-2">{n.message}</p>
                    <time className="text-[11px] text-muted">{new Date(n.at).toLocaleString()}</time>
                  </a>
                </li>
              ))}
              {!items.length && <li className="px-3 py-6 text-center text-sm text-muted">All quiet.</li>}
            </ul>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function UserChip() {
  const { me } = useStore()
  if (!me) return null
  return (
    <a href="#/settings?section=security" className="ml-1 flex items-center gap-2 rounded-xl px-2 py-1 hover:bg-lav-50" data-testid="user-chip" title={`${me.name} · ${me.email}`}>
      <span className="grid size-7 place-items-center rounded-full bg-lav-200 text-[11px] font-semibold text-lav-900">{(me.name || '?').split(/\s+/).map((x) => x[0]).join('').slice(0, 2).toUpperCase()}</span>
      <span className="hidden text-left leading-tight md:block"><span className="block text-[13px] font-medium" data-testid="user-name">{me.name}</span><Badge tone={ROLE_TONE[me.role]} data-testid="user-role">{roleName(me.role)}</Badge></span>
    </a>
  )
}

function ChannelRow({ c, route, collapsed }) {
  const [open, setOpen] = useState(route.parts[1] === c.id)
  const here = route.parts[1] === c.id
  const n = c.counts?.scheduled || 0
  return (
    <li>
      <div className={cx('group flex items-center rounded-xl', here ? 'bg-white/10' : 'hover:bg-white/5')}>
        <a href={`#/queue/${c.id}`} data-testid="sidebar-channel" title={collapsed ? `@${c.username}` : undefined}
          className={cx('flex min-w-0 flex-1 items-center gap-3 py-2 text-sm', collapsed ? 'justify-center px-2' : 'pl-3', here ? 'text-white' : 'text-white/75')}>
          <ChannelAvatar ch={c} size={28} />
          {!collapsed && <span className="min-w-0 flex-1 leading-tight">
            <span className="block truncate">{c.username}</span>
            <span className="block text-[11px] text-white/45" data-testid="sidebar-channel-count">{c.status !== 'active' ? 'Reconnect needed' : `${n} scheduled post${n === 1 ? '' : 's'}`}</span>
          </span>}
        </a>
        {!collapsed && (c.status !== 'active' ? <Badge tone="red" className="mr-2">!</Badge> : (
          <button aria-label={`${open ? 'Collapse' : 'Expand'} ${c.username}`} aria-expanded={open} onClick={() => setOpen(!open)} data-testid="sidebar-channel-toggle"
            className="mr-1 cursor-pointer rounded-lg p-1.5 text-white/40 hover:bg-white/10 hover:text-white">{open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}</button>
        ))}
      </div>
      <AnimatePresence initial={false}>
        {open && !collapsed && (
          <motion.ul initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="ml-7 overflow-hidden border-l border-white/10 pl-3" data-testid="sidebar-channel-menu">
            {[['Publish', `#/queue/${c.id}`, n], ['Community', `#/channel/${c.id}?tab=community`], ['Insights', `#/channel/${c.id}?tab=insights`]].map(([l, h, k]) => (
              <li key={l}><a data-testid="app-a" href={h} className="flex items-center justify-between rounded-lg px-2 py-1.5 text-[13px] text-white/60 hover:bg-white/5 hover:text-white">{l}{k !== undefined && <span className="text-[11px] text-white/40">{k}</span>}</a></li>
            ))}
          </motion.ul>
        )}
      </AnimatePresence>
    </li>
  )
}

function Sidebar({ route, collapsed, setCollapsed }) {
  const { channels } = useStore()
  const theme = useTheme()
  const active = (p) => route.path === p || route.path.startsWith(p + '/')
  const main = NAV.filter(([p]) => p !== '/settings')
  return (
    <aside className={cx('flex shrink-0 flex-col bg-night text-white transition-[width] duration-200', collapsed ? 'w-[76px]' : 'w-[264px]')} aria-label="Main navigation" data-testid="sidebar">
      <div className={cx('flex h-16 items-center', collapsed ? 'justify-center' : 'justify-between px-5')}>
        {!collapsed && <a href="#/home" aria-label="HuhQueue home"><img src="/static/logo.png" alt="HuhByte AI" className="h-7 w-auto" /></a>}
        <button onClick={() => setCollapsed(!collapsed)} aria-label={collapsed ? 'Open sidebar' : 'Close sidebar'} data-testid="sidebar-collapse"
          className="cursor-pointer rounded-lg p-1.5 text-white/40 hover:bg-white/10 hover:text-white">{collapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}</button>
      </div>
      <div className={collapsed ? 'px-3' : 'px-4'}>
        <Button variant="accent" size="lg" className={cx('shine w-full justify-center', collapsed && 'px-0')} data-testid="new-post" aria-label="Create post" onClick={() => go(route.path, { ...route.query, compose: 'new' })}>
          <PenSquare className="size-4" />{!collapsed && <>Create post <span className="ml-auto rounded bg-black/10 px-1.5 text-[10px]">C</span></>}
        </Button>
      </div>
      <nav className="mt-4 space-y-0.5 px-3">
        {main.map(([p, l, I, , sub]) => (
          <a key={p} href={'#' + p} data-testid={`nav-${l.toLowerCase()}`} aria-current={active(p) ? 'page' : undefined} title={collapsed ? l : undefined}
            className={cx('relative flex items-center gap-3 rounded-xl py-2 text-sm transition-colors', collapsed ? 'justify-center px-2' : sub ? 'ml-5 px-3 text-[13px]' : 'px-3',
              active(p) ? 'text-white' : 'text-white/60 hover:bg-white/5 hover:text-white')}>
            {active(p) && <motion.span layoutId="nav-active" className="absolute inset-0 rounded-xl bg-white/10" transition={{ type: 'spring', stiffness: 500, damping: 40 }} />}
            {active(p) && <span className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-full bg-[#c9acfa]" />}
            <I className={cx('relative', sub ? 'size-4' : 'size-[18px]')} />{!collapsed && <span className="relative">{l}</span>}
          </a>
        ))}
      </nav>
      <div className={cx('mt-6 flex items-center text-[11px] font-semibold uppercase tracking-widest text-white/40', collapsed ? 'justify-center' : 'justify-between px-6')}>
        {!collapsed && <span>Channels <span className="font-normal normal-case tracking-normal text-white/30">· {channels.length}</span></span>}
        <a href="#/channels" className="rounded-md p-1 hover:bg-white/10" aria-label="Connect channel" data-testid="sidebar-connect"><Plus className="size-3.5" /></a>
      </div>
      <ul className="scroll-thin mt-2 flex-1 space-y-0.5 overflow-y-auto px-3 pb-4">
        {channels.map((c) => <ChannelRow key={c.id} c={c} route={route} collapsed={collapsed} />)}
        {!collapsed && <li>
          <a data-testid="app-connect-channel" href="#/channels" className="mt-1 flex items-center gap-3 rounded-xl border border-dashed border-white/15 px-3 py-2 text-sm text-white/60 hover:border-white/30 hover:text-white">
            <PlugZap className="size-4" />Connect channel
          </a>
        </li>}
      </ul>
      <div className={cx('space-y-0.5 border-t border-white/10 p-3')}>
        <a href="#/settings" data-testid="nav-settings" aria-current={active('/settings') ? 'page' : undefined} title={collapsed ? 'Settings' : undefined}
          className={cx('flex items-center gap-3 rounded-xl py-2 text-sm', collapsed ? 'justify-center px-2' : 'px-3', active('/settings') ? 'bg-white/10 text-white' : 'text-white/60 hover:bg-white/5 hover:text-white')}>
          <Settings className="size-[18px]" />{!collapsed && 'Settings'}
        </a>
        <button onClick={toggleTheme} data-testid="sidebar-theme" aria-label="Toggle theme" title={collapsed ? 'Toggle theme' : undefined}
          className={cx('flex w-full cursor-pointer items-center gap-3 rounded-xl py-2 text-sm text-white/60 hover:bg-white/5 hover:text-white', collapsed ? 'justify-center px-2' : 'px-3')}>
          {theme === 'dark' ? <Sun className="size-[18px]" /> : <Moon className="size-[18px]" />}{!collapsed && (theme === 'dark' ? 'Light mode' : 'Dark mode')}
        </button>
      </div>
    </aside>
  )
}

function Shell() {
  const route = useRoute()
  const { ready } = useStore()
  const theme = useTheme()
  const [palette, setPalette] = useState(false)
  const [collapsed, setCollapsedState] = useState(localStorage.getItem('huhq-rail') === 'min')
  const setCollapsed = (v) => { localStorage.setItem('huhq-rail', v ? 'min' : ''); setCollapsedState(v) }
  useEffect(() => {
    let g = false
    const keys = (e) => {
      const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName) || document.activeElement?.isContentEditable
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setPalette((x) => !x); return }
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return
      const r = parseHash()
      if (g) {
        g = false
        const hit = NAV.find(([, , , k]) => k === 'g ' + e.key)
        if (hit) go(hit[0])
        return
      }
      if (e.key === 'g') { g = true; setTimeout(() => (g = false), 800) }
      if (e.key === 'c') go(r.path, { ...r.query, compose: 'new' })
      if (e.key === 't') toggleTheme()
      if (e.key === '/') { e.preventDefault(); setPalette(true) }
      if (e.key === '?') go('/settings', { section: 'shortcuts' })
    }
    window.addEventListener('keydown', keys)
    return () => window.removeEventListener('keydown', keys)
  }, [])
  useAgentContext(route)
  const [first] = route.parts
  const page = {
    home: <Home />, ideas: <Ideas />, queue: <Queue />, calendar: <CalendarPage />, community: <Community />,
    insights: <Insights />, settings: <SettingsPage />, library: <Library />, contacts: <Contacts />, growth: <Growth />, channels: <Channels />, channel: <ChannelDetail />, connect: <Channels />,
  }[first || 'home'] || <Home />
  return (
    <div className="flex h-full">
      <Sidebar route={route} collapsed={collapsed} setCollapsed={setCollapsed} />
      <div className="flex min-w-0 flex-1 flex-col">
        <AgentsBanner />
        <header className="flex h-16 shrink-0 items-center gap-3 border-b border-lav-200/70 bg-surface/70 px-6 backdrop-blur">
          <button onClick={() => setPalette(true)} data-testid="open-command-palette" aria-label="Open command palette"
            className="flex h-10 w-full max-w-md cursor-pointer items-center gap-2 rounded-xl border border-lav-200 bg-lav-50 px-3 text-sm text-muted transition hover:border-lav-400">
            <Search className="size-4" />Search or run a command… <span className="ml-auto flex gap-1"><Kbd><Command className="inline size-2.5" /></Kbd><Kbd>K</Kbd></span>
          </button>
          <div className="ml-auto flex items-center gap-1">
            <Button variant="ghost" size="icon" aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'} data-testid="theme-toggle" onClick={toggleTheme}>
              {theme === 'dark' ? <Sun className="size-[18px]" /> : <Moon className="size-[18px]" />}
            </Button>
            <Notifications />
            <UserChip />
            <Button variant="ghost" size="icon" aria-label="Log out" data-testid="logout" onClick={logout}><LogOut className="size-[18px]" /></Button>
          </div>
        </header>
        <main className="scroll-thin flex-1 overflow-y-auto" id="main">
          {ready ? (
            // enter-only transition: an exit animation can stall if the old page keeps re-rendering on the new route
            <motion.div key={first} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.18 }} className="mx-auto max-w-7xl p-6 lg:p-8">
              {page}
            </motion.div>
          ) : (
            <div className="mx-auto max-w-7xl space-y-4 p-8" data-testid="app-loading" aria-busy="true">
              <Skeleton className="h-36 w-full rounded-3xl" />
              <div className="grid gap-4 md:grid-cols-3"><Skeleton className="h-28" /><Skeleton className="h-28" /><Skeleton className="h-28" /></div>
              <Skeleton className="h-64 w-full rounded-2xl" />
            </div>
          )}
        </main>
      </div>
      {route.query.compose && ready && <Composer key={route.query.compose} />}
      {ready && (route.query.onboard || (first === 'channel' && route.parts[1] && route.query.connected === '1')) &&
        <Onboarding key={route.query.onboard || route.parts[1]} cid={route.query.onboard || route.parts[1]} onClose={() => setQuery({ onboard: '', connected: '' })} />}
      <CommandPalette open={palette} onClose={() => setPalette(false)} />
    </div>
  )
}

// Headless / agent-first: keep <script type="application/json" id="agent-context"> describing the current page
// (what it shows, its actions with REST/MCP names and required fields, and the IDs on it) - from GET /api/context.
function useAgentContext(route) {
  const key = window.location.hash.replace(/^#/, '') || '/home'
  useEffect(() => {
    let alive = true
    const load = () => get(`/api/context?page=${encodeURIComponent(window.location.hash.replace(/^#/, '') || '/home')}`).then((ctx) => {
      if (!alive) return
      let el = document.getElementById('agent-context')
      if (!el) { el = document.createElement('script'); el.type = 'application/json'; el.id = 'agent-context'; document.head.appendChild(el) }
      el.textContent = JSON.stringify(ctx)
      el.dataset.page = ctx.page
      document.documentElement.dataset.page = ctx.route || ''
    }).catch(() => {})
    load()
    window.addEventListener('huhq:posts-changed', load)
    return () => { alive = false; window.removeEventListener('huhq:posts-changed', load) }
  }, [key])
}

export default function App() {
  const [auth, setAuth] = useState(null)
  const check = () => get('/api/auth/status').then((s) => setAuth(s.logged_in)).catch(() => setAuth(false))
  useEffect(() => {
    check()
    const out = () => setAuth(false)
    window.addEventListener('huhq:logout', out)
    return () => window.removeEventListener('huhq:logout', out)
  }, [])
  const route = useRoute()
  if (auth === null) return null
  const invite = route.parts[0] === 'invite' && route.parts[1]
  return (
    <ToastProvider>
      {invite ? <InviteAccept token={route.parts[1]} onDone={() => window.location.reload()} />
        : auth ? <StoreProvider><Shell /></StoreProvider> : <Login onDone={() => setAuth(true)} />}
      <AskHost />
    </ToastProvider>
  )
}
