// Calendar (Buffer parity and then some): List / Week / Month, Today + prev/next, filters for channels,
// post type, status, tags, campaign and caption text, a timezone selector, saved views, "No date" drafts,
// posting-time markers, per-day counts, "N more", click-to-schedule on any empty slot and drag to reschedule.
import { CalendarDays, CalendarOff, ChevronLeft, ChevronRight, Clock3, List, Plus } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { get, patch } from '../api'
import { go, setQuery, useRoute } from '../router'
import { useStore } from '../store'
import { Badge, Button, ChannelAvatar, cx, Empty, Skeleton, SkeletonList, Switch, useToast } from '../ui'
import PostCard, { STATUS } from '../components/PostCard'
import FilterBar, { useFilters, zoned } from '../components/FilterBar'

const pad = (n) => String(n).padStart(2, '0')
const ymd = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
const startOfWeek = (d) => { const x = new Date(d); x.setHours(0, 0, 0, 0); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x }
const HOURS = Array.from({ length: 24 }, (_, i) => i)
const MONTH_MAX = 3
const ROW_H = 56

/** The instant at wall-clock ymd hh:mm in tz. */
function fromZoned(day, hh, mm, tz) {
  const [y, mo, d] = day.split('-').map(Number)
  let guess = Date.UTC(y, mo - 1, d, hh, mm)
  for (let i = 0; i < 2; i++) {
    const z = zoned(new Date(guess), tz)
    const diff = Date.UTC(z.y, z.mo - 1, z.d, z.h, z.mi) - Date.UTC(y, mo - 1, d, hh, mm)
    guess -= diff
  }
  return new Date(guess)
}

export default function CalendarPage() {
  const { channels } = useStore()
  const { query } = useRoute()
  const toast = useToast()
  const fl = useFilters()
  const view = query.view || localStorage.getItem('huhq-cal-view') || 'week'
  useEffect(() => { localStorage.setItem('huhq-cal-view', view) }, [view])
  const anchor = query.date ? new Date(query.date + 'T12:00') : new Date()
  const [rows, setRows] = useState(null)
  const [undated, setUndated] = useState([])
  const [openDay, setOpenDay] = useState(null)
  const showNoDate = query.nodate === '1'
  const showSlots = query.slots !== '0'
  const scroller = useRef(null)
  const tz = fl.tz
  const days = useMemo(() => {
    if (view === 'week') { const s = startOfWeek(anchor); return Array.from({ length: 7 }, (_, i) => { const d = new Date(s); d.setDate(s.getDate() + i); return d }) }
    if (view === 'list') { const s = new Date(anchor); s.setHours(0, 0, 0, 0); return Array.from({ length: 28 }, (_, i) => { const d = new Date(s); d.setDate(s.getDate() + i); return d }) }
    const first = new Date(anchor.getFullYear(), anchor.getMonth(), 1)
    const s = startOfWeek(first)
    return Array.from({ length: 42 }, (_, i) => { const d = new Date(s); d.setDate(s.getDate() + i); return d })
  }, [view, query.date])
  const filterKey = ['ch', 'type', 'status', 'tag', 'campaign', 'q'].map((k) => fl.f[k]).join('|')

  // only the newest load may set rows: a restored filter (useFilters) starts a second load while the first, unfiltered
  // one is still in flight, and a slower first answer used to overwrite the filtered list
  const loadSeq = useRef(0)
  const load = async () => {
    const seq = ++loadSeq.current
    // pad the window by a day each side so a timezone switch never drops edge posts
    const lo = new Date(days[0]); lo.setDate(lo.getDate() - 1)
    const hi = new Date(days[days.length - 1]); hi.setDate(hi.getDate() + 2)
    const p = fl.params({ start: ymd(lo) + 'T00:00', end: ymd(hi) + 'T00:00' })
    if (!fl.f.status) p.set('status', 'scheduled,sending,processing,sent,failed,needs_action,pending_approval,draft')
    const got = await get('/api/posts?' + p)
    if (seq !== loadSeq.current) return
    setRows(got)
    if (showNoDate) {
      const u = fl.params({ undated: 'true' }); u.set('status', 'draft')
      const und = await get('/api/posts?' + u)
      if (seq === loadSeq.current) setUndated(und)
    }
  }
  useEffect(() => { load().catch((e) => toast(e.message, 'err')) }, [days, filterKey, showNoDate])
  useEffect(() => { const f = () => load(); window.addEventListener('huhq:posts-changed', f); return () => window.removeEventListener('huhq:posts-changed', f) })
  useEffect(() => { if (view === 'week' && scroller.current) scroller.current.scrollTop = 7 * ROW_H }, [view, rows === null])

  const instant = (t) => new Date(t.status === 'sent' ? t.published_at : t.scheduled_at)
  const z = (t) => zoned(instant(t), tz)
  const dated = (rows || []).filter((t) => t.scheduled_at || t.published_at)
  const byDay = useMemo(() => {
    const m = {}
    for (const t of dated) (m[z(t).ymd] ||= []).push(t)
    for (const k in m) m[k].sort((a, b) => instant(a) - instant(b))
    return m
  }, [rows, tz])
  const dayItems = (d) => byDay[ymd(d)] || []
  const visibleChannels = fl.list('ch').length ? channels.filter((c) => fl.list('ch').includes(c.id)) : channels
  // posting-time markers: each channel's weekly slots, shown in the selected timezone
  const slotMap = useMemo(() => {
    const m = {}
    if (!showSlots || view !== 'week') return m
    const span = [new Date(days[0].getTime() - 86400000), ...days, new Date(days[6].getTime() + 86400000)]
    for (const day of span) {
      const ds = ymd(day)
      const dow = (new Date(ds + 'T12:00:00Z').getUTCDay() + 6) % 7
      for (const c of visibleChannels) {
        for (const s of c.slots || []) {
          if (s.weekday !== dow) continue
          const [hh, mm] = s.time.split(':').map(Number)
          const local = zoned(fromZoned(ds, hh, mm, c.timezone), tz)
          ;(m[`${local.ymd}|${local.h}`] ||= []).push({ c, time: `${pad(local.h)}:${pad(local.mi)}` })
        }
      }
    }
    return m
  }, [days, tz, showSlots, view, fl.f.ch, channels])
  const slotsFor = (d, h) => slotMap[`${ymd(d)}|${h}`] || []

  const move = async (id, d, hour) => {
    const t = [...(rows || []), ...undated].find((x) => x.id === id)
    if (!t || !['scheduled', 'draft'].includes(t.status)) return toast('Only scheduled posts and drafts can be moved', 'err')
    const old = t.scheduled_at ? z(t) : { h: 9, mi: 0 }
    const hh = hour ?? old.h, mm = hour !== undefined ? 0 : old.mi
    const inst = fromZoned(ymd(d), hh, mm, tz)
    const before = rows
    setRows([...(rows || []).filter((x) => x.id !== id), { ...t, scheduled_at: inst.toISOString() }])  // optimistic
    setUndated((u) => u.filter((x) => x.id !== id))
    try {
      await patch(`/api/targets/${id}`, { when: 'custom', scheduled_at: `${ymd(d)}T${pad(hh)}:${pad(mm)}`, timezone: tz })
      toast('Rescheduled'); load()
    } catch (e) { setRows(before); load(); toast(e.message, 'err') }
  }
  const shift = (n) => { const d = new Date(anchor); view === 'month' ? d.setMonth(d.getMonth() + n) : d.setDate(d.getDate() + (view === 'list' ? 28 : 7) * n); setQuery({ date: ymd(d) }) }
  const compose = (d, h, ch) => setQuery({ compose: 'new', when: 'custom', at: `${ymd(d)}T${pad(h ?? 9)}:00`, channels: ch || fl.f.ch || '' })
  const Chip = ({ t }) => {
    const [label, tone] = STATUS[t.status] || []
    const w = (t.scheduled_at || t.published_at) ? z(t) : null
    return (
      <div draggable onDragStart={(e) => e.dataTransfer.setData('text/plain', t.id)} onClick={() => setQuery({ compose: ['scheduled', 'draft', 'failed', 'pending_approval'].includes(t.status) ? t.id : undefined })}
        data-testid="calendar-post" data-format={t.format} title={`${label} · ${t.format}: ${t.caption}`}
        className={cx('flex min-w-0 cursor-pointer items-center gap-1.5 overflow-hidden rounded-lg border px-1.5 py-1 text-[11px] leading-tight shadow-sm transition hover:-translate-y-px',
          tone === 'green' ? 'border-emerald-200 bg-emerald-50' : tone === 'red' ? 'border-rose-200 bg-rose-50' : tone === 'amber' ? 'border-amber-200 bg-amber-50' : tone === 'gray' ? 'border-dashed border-zinc-300 bg-zinc-50' : 'border-lav-200 bg-lav-100')}>
        <ChannelAvatar ch={t.channel} size={16} />
        <span className="tabular-nums text-muted">{w ? `${pad(w.h)}:${pad(w.mi)}` : 'No date'}</span>
        <span className="truncate">{t.caption || '(media)'}</span>
      </div>
    )
  }
  const today = zoned(new Date(), tz).ymd
  const total = dated.filter((t) => days.some((d) => ymd(d) === z(t).ymd)).length
  const title = view === 'month' ? anchor.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
    : `${days[0].toLocaleDateString(undefined, { day: 'numeric', month: 'short' })} – ${days[days.length - 1].toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })}`
  const viewTabs = (
    <div className="inline-flex rounded-xl border border-lav-200 bg-surface p-1" role="tablist" aria-label="Calendar view" data-testid="calendar-view">
      {[['list', 'List', List], ['week', 'Week', CalendarDays], ['month', 'Month', CalendarDays]].map(([v, l, I]) => (
        <button key={v} role="tab" aria-selected={view === v} onClick={() => setQuery({ view: v })} data-testid={`calendar-view-${v}`}
          className={cx('flex cursor-pointer items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] transition', view === v ? 'bg-lav-100 font-medium text-ink' : 'text-muted hover:text-ink')}><I className="size-3.5" />{l}</button>
      ))}
    </div>
  )

  return (
    <div className="space-y-4" data-testid="page-calendar">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold tracking-tight" data-testid="calendar-title">{title}</h1>
        <div className="flex gap-1">
          <Button size="icon" variant="outline" aria-label="Previous" onClick={() => shift(-1)} data-testid="calendar-prev"><ChevronLeft className="size-4" /></Button>
          <Button size="sm" variant="outline" className="h-9" onClick={() => setQuery({ date: '' })} data-testid="calendar-today">Today</Button>
          <Button size="icon" variant="outline" aria-label="Next" onClick={() => shift(1)} data-testid="calendar-next"><ChevronRight className="size-4" /></Button>
        </div>
        <Badge tone="lav" data-testid="calendar-total">{total} post{total === 1 ? '' : 's'}</Badge>
        <div className="ml-auto flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-[13px] text-ink-2"><Switch checked={showNoDate} onChange={(v) => setQuery({ nodate: v ? '1' : '' })} label="Show No Date drafts" testid="calendar-nodate-toggle" /><CalendarOff className="size-3.5" />No date</label>
          <label className="flex items-center gap-2 text-[13px] text-ink-2"><Switch checked={showSlots} onChange={(v) => setQuery({ slots: v ? '' : '0' })} label="Show posting times" testid="calendar-slots-toggle" /><Clock3 className="size-3.5" />Posting times</label>
          <Button variant="primary" onClick={() => setQuery({ compose: 'new', channels: fl.f.ch || '' })} data-testid="calendar-new"><Plus className="size-4" />New post</Button>
        </div>
      </div>

      <FilterBar fl={fl} page="calendar" extra={viewTabs} />

      <div className={cx('grid gap-4', showNoDate && 'xl:grid-cols-[minmax(0,1fr)_260px]')}>
        <div className="min-w-0">
          {rows === null ? <Skeleton className="h-[560px] w-full rounded-2xl" data-testid="calendar-loading" /> : view === 'list' ? (
            dated.length ? (
              <div className="space-y-6" data-testid="calendar-list">
                {days.filter((d) => dayItems(d).length).map((d) => (
                  <section key={ymd(d)}>
                    <h3 className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted">{d.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })}<Badge tone="gray" data-testid="calendar-day-count">{dayItems(d).length}</Badge></h3>
                    <div className="space-y-3">{dayItems(d).map((t) => <PostCard key={t.id} t={t} tz={tz} onChange={load} />)}</div>
                  </section>
                ))}
              </div>
            ) : <Empty icon={CalendarDays} title="No posts match these filters" action={fl.active ? <Button data-testid="calendar-clear-filters" variant="outline" onClick={fl.clear}>Clear filters</Button> : <Button data-testid="calendar-create-post" variant="primary" onClick={() => setQuery({ compose: 'new' })}>Create post</Button>}>Try another channel, type or date range.</Empty>
          ) : view === 'week' ? (
            <div ref={scroller} className="scroll-thin max-h-[calc(100vh-17rem)] min-h-[420px] overflow-auto rounded-2xl border border-lav-200 bg-surface shadow-soft" data-testid="calendar-week">
              <div className="grid min-w-[980px] grid-cols-[60px_repeat(7,minmax(0,1fr))]">
                <div className="sticky left-0 top-0 z-20 flex items-end justify-end border-b border-lav-200 bg-surface p-1 text-[9px] text-muted" title="Timezone">{tz.split('/').pop().replace('_', ' ')}</div>
                {days.map((d) => (
                  <div key={ymd(d)} className={cx('sticky top-0 z-10 border-b border-l border-lav-200 bg-surface px-2 py-2 text-center text-xs font-semibold', ymd(d) === today && 'text-lav-700')}>
                    <span className="block text-[11px] font-medium uppercase tracking-wider text-muted">{d.toLocaleDateString(undefined, { weekday: 'short' })}</span>
                    <span className={cx('mt-0.5 inline-grid size-7 place-items-center rounded-full text-sm', ymd(d) === today && 'bg-lav-600 text-white')}>{d.getDate()}</span>
                    <span className="block text-[10px] font-normal text-muted" data-testid="calendar-day-count">{dayItems(d).length ? `${dayItems(d).length} post${dayItems(d).length === 1 ? '' : 's'}` : '\u00a0'}</span>
                  </div>
                ))}
                {HOURS.map((h) => [
                  <div key={'h' + h} className="sticky left-0 z-[5] border-b border-lav-100 bg-surface pr-2 pt-1 text-right text-[10px] tabular-nums text-muted">{pad(h)}:00</div>,
                  ...days.map((d) => {
                    const items = dayItems(d).filter((t) => z(t).h === h)
                    const slots = slotsFor(d, h)
                    return (
                      <div key={ymd(d) + h} data-testid="calendar-cell" data-empty={items.length ? undefined : '1'} onDragOver={(e) => e.preventDefault()} onDrop={(e) => move(e.dataTransfer.getData('text/plain'), d, h)}
                        onClick={(e) => e.target === e.currentTarget && compose(d, h)} title={items.length ? undefined : `Schedule a post ${d.toLocaleDateString()} ${pad(h)}:00`}
                        className={cx('group relative min-h-14 min-w-0 cursor-pointer space-y-1 border-b border-l border-lav-100 p-1 pr-6 transition-colors hover:bg-lav-50', ymd(d) === today && 'bg-lav-50/50')}>
                        {items.map((t) => <div key={t.id}>{Chip({ t })}</div>)}
                        {!items.length && slots.slice(0, 2).map((s, i) => (
                          <button key={i} onClick={() => compose(d, Number(s.time.slice(0, 2)), s.c.id)} data-testid="calendar-slot"
                            className="flex w-full min-w-0 cursor-pointer items-center gap-1 rounded-lg border border-dashed border-lav-300 px-1.5 py-0.5 text-[10px] text-muted hover:border-lav-500 hover:text-lav-700">
                            <Clock3 className="size-3 shrink-0" />{s.time}<span className="truncate">· @{s.c.username}</span>
                          </button>
                        ))}
                        <button aria-label={`Schedule post ${ymd(d)} ${h}:00`} onClick={() => compose(d, h)} data-testid="calendar-add"
                          className="absolute right-1 top-1 cursor-pointer rounded-md bg-surface p-0.5 text-lav-600 opacity-0 shadow transition-opacity focus:opacity-100 group-hover:opacity-100"><Plus className="size-3" /></button>
                      </div>
                    )
                  }),
                ])}
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-[repeat(7,minmax(0,1fr))] overflow-hidden rounded-2xl border border-lav-200 bg-surface shadow-soft" data-testid="calendar-month">
              {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((d) => <div key={d} className="border-b border-lav-100 py-2 text-center text-xs font-semibold text-muted">{d}</div>)}
              {days.map((d) => {
                const items = dayItems(d)
                const expanded = openDay === ymd(d)
                const shown = expanded ? items : items.slice(0, MONTH_MAX)
                return (
                  <div key={ymd(d)} data-testid="calendar-cell" onDragOver={(e) => e.preventDefault()} onDrop={(e) => move(e.dataTransfer.getData('text/plain'), d)}
                    onClick={(e) => e.target === e.currentTarget && compose(d)}
                    className={cx('group relative min-h-32 min-w-0 cursor-pointer space-y-1 border-b border-l border-lav-100 p-1.5 transition-colors hover:bg-lav-50/70', d.getMonth() !== anchor.getMonth() && 'bg-lav-50/60 text-muted')}>
                    <div className="pointer-events-none flex items-center gap-1.5">
                      <span className={cx('text-xs font-semibold', ymd(d) === today && 'inline-grid size-6 place-items-center rounded-full bg-lav-600 text-white')}>{d.getDate()}</span>
                      {items.length > 0 && <span className="rounded-full bg-lav-100 px-1.5 text-[10px] font-medium text-lav-700" data-testid="calendar-day-count">{items.length}</span>}
                    </div>
                    {shown.map((t) => <div key={t.id}>{Chip({ t })}</div>)}
                    {items.length > MONTH_MAX && (
                      <button onClick={() => setOpenDay(expanded ? null : ymd(d))} data-testid="calendar-more" className="cursor-pointer rounded-md px-1.5 py-0.5 text-[11px] font-medium text-lav-700 hover:bg-lav-100">
                        {expanded ? 'Show less' : `${items.length - MONTH_MAX} more`}
                      </button>
                    )}
                    <button aria-label={`Schedule post ${ymd(d)}`} onClick={() => compose(d)} data-testid="calendar-add" className="absolute right-1 top-1 cursor-pointer rounded-md bg-surface p-0.5 text-lav-600 opacity-0 shadow transition-opacity focus:opacity-100 group-hover:opacity-100"><Plus className="size-3" /></button>
                  </div>
                )
              })}
            </div>
          )}
          <p className="mt-2 text-xs text-muted">Click any empty slot to schedule there · drag a post (or a No date draft) onto the calendar to reschedule · times in {tz}.</p>
        </div>

        {showNoDate && (
          <aside className="rounded-2xl border border-lav-200 bg-surface p-3 shadow-soft xl:self-start" data-testid="calendar-nodate" aria-label="Drafts with no date">
            <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold"><CalendarOff className="size-4 text-lav-600" />No date <Badge tone="gray">{undated.length}</Badge></h3>
            {undated.length ? <div className="space-y-1.5">{undated.map((t) => <div key={t.id}>{Chip({ t })}</div>)}</div>
              : <p className="text-xs text-muted">Drafts without a date land here. Drag one onto the calendar to schedule it.</p>}
          </aside>
        )}
      </div>
    </div>
  )
}
