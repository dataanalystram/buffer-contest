// Small shadcn/ui-style component kit (own implementation, same API ideas: variants, composable parts).
import { AnimatePresence, motion } from 'motion/react'
import { AlertCircle, CheckCircle2, Sparkles, X, Loader2 } from 'lucide-react'
import { Instagram, Youtube, TikTok, Facebook, Linkedin, XLogo, Threads, Bluesky } from './components/BrandIcons'
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'

import { cn } from './lib/utils'

export { cn }
export const cx = cn

const BTN = {
  primary: 'bg-ink text-surface hover:opacity-90 shadow-soft',
  accent: 'bg-lav-400 text-[#141020] hover:brightness-105 shadow-soft',
  lav: 'bg-lav-600 text-white hover:brightness-110 shadow-soft',
  soft: 'bg-lav-100 text-ink hover:bg-lav-200',
  outline: 'border border-lav-200 bg-surface text-ink hover:border-lav-400 hover:bg-lav-50',
  ghost: 'text-ink-2 hover:bg-lav-100',
  danger: 'bg-rose-600 text-white hover:bg-rose-700',
  'danger-ghost': 'text-rose-600 hover:bg-rose-50',
}
const SIZE = { sm: 'h-8 px-3 text-[13px] gap-1.5', md: 'h-10 px-4 text-sm gap-2', lg: 'h-12 px-6 text-[15px] gap-2', icon: 'h-9 w-9 justify-center', 'icon-sm': 'h-8 w-8 justify-center' }

export function Button({ variant = 'outline', size = 'md', className, loading, children, ...p }) {
  return (
    <motion.button whileTap={{ scale: 0.97 }} {...p} disabled={p.disabled || loading}
      className={cx('inline-flex items-center rounded-xl font-medium transition-colors disabled:opacity-50 disabled:pointer-events-none focus-visible:outline-2 focus-visible:outline-lav-500 cursor-pointer whitespace-nowrap', BTN[variant], SIZE[size], className)}>
      {loading && <Loader2 className="size-4 animate-spin" />}
      {children}
    </motion.button>
  )
}

export function Card({ className, children, ...p }) {
  return <section {...p} className={cx('rounded-2xl border border-lav-200/70 bg-surface shadow-soft', className)}>{children}</section>
}

export function Badge({ tone = 'lav', className, children, ...p }) {
  const t = {
    lav: 'bg-lav-100 text-lav-700', green: 'bg-emerald-50 text-emerald-700', red: 'bg-rose-50 text-rose-700',
    amber: 'bg-amber-50 text-amber-700', gray: 'bg-zinc-100 text-zinc-600', ink: 'bg-ink text-surface', blue: 'bg-sky-50 text-sky-700',
  }[tone]
  return <span {...p} className={cx('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold tracking-wide', t, className)}>{children}</span>
}

export const Input = ({ className, ...p }) => (
  <input {...p} className={cx('h-10 w-full rounded-xl border border-lav-200 bg-surface px-3 text-sm outline-none placeholder:text-muted focus:border-lav-500 focus:ring-4 focus:ring-lav-200/60', className)} />
)
export const Textarea = ({ className, ...p }) => (
  <textarea {...p} className={cx('w-full rounded-xl border border-lav-200 bg-surface p-3 text-sm outline-none placeholder:text-muted focus:border-lav-500 focus:ring-4 focus:ring-lav-200/60', className)} />
)
export function Select({ className, options = [], ...p }) {
  return (
    <select {...p} className={cx('h-10 rounded-xl border border-lav-200 bg-surface px-3 text-sm outline-none focus:border-lav-500 focus:ring-4 focus:ring-lav-200/60', className)}>
      {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
    </select>
  )
}

export function Switch({ checked, onChange, label, testid, ...p }) {
  return (
    <button type="button" role="switch" aria-checked={!!checked} aria-label={label} data-testid={testid} {...p}
      onClick={() => onChange(!checked)}
      className={cx('relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors cursor-pointer', checked ? 'bg-lav-600' : 'bg-zinc-300')}>
      <motion.span layout transition={{ type: 'spring', stiffness: 600, damping: 35 }}
        className={cx('inline-block size-5 rounded-full bg-surface shadow', checked ? 'ml-[22px]' : 'ml-0.5')} />
    </button>
  )
}

export function Field({ label, hint, children, className }) {
  return (
    <label className={cx('block space-y-1.5', className)}>
      {label && <span className="text-[13px] font-medium text-ink-2">{label}</span>}
      {children}
      {hint && <span className="block text-xs text-muted">{hint}</span>}
    </label>
  )
}

export function Tabs({ value, onChange, items, testid = 'tabs', className }) {
  return (
    <div role="tablist" data-testid={testid} className={cx('inline-flex rounded-xl bg-lav-100 p-1', className)}>
      {items.map(([v, l, count]) => (
        <button key={v} role="tab" aria-selected={value === v} data-testid={`${testid}-${v}`} onClick={() => onChange(v)}
          className={cx('relative rounded-lg px-3 py-1.5 text-[13px] font-medium transition-colors cursor-pointer', value === v ? 'text-ink' : 'text-muted hover:text-ink')}>
          {value === v && <motion.span layoutId={`tab-${testid}`} className="absolute inset-0 rounded-lg bg-surface shadow-soft" transition={{ type: 'spring', stiffness: 500, damping: 40 }} />}
          <span className="relative flex items-center gap-1.5">{l}{count !== undefined && <span className="rounded-full bg-lav-200 px-1.5 text-[10px]">{count}</span>}</span>
        </button>
      ))}
    </div>
  )
}

export function Dialog({ open, onClose, title, children, wide, testid = 'dialog', footer }) {
  useEffect(() => {
    if (!open) return
    const k = (e) => e.key === 'Escape' && onClose?.()
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [open, onClose])
  return (
    <AnimatePresence>
      {open && (
        <motion.div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-night/40 p-4 pt-[6vh] backdrop-blur-sm"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onMouseDown={(e) => e.target === e.currentTarget && onClose?.()}>
          <motion.div role="dialog" aria-modal="true" aria-label={title} data-testid={testid}
            initial={{ opacity: 0, y: 24, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 12, scale: 0.98 }}
            transition={{ type: 'spring', stiffness: 380, damping: 32 }}
            className={cx('w-full rounded-3xl border border-lav-200 bg-surface shadow-pop', wide ? 'max-w-6xl' : 'max-w-lg')}>
            <header className="flex items-center justify-between border-b border-lav-100 px-6 py-4">
              <h2 className="text-lg font-semibold">{title}</h2>
              <Button variant="ghost" size="icon" aria-label="Close" data-testid={`${testid}-close`} onClick={onClose}><X className="size-4" /></Button>
            </header>
            <div className="p-6">{children}</div>
            {footer && <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-lav-100 px-6 py-4">{footer}</footer>}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

export function Empty({ icon: Icon = Sparkles, title, children, action, testid = 'empty' }) {
  return (
    <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} data-testid={testid}
      className="relative flex flex-col items-center justify-center gap-3 overflow-hidden rounded-3xl border border-lav-200/80 bg-surface px-6 py-16 text-center shadow-soft">
      <div className="bg-grid pointer-events-none absolute inset-0 opacity-50 [mask-image:radial-gradient(ellipse_at_center,black_20%,transparent_70%)]" />
      <div className="relative">
        <div className="absolute inset-0 -m-3 rounded-full bg-lav-400/30 blur-xl" />
        <div className="relative grid size-14 place-items-center rounded-2xl bg-gradient-to-br from-lav-200 to-lav-400 text-[#2a1d4d] shadow-soft"><Icon className="size-6" /></div>
      </div>
      <h3 className="relative text-base font-semibold">{title}</h3>
      {children && <p className="relative max-w-md text-sm text-muted">{children}</p>}
      {action && <div className="relative mt-1">{action}</div>}
    </motion.div>
  )
}

export function Skeleton({ className, ...p }) {
  return <div aria-hidden="true" {...p} className={cx('skeleton', className)} />
}

export function SkeletonList({ rows = 3, testid = 'skeleton' }) {
  return (
    <div className="space-y-3" data-testid={testid} aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex gap-4 rounded-2xl border border-lav-200/70 bg-surface p-4 shadow-soft">
          <Skeleton className="size-10 rounded-full" />
          <div className="flex-1 space-y-2.5"><Skeleton className="h-3 w-40" /><Skeleton className="h-3 w-full" /><Skeleton className="h-3 w-2/3" /></div>
        </div>
      ))}
    </div>
  )
}

/** Card with a soft lavender spotlight that follows the pointer (own take on the React Bits idea). */
export function SpotlightCard({ className, children, as: Tag = 'div', ...p }) {
  const ref = useRef(null)
  const move = (e) => {
    const r = ref.current?.getBoundingClientRect()
    if (!r) return
    ref.current.style.setProperty('--mx', `${e.clientX - r.left}px`)
    ref.current.style.setProperty('--my', `${e.clientY - r.top}px`)
  }
  return <Tag ref={ref} onMouseMove={move} {...p} className={cx('spotlight rounded-2xl border border-lav-200/70 bg-surface shadow-soft', className)}>{children}</Tag>
}

/** Right-hand side sheet (Buffer-style composer panel). Same testid contract as Dialog. */
export function Sheet({ open, onClose, title, children, testid = 'sheet', footer, headerExtra, width = 'max-w-[1180px]' }) {
  useEffect(() => {
    if (!open) return
    const k = (e) => e.key === 'Escape' && onClose?.()
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [open, onClose])
  return (
    <AnimatePresence>
      {open && (
        <motion.div className="fixed inset-0 z-50 flex justify-end bg-night/50 backdrop-blur-[2px]" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
          onMouseDown={(e) => e.target === e.currentTarget && onClose?.()}>
          <motion.div role="dialog" aria-modal="true" aria-label={title} data-testid={testid}
            initial={{ x: 60, opacity: 0 }} animate={{ x: 0, opacity: 1 }} exit={{ x: 60, opacity: 0 }} transition={{ type: 'spring', stiffness: 420, damping: 38 }}
            className={cx('flex h-full w-full flex-col border-l border-lav-200 bg-background shadow-pop', width)}>
            <header className="flex h-14 shrink-0 items-center gap-3 border-b border-lav-200 bg-surface px-5">
              <h2 className="text-[15px] font-semibold">{title}</h2>
              <div className="ml-auto flex items-center gap-1">{headerExtra}
                <Button variant="ghost" size="icon" aria-label="Close" data-testid={`${testid}-close`} onClick={onClose}><X className="size-4" /></Button></div>
            </header>
            <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">{children}</div>
            {footer && <footer className="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-lav-200 bg-surface px-5 py-3">{footer}</footer>}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

/** Small dropdown menu. items: [label, onClick, {icon, testid, danger}] */
export function Menu({ label, icon: Icon, items, testid, align = 'right', variant = 'ghost', size = 'icon', children }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  useOutside(ref, () => setOpen(false))
  return (
    <div className="relative" ref={ref}>
      <Button type="button" variant={variant} size={size} aria-label={label} aria-haspopup="menu" aria-expanded={open} data-testid={testid} onClick={() => setOpen(!open)}>
        {Icon && <Icon className="size-4" />}{children}
      </Button>
      <AnimatePresence>
        {open && (
          <motion.div role="menu" initial={{ opacity: 0, y: -4, scale: .98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0 }}
            className={cx('absolute top-10 z-40 min-w-52 rounded-xl border border-lav-200 bg-popover p-1 shadow-pop', align === 'right' ? 'right-0' : 'left-0')}>
            {items.filter(Boolean).map(([l, fn, o = {}]) => (
              <button key={l} role="menuitem" data-testid={o.testid} onClick={() => { setOpen(false); fn() }}
                className={cx('flex w-full cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-left text-[13px]', o.danger ? 'text-rose-600 hover:bg-rose-50' : 'text-ink-2 hover:bg-lav-100 hover:text-ink')}>
                {o.icon && <o.icon className="size-4" />}{l}
              </button>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

export const Kbd = ({ children }) => <kbd className="rounded-md border border-lav-200 bg-surface px-1.5 py-0.5 font-mono text-[10px] text-ink-2 shadow-sm">{children}</kbd>

// ---------------------------------------------------------------- networks
export const NETWORKS = {
  instagram: { name: 'Instagram', icon: Instagram, bg: 'bg-gradient-to-br from-amber-400 via-pink-500 to-purple-600' },
  tiktok: { name: 'TikTok', icon: TikTok, bg: 'bg-zinc-900' },
  youtube: { name: 'YouTube', icon: Youtube, bg: 'bg-red-600' },
  facebook: { name: 'Facebook', icon: Facebook, bg: 'bg-blue-600' },
  linkedin: { name: 'LinkedIn', icon: Linkedin, bg: 'bg-sky-700' },
  linkedin_page: { name: 'LinkedIn Page', icon: Linkedin, bg: 'bg-sky-800' },
  x: { name: 'X', icon: XLogo, bg: 'bg-black' },
  threads: { name: 'Threads', icon: Threads, bg: 'bg-zinc-800' },
  bluesky: { name: 'Bluesky', icon: Bluesky, bg: 'bg-sky-500' },
  fake: { name: 'Demo', icon: Sparkles, bg: 'bg-lav-600' },
}

export function NetIcon({ provider, size = 'md', className }) {
  const n = NETWORKS[provider] || NETWORKS.fake
  const I = n.icon
  const s = { sm: 'size-4 p-0.5', md: 'size-6 p-1', lg: 'size-10 p-2' }[size]
  return <span className={cx('inline-grid place-items-center rounded-full text-white', n.bg, s, className)} aria-label={n.name}><I className="size-full" /></span>
}

export function ChannelAvatar({ ch, size = 40, ring, className }) {
  const initials = (ch?.display_name || ch?.username || '?').slice(0, 2).toUpperCase()
  return (
    <span className={cx('relative inline-block shrink-0', className)} style={{ width: size, height: size }}>
      {ch?.avatar_url
        ? <img src={ch.avatar_url} alt="" className={cx('size-full rounded-full object-cover', ring && 'ring-2 ring-lav-500 ring-offset-2')} />
        : <span className={cx('grid size-full place-items-center rounded-full bg-gradient-to-br from-lav-300 to-lav-600 text-xs font-bold text-white', ring && 'ring-2 ring-lav-500 ring-offset-2')}>{initials}</span>}
      <NetIcon provider={ch?.provider} size="sm" className="absolute -bottom-0.5 -right-0.5 ring-2 ring-surface" />
    </span>
  )
}

// ---------------------------------------------------------------- toasts
const ToastCtx = createContext(() => {})
export const useToast = () => useContext(ToastCtx)
export function ToastProvider({ children }) {
  const [items, setItems] = useState([])
  const push = useCallback((msg, tone = 'ok') => {
    const id = Math.random()
    setItems((x) => [...x, { id, msg, tone }])
    setTimeout(() => setItems((x) => x.filter((i) => i.id !== id)), tone === 'err' ? 9000 : 5000)
  }, [])
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-[60] flex flex-col gap-2" aria-live="polite">
        <AnimatePresence>
          {items.map((t) => (
            <motion.div key={t.id} role="status" data-testid="toast" initial={{ opacity: 0, y: 16, scale: 0.95 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, x: 40 }}
              className={cx('pointer-events-auto max-w-sm rounded-2xl px-4 py-3 text-sm shadow-pop', t.tone === 'err' ? 'bg-rose-600 text-white' : 'bg-ink text-surface')}>
              {t.msg}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </ToastCtx.Provider>
  )
}

export function useOutside(ref, fn) {
  useEffect(() => {
    const h = (e) => ref.current && !ref.current.contains(e.target) && fn()
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [ref, fn])
}

export function Stat({ label, value, sub, testid }) {
  return (
    <div data-testid={testid} className="rounded-2xl border border-lav-200/70 bg-surface p-4 shadow-soft">
      <div className="text-xs font-medium uppercase tracking-wider text-muted">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value ?? '—'}</div>
      {sub && <div className="mt-0.5 text-xs text-muted">{sub}</div>}
    </div>
  )
}

export const fmt = (n) => (n === null || n === undefined ? '—' : Intl.NumberFormat('en', { notation: n > 9999 ? 'compact' : 'standard' }).format(n))

export function useAsync(fn, deps = []) {
  const [state, setState] = useState({ loading: true, data: null, error: null })
  const ref = useRef(0)
  const run = useCallback(async () => {
    const id = ++ref.current
    setState((s) => ({ ...s, loading: true }))
    try {
      const data = await fn()
      if (id === ref.current) setState({ loading: false, data, error: null })
    } catch (error) {
      if (id === ref.current) setState({ loading: false, data: null, error })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  useEffect(() => { run() }, [run])
  return { ...state, reload: run }
}

// In-app confirm / prompt. Native window.confirm/prompt block the page (and browser agents driving it),
// so destructive actions and quick inputs go through this promise-based dialog instead.
let askHost = null
const ask = (o) => new Promise((resolve) => (askHost ? askHost({ ...o, resolve }) : resolve(o.input ? null : false)))
export const confirmAsync = (message, o = {}) => ask({ message, title: o.title || 'Are you sure?', ok: o.ok || 'Confirm', danger: o.danger ?? true })
export const promptAsync = (message, o = {}) => ask({ message, title: o.title || message, ok: o.ok || 'OK', input: true, value: o.value || '', placeholder: o.placeholder || '' })
export function AskHost() {
  const [req, setReq] = useState(null)
  const [val, setVal] = useState('')
  useEffect(() => { askHost = (r) => { setVal(r.value || ''); setReq(r) }; return () => { askHost = null } }, [])
  const done = (v) => { req?.resolve(v); setReq(null) }
  const cancel = () => done(req?.input ? null : false)
  const ok = () => done(req?.input ? (val.trim() ? val : null) : true)
  return (
    <Dialog open={!!req} onClose={cancel} title={req?.title || ''} testid="ask"
      footer={<><Button variant="ghost" onClick={cancel} data-testid="ask-cancel">Cancel</Button>
        <Button variant={req?.danger ? 'danger' : 'primary'} onClick={ok} data-testid="ask-ok">{req?.ok}</Button></>}>
      {req?.message && req.message !== req.title && <p className="whitespace-pre-line text-sm text-ink-2" data-testid="ask-message">{req.message}</p>}
      {req?.input && <Input autoFocus className="mt-3" value={val} placeholder={req.placeholder} onChange={(e) => setVal(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && ok()} data-testid="ask-input" aria-label={req.title} />}
    </Dialog>
  )
}
