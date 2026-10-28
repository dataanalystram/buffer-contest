// One filter bar for Calendar, Publish list and Sent/history (Buffer parity: channels, status, tags,
// post type, timezone, "save current view"; plus campaign and caption search, which Buffer lacks).
// Every filter lives in the URL hash (shareable, agent-addressable) and is mirrored to localStorage,
// so a reload or switching between Calendar and List keeps the same view.
import { AnimatePresence, motion } from 'motion/react'
import { Bookmark, Check, ChevronDown, Globe, Search, Trash2, X, Plus } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { get, put } from '../api'
import { setQuery, useRoute } from '../router'
import { useStore } from '../store'
import { Button, ChannelAvatar, cx, Input, useOutside, useToast } from '../ui'

export const FILTER_KEYS = ['ch', 'type', 'status', 'tag', 'campaign', 'q', 'tz']
const STORE_KEY = 'huhq-filters'

export const TYPES = [['post', 'Post'], ['carousel', 'Carousel'], ['reel', 'Reel'], ['story', 'Story'], ['short', 'Short'], ['video', 'Video']]
export const STATUSES = [['scheduled', 'Scheduled'], ['draft', 'Draft'], ['pending_approval', 'Pending approval'], ['sent', 'Sent'], ['failed', 'Failed']]

const split = (v) => (v ? String(v).split(',').filter(Boolean) : [])
export const browserTz = () => Intl.DateTimeFormat().resolvedOptions().timeZone

/** Wall-clock parts of an instant in a timezone. */
export function zoned(date, tz) {
  const d = date instanceof Date ? date : new Date(date)
  const p = Object.fromEntries(new Intl.DateTimeFormat('en-GB', { timeZone: tz || browserTz(), year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
    .formatToParts(d).map((x) => [x.type, x.value]))
  return { y: +p.year, mo: +p.month, d: +p.day, h: +p.hour % 24, mi: +p.minute, ymd: `${p.year}-${p.month}-${p.day}` }
}

/** Filters for a page: values from the URL, falling back to (and saved in) localStorage. */
export function useFilters({ fields = FILTER_KEYS } = {}) {
  const { query } = useRoute()
  const { settings } = useStore()
  // first visit with no filters in the URL: restore the last ones used
  useEffect(() => {
    if (FILTER_KEYS.some((k) => query[k] !== undefined)) return
    try {
      const saved = JSON.parse(localStorage.getItem(STORE_KEY) || '{}')
      const patch = Object.fromEntries(Object.entries(saved).filter(([k, v]) => fields.includes(k) && v))
      if (Object.keys(patch).length) setQuery(patch)
    } catch { /* ignore */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const f = Object.fromEntries(FILTER_KEYS.map((k) => [k, query[k] || '']))
  useEffect(() => {
    const cur = JSON.parse(localStorage.getItem(STORE_KEY) || '{}')
    const next = { ...cur }
    for (const k of fields) next[k] = f[k]
    localStorage.setItem(STORE_KEY, JSON.stringify(next))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [FILTER_KEYS.map((k) => f[k]).join('|')])
  const set = (patch) => setQuery(patch)
  const clear = () => setQuery(Object.fromEntries(fields.filter((k) => k !== 'tz').map((k) => [k, ''])))
  const tz = f.tz || settings?.timezone || browserTz()
  const active = fields.filter((k) => k !== 'tz' && f[k]).length
  /** query-string params for /api/posts */
  const params = (extra = {}) => {
    const p = new URLSearchParams()
    if (f.ch && fields.includes('ch')) p.set('channel_id', f.ch)
    if (f.type && fields.includes('type')) p.set('post_type', f.type)
    if (f.status && fields.includes('status')) p.set('status', f.status)
    if (f.tag && fields.includes('tag')) p.set('tag', f.tag)
    if (f.campaign && fields.includes('campaign')) p.set('campaign', f.campaign)
    if (f.q && fields.includes('q')) p.set('q', f.q)
    for (const [k, v] of Object.entries(extra)) if (v) p.set(k, v)
    return p
  }
  return { f, set, clear, tz, active, params, list: (k) => split(f[k]) }
}

function Pop({ label, count, testid, children, icon: Icon, wide }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  useOutside(ref, () => setOpen(false))
  return (
    <div className="relative" ref={ref}>
      <button type="button" onClick={() => setOpen(!open)} data-testid={testid} aria-haspopup="listbox" aria-expanded={open}
        className={cx('flex h-9 cursor-pointer items-center gap-1.5 rounded-xl border px-3 text-[13px] transition',
          count ? 'border-lav-500 bg-lav-100 font-medium text-ink' : 'border-lav-200 bg-surface text-ink-2 hover:border-lav-400')}>
        {Icon && <Icon className="size-3.5" />}{label}{count ? <span className="rounded-full bg-lav-600 px-1.5 text-[10px] text-white" data-testid={`${testid}-count`}>{count}</span> : null}
        <ChevronDown className="size-3.5 text-muted" />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} data-testid={`${testid}-menu`}
            className={cx('absolute left-0 top-11 z-40 max-h-96 overflow-y-auto rounded-2xl border border-lav-200 bg-popover p-1.5 shadow-pop', wide ? 'w-80' : 'w-60')}>
            {children(() => setOpen(false))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function Option({ on, onClick, children, testid }) {
  return (
    <button type="button" role="option" aria-selected={on} onClick={onClick} data-testid={testid}
      className={cx('flex w-full cursor-pointer items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-[13px]', on ? 'bg-lav-100 text-ink' : 'text-ink-2 hover:bg-lav-50')}>
      <span className={cx('grid size-4 shrink-0 place-items-center rounded border', on ? 'border-lav-600 bg-lav-600 text-white' : 'border-lav-300')}>{on && <Check className="size-3" />}</span>
      {children}
    </button>
  )
}

function Multi({ k, label, options, fl, testid, render, wide }) {
  const sel = fl.list(k)
  const toggle = (v) => fl.set({ [k]: (sel.includes(v) ? sel.filter((x) => x !== v) : [...sel, v]).join(',') })
  return (
    <Pop label={label} count={sel.length} testid={testid} wide={wide}>
      {() => <>
        <Option on={!sel.length} onClick={() => fl.set({ [k]: '' })} testid={`${testid}-all`}>All</Option>
        {options.map(([v, l, extra]) => <Option key={v} on={sel.includes(v)} onClick={() => toggle(v)} testid={`${testid}-option`}>{render ? render(v, l, extra) : l}</Option>)}
        {!options.length && <p className="px-3 py-2 text-xs text-muted">Nothing here yet.</p>}
      </>}
    </Pop>
  )
}

const ZONES = ['Europe/Dublin', 'Europe/London', 'UTC', 'Europe/Berlin', 'America/New_York', 'America/Los_Angeles', 'Asia/Kolkata', 'Asia/Singapore', 'Australia/Sydney']

function SavedViews({ fl, page }) {
  const toast = useToast()
  const [views, setViews] = useState([])
  const [name, setName] = useState('')
  useEffect(() => { get('/api/views').then(setViews).catch(() => {}) }, [])
  const save = async () => {
    if (!name.trim()) return
    const filters = Object.fromEntries(FILTER_KEYS.filter((k) => fl.f[k]).map((k) => [k, fl.f[k]]))
    try { setViews(await put('/api/views', { items: [...views, { name: name.trim(), page, filters }] })); setName(''); toast(`View “${name.trim()}” saved`) } catch (e) { toast(e.message, 'err') }
  }
  const remove = async (id) => setViews(await put('/api/views', { items: views.filter((v) => v.id !== id) }))
  return (
    <Pop label="Views" icon={Bookmark} testid="filter-views" wide>
      {(close) => <>
        <div className="px-2.5 pb-1 pt-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted">Saved views</div>
        {views.map((v) => (
          <div key={v.id} className="group flex items-center rounded-xl hover:bg-lav-50">
            <button className="flex-1 cursor-pointer px-2.5 py-2 text-left text-[13px]" data-testid="filter-view-apply"
              onClick={() => { fl.set(Object.fromEntries(FILTER_KEYS.map((k) => [k, v.filters[k] || '']))); close() }}>{v.name}
              <span className="block truncate text-[11px] text-muted">{Object.entries(v.filters).map(([k, x]) => `${k}: ${x}`).join(' · ') || 'no filters'}</span></button>
            <button aria-label={`Delete view ${v.name}`} onClick={() => remove(v.id)} className="mr-1 cursor-pointer rounded-lg p-1.5 text-muted opacity-0 hover:text-rose-600 group-hover:opacity-100"><Trash2 className="size-3.5" /></button>
          </div>
        ))}
        {!views.length && <p className="px-2.5 py-1 text-xs text-muted">Save the filters you use most.</p>}
        <div className="mt-1 flex gap-1.5 border-t border-lav-100 p-1.5 pt-2.5">
          <Input className="h-8" placeholder="Name this view" value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && save()} data-testid="filter-view-name" />
          <Button size="sm" variant="primary" onClick={save} disabled={!name.trim()} data-testid="filter-view-save"><Plus className="size-3.5" />Save</Button>
        </div>
      </>}
    </Pop>
  )
}

/**
 * fields: which filters to show. lockChannel: hide the channel picker (page already scoped to one channel).
 * extra: page-specific controls rendered at the end (e.g. view toggle).
 */
export default function FilterBar({ fl, fields = FILTER_KEYS, page = 'calendar', lockChannel, extra, campaigns: campaignsProp }) {
  const { channels, library } = useStore()
  const [campaigns, setCampaigns] = useState(campaignsProp || [])
  const [q, setQ] = useState(fl.f.q)
  useEffect(() => { if (fields.includes('campaign') && !campaignsProp) get('/api/campaigns').then((r) => setCampaigns(r.map((c) => c.name))).catch(() => {}) }, [])
  useEffect(() => { setQ(fl.f.q) }, [fl.f.q])
  useEffect(() => { const t = setTimeout(() => q !== fl.f.q && fl.set({ q }), 250); return () => clearTimeout(t) }, [q])
  const tzOptions = useMemo(() => [...new Set([browserTz(), ...channels.map((c) => c.timezone), ...ZONES])], [channels])
  const chips = [
    ...fl.list('ch').map((v) => ['ch', v, '@' + (channels.find((c) => c.id === v)?.username || v)]),
    ...fl.list('type').map((v) => ['type', v, TYPES.find((t) => t[0] === v)?.[1] || v]),
    ...fl.list('status').map((v) => ['status', v, STATUSES.find((t) => t[0] === v)?.[1] || v]),
    ...fl.list('tag').map((v) => ['tag', v, '#' + ((library.tags || []).find((t) => t.id === v)?.name || v)]),
    ...fl.list('campaign').map((v) => ['campaign', v, v]),
  ].filter(([k]) => fields.includes(k) && !(k === 'ch' && lockChannel))
  return (
    <div className="space-y-2" data-testid="filter-bar">
      <div className="flex flex-wrap items-center gap-2">
        {fields.includes('ch') && !lockChannel && (
          <Multi k="ch" label="Channels" fl={fl} testid="filter-channels" wide options={channels.map((c) => [c.id, c.username, c])}
            render={(v, l, c) => <span className="flex min-w-0 items-center gap-2"><ChannelAvatar ch={c} size={22} /><span className="truncate">@{l}</span><span className="ml-auto text-[11px] text-muted">{c.counts?.scheduled || 0}</span></span>} />
        )}
        {fields.includes('type') && <Multi k="type" label="Post type" fl={fl} testid="filter-type" options={TYPES} />}
        {fields.includes('status') && <Multi k="status" label="Status" fl={fl} testid="filter-status" options={STATUSES} />}
        {fields.includes('tag') && <Multi k="tag" label="Tags" fl={fl} testid="filter-tags" options={(library.tags || []).map((t) => [t.id, t.name, t])}
          render={(v, l, t) => <span className="flex items-center gap-2"><span className="size-2.5 rounded-full" style={{ background: t.color }} />{l}</span>} />}
        {fields.includes('campaign') && <Multi k="campaign" label="Campaign" fl={fl} testid="filter-campaign" options={campaigns.map((c) => [c, c])} />}
        {fields.includes('q') && (
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search captions" aria-label="Search captions" data-testid="filter-search"
              className="h-9 w-48 rounded-xl border border-lav-200 bg-surface pl-8 pr-3 text-[13px] outline-none placeholder:text-muted focus:border-lav-500 focus:ring-4 focus:ring-lav-200/60" />
          </div>
        )}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {fields.includes('tz') && (
            <label className="flex h-9 items-center gap-1.5 rounded-xl border border-lav-200 bg-surface pl-2.5 text-[13px] text-ink-2" title="Timezone used to show times">
              <Globe className="size-3.5 text-muted" />
              <select value={fl.tz} onChange={(e) => fl.set({ tz: e.target.value })} data-testid="filter-timezone" aria-label="Timezone" className="h-full cursor-pointer bg-transparent pr-2 outline-none">
                {[...new Set([fl.tz, ...tzOptions])].map((z) => <option key={z} value={z}>{z.replace('_', ' ')}</option>)}
              </select>
            </label>
          )}
          <SavedViews fl={fl} page={page} />
          {extra}
        </div>
      </div>
      {chips.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5" data-testid="filter-chips">
          {chips.map(([k, v, l]) => (
            <button key={k + v} onClick={() => fl.set({ [k]: fl.list(k).filter((x) => x !== v).join(',') })} data-testid="filter-chip"
              className="flex cursor-pointer items-center gap-1 rounded-full bg-lav-100 py-0.5 pl-2.5 pr-1.5 text-xs text-ink-2 hover:bg-lav-200">{l}<X className="size-3" /></button>
          ))}
          <button onClick={fl.clear} className="cursor-pointer px-2 text-xs font-medium text-lav-700 hover:underline" data-testid="filter-clear">Clear all</button>
        </div>
      )}
    </div>
  )
}
