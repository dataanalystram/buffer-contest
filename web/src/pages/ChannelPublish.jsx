// Channel page "Publish": Queue / Drafts / Approvals / Sent, weekly goal + timezone pills, List / Calendar,
// tag filter, "Add a post to <network> at <time>" chips for empty slots, Sent cards with their numbers, and (visual networks)
// a planned Grid preview: queued posts above the live profile grid, drag (or arrows) to reorder the queue.
import { ArrowLeft, ArrowRight, CalendarDays, Clock, Copy, ExternalLink, Eye, FileText, Film, Globe, Grid3x3, Heart, Images, Inbox, List, MessageCircle, PenSquare, Plus, RefreshCw, Send, ShieldCheck, Target, TrendingUp, Users } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { get, post } from '../api'
import BulkUpload from '../components/BulkUpload'
import PostCard from '../components/PostCard'
import { go, href, setQuery, useRoute } from '../router'
import { Badge, Button, Card, cx, Empty, fmt, NETWORKS, Select, Skeleton, SkeletonList, Tabs, useToast } from '../ui'

const PUB_TABS = [['queue', 'Queue', 'scheduled,sending,processing,needs_action'], ['drafts', 'Drafts', 'draft'],
  ['approvals', 'Approvals', 'pending_approval'], ['sent', 'Sent', 'sent']]

const dayKey = (iso) => (iso || '').slice(0, 10)
const dayLabel = (key) => new Date(key + 'T12:00').toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'short' })

export function compose(ch, extra = {}) {
  const { path, query } = { path: `/channel/${ch.id}`, query: { tab: 'publish' } }
  go(path, { ...query, compose: 'new', channels: ch.id, ...extra })
}

export function SlotChip({ ch, slot, compact }) {
  const net = NETWORKS[ch.provider]?.name || ch.provider
  return (
    <button type="button" data-testid="empty-slot" data-at={slot.local} onClick={() => compose(ch, { when: 'custom', at: slot.local })}
      aria-label={`Add a post to ${net} at ${slot.time} on ${slot.day}`}
      className={cx('group flex w-full cursor-pointer items-center gap-2 rounded-2xl border border-dashed border-lav-300 bg-lav-50/40 text-left text-sm text-muted transition hover:border-lav-500 hover:bg-lav-100 hover:text-ink',
        compact ? 'px-2 py-1.5 text-xs' : 'px-4 py-3')}>
      <span className="tabular-nums font-medium text-ink-2">{slot.time}</span>
      <Plus className="size-3.5 text-lav-600" />
      <span className="truncate">Add a post to {net} at {slot.time}</span>
    </button>
  )
}

function Metric({ icon: Icon, label, value, testid }) {
  return <span className="flex items-center gap-1" title={label} data-testid={testid}><Icon className="size-3.5 text-lav-600" /><b className="tabular-nums text-ink">{value}</b><span className="text-muted">{label}</span></span>
}

export function SentCard({ t, onChange }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const i = t.insights || {}
  const views = i.views ?? i.impressions ?? i.reach
  const reactions = i.likes
  const eng = (i.likes || 0) + (i.comments || 0) + (i.shares || 0) + (i.saved || 0)
  const rate = i.engagement_rate ?? (views ? Math.round((10000 * eng) / views) / 100 : null)
  const thumb = t.media?.[0]?.thumb_url || t.media?.[0]?.url
  return (
    <Card className="flex gap-4 p-4" data-testid="sent-card">
      {thumb ? <img src={thumb} alt="" className="size-20 shrink-0 rounded-xl object-cover" /> : <div className="grid size-20 shrink-0 place-items-center rounded-xl bg-lav-100 text-lav-700"><FileText className="size-6" /></div>}
      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex items-center gap-2 text-xs text-muted"><Badge tone="green">Sent</Badge><span>{t.published_at ? new Date(t.published_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : ''}</span><Badge tone="gray">{t.format}</Badge></div>
        <p className="line-clamp-2 text-sm">{t.caption || <span className="text-muted">No caption</span>}</p>
        <div className="flex flex-wrap gap-4 text-xs" data-testid="sent-metrics">
          <Metric icon={Eye} label="views" value={fmt(views)} testid="sent-views" />
          <Metric icon={Heart} label="reactions" value={fmt(reactions)} testid="sent-reactions" />
          <Metric icon={MessageCircle} label="comments" value={fmt(i.comments)} testid="sent-comments" />
          <Metric icon={TrendingUp} label="eng. rate" value={rate === null || rate === undefined ? '—' : `${rate}%`} testid="sent-eng" />
        </div>
        <div className="flex flex-wrap gap-1">
          {t.permalink && <a href={t.permalink} target="_blank" rel="noreferrer" data-testid="sent-open" className="inline-flex h-8 items-center gap-1.5 rounded-xl px-3 text-[13px] text-ink-2 hover:bg-lav-100"><ExternalLink className="size-3.5" />Open post</a>}
          {t.permalink && <Button size="sm" variant="ghost" data-testid="sent-copy-link" onClick={async () => { try { await navigator.clipboard.writeText(t.permalink); toast('Link copied') } catch { toast('Copy failed', 'err') } }}><Copy className="size-3.5" />Copy link</Button>}
          <Button size="sm" variant="ghost" loading={busy} data-testid="sent-refresh" onClick={async () => {
            setBusy(true)
            try { await post(`/api/targets/${t.id}/refresh-insights`); toast('Insights refreshed'); onChange?.() } catch (e) { toast(e.message, 'err') } finally { setBusy(false) }
          }}><RefreshCw className="size-3.5" />Refresh numbers</Button>
        </div>
      </div>
    </Card>
  )
}

export function PublishHeader({ ch, sum }) {
  if (!sum) return <div className="flex gap-2" data-testid="channel-publish-header-loading"><Skeleton className="h-8 w-48 rounded-full" /><Skeleton className="h-8 w-32 rounded-full" /></div>
  const met = sum.sent_this_week >= sum.goal
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="channel-publish-header">
      <a href={href(`/channel/${ch.id}`, { tab: 'schedule' })} data-testid="channel-goal-pill" title="Posting goal - change it in the posting schedule"
        className={cx('inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[13px] font-medium', met ? 'bg-emerald-100 text-emerald-800' : 'bg-lav-100 text-ink-2 hover:bg-lav-200')}>
        <Target className="size-3.5" />{sum.sent_this_week}/{sum.goal} posts sent this week
      </a>
      <a href={href(`/channel/${ch.id}`, { tab: 'schedule' })} data-testid="channel-tz-pill" title={`Queue times are in ${sum.timezone}`}
        className="inline-flex items-center gap-1.5 rounded-full bg-lav-100 px-3 py-1.5 text-[13px] text-ink-2 hover:bg-lav-200"><Globe className="size-3.5" />{sum.timezone_label}</a>
      {sum.paused && <Badge tone="amber" data-testid="channel-paused-pill">Queue paused</Badge>}
      {!sum.slots && <a href={href(`/channel/${ch.id}`, { tab: 'schedule' })} className="text-[13px] text-lav-700 underline" data-testid="channel-no-slots">Set posting times</a>}
    </div>
  )
}

const GRID_NETWORKS = ['instagram', 'tiktok', 'fake']

/** Move the visible queue tiles to a new order and slot them back into the full queue (stories keep their place). */
export function reorderQueue(queueIds, visibleAsc, from, to) {
  const next = [...visibleAsc]
  const [m] = next.splice(from, 1)
  next.splice(to, 0, m)
  const vis = new Set(visibleAsc)
  let k = 0
  return queueIds.map((id) => (vis.has(id) ? next[k++] : id))
}

function PlannedGrid({ ch }) {
  const toast = useToast()
  const [g, setG] = useState(null)
  const [drag, setDrag] = useState(null)
  const load = () => get(`/api/channels/${ch.id}/grid`).then(setG).catch((e) => { toast(e.message, 'err'); setG({ planned: [], live: [], queue_ids: [] }) })
  useEffect(() => { load() }, [ch.id])
  useEffect(() => { const f = () => load(); window.addEventListener('huhq:posts-changed', f); return () => window.removeEventListener('huhq:posts-changed', f) })
  if (!g) return <SkeletonList rows={2} testid="planned-grid-loading" />
  // tiles are newest-first (top-left = last to go out); the queue order is oldest-first
  const visibleAsc = g.planned.filter((p) => p.in_queue).map((p) => p.id).reverse()
  const move = async (id, dir) => {
    const from = visibleAsc.indexOf(id)
    const to = from + dir
    if (from < 0 || to < 0 || to >= visibleAsc.length) return
    try { await post(`/api/queue/${ch.id}/order`, { ids: reorderQueue(g.queue_ids, visibleAsc, from, to) }); toast('Queue reordered'); load() } catch (e) { toast(e.message, 'err') }
  }
  const dropOn = (targetId) => {
    if (!drag || drag === targetId) return
    const from = visibleAsc.indexOf(drag), to = visibleAsc.indexOf(targetId)
    setDrag(null)
    if (from < 0 || to < 0) return
    post(`/api/queue/${ch.id}/order`, { ids: reorderQueue(g.queue_ids, visibleAsc, from, to) }).then(() => { toast('Queue reordered'); load() }).catch((e) => toast(e.message, 'err'))
  }
  return (
    <div className="space-y-3" data-testid="planned-grid">
      <p className="text-sm text-muted">How @{ch.username}’s profile will look: planned posts (lavender) above what’s live now. Drag a queued tile — or use the arrows — to change the order; times stay put.</p>
      {g.live_error && <p className="text-xs text-rose-700" data-testid="planned-grid-live-error">Couldn’t load the live grid: {g.live_error}</p>}
      <div className="mx-auto grid max-w-xl grid-cols-3 gap-1" data-testid="planned-grid-tiles">
        {g.planned.map((p) => {
          const qi = visibleAsc.indexOf(p.id)
          return (
            <div key={p.id} data-testid="grid-planned" data-id={p.id} draggable={p.in_queue} aria-roledescription={p.in_queue ? 'draggable queued post' : undefined}
              onDragStart={() => setDrag(p.id)} onDragOver={(e) => p.in_queue && e.preventDefault()} onDrop={() => dropOn(p.id)}
              className={cx('group relative aspect-square overflow-hidden rounded-md bg-lav-100 ring-2 ring-lav-300', p.in_queue && 'cursor-grab', drag === p.id && 'opacity-50')}>
              {p.thumb_url ? <img src={p.thumb_url} alt="" className="size-full object-cover" /> : <div className="grid size-full place-items-center p-2 text-center text-[11px] text-ink-2">{(p.caption || '').slice(0, 90) || 'No media'}</div>}
              <span className="absolute left-1 top-1 rounded-full bg-lav-600 px-1.5 py-0.5 text-[10px] font-medium text-white" data-testid="grid-planned-time">{(p.scheduled_local_iso || '').slice(5, 16).replace('T', ' ') || p.status}</span>
              {p.format === 'reel' && <Film className="absolute right-1 top-1 size-4 text-white drop-shadow" />}
              {p.format === 'carousel' && <Images className="absolute right-1 top-1 size-4 text-white drop-shadow" />}
              {p.collaborators && <span title={`Collaborators: ${p.collaborators}`} className="absolute bottom-1 left-1 rounded-full bg-black/60 p-1 text-white"><Users className="size-3" /></span>}
              {p.in_queue && visibleAsc.length > 1 && (
                <span className="absolute bottom-1 right-1 flex gap-0.5 opacity-0 transition group-hover:opacity-100 group-focus-within:opacity-100">
                  <button type="button" aria-label="Post later" data-testid="grid-move-later" disabled={qi >= visibleAsc.length - 1} onClick={() => move(p.id, 1)} className="cursor-pointer rounded bg-black/60 p-0.5 text-white disabled:opacity-30"><ArrowLeft className="size-3" /></button>
                  <button type="button" aria-label="Post earlier" data-testid="grid-move-earlier" disabled={qi <= 0} onClick={() => move(p.id, -1)} className="cursor-pointer rounded bg-black/60 p-0.5 text-white disabled:opacity-30"><ArrowRight className="size-3" /></button>
                </span>
              )}
            </div>
          )
        })}
        {g.live.map((m) => (
          <a key={m.external_id} href={m.permalink} target="_blank" rel="noreferrer" data-testid="grid-live" className="relative aspect-square overflow-hidden rounded-md bg-lav-50">
            {m.thumbnail_url ? <img src={m.thumbnail_url} alt="" loading="lazy" referrerPolicy="no-referrer" className="size-full object-cover" /> : <div className="grid size-full place-items-center p-2 text-center text-[11px] text-muted">{(m.caption || '').slice(0, 90)}</div>}
          </a>
        ))}
      </div>
      {!g.planned.length && !g.live.length && <Empty icon={Grid3x3} title="Nothing to preview yet" testid="planned-grid-empty">Queue a post and it shows up here as the newest tile.</Empty>}
    </div>
  )
}

function Calendar({ ch, rows, slots }) {
  const days = useMemo(() => {
    const out = []
    const d0 = new Date()
    for (let i = 0; i < 7; i++) { const d = new Date(d0); d.setDate(d0.getDate() + i); out.push(d.toLocaleDateString('sv-SE')) }
    return out
  }, [])
  return (
    <div className="grid gap-2 md:grid-cols-7" data-testid="channel-calendar">
      {days.map((k) => {
        const posts = rows.filter((t) => dayKey(t.scheduled_local_iso || t.published_at) === k)
        const free = slots.filter((s) => dayKey(s.local) === k)
        return (
          <div key={k} className="min-h-40 space-y-1.5 rounded-2xl border border-lav-200 bg-surface p-2" data-testid="calendar-day" data-day={k}>
            <div className="text-xs font-semibold text-ink-2">{new Date(k + 'T12:00').toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' })}</div>
            {posts.map((t) => (
              <button key={t.id} type="button" onClick={() => go(`/channel/${ch.id}`, { tab: 'publish', compose: t.id })} data-testid="calendar-post"
                className="block w-full cursor-pointer rounded-xl bg-lav-100 p-2 text-left text-xs hover:bg-lav-200">
                <span className="flex items-center gap-1 font-medium tabular-nums"><Clock className="size-3" />{(t.scheduled_local_iso || '').slice(11, 16)}</span>
                <span className="line-clamp-2 text-ink-2">{t.caption || 'No caption'}</span>
              </button>
            ))}
            {free.map((s) => <SlotChip key={s.at} ch={ch} slot={s} compact />)}
            {!posts.length && !free.length && <p className="pt-4 text-center text-[11px] text-muted">Nothing planned</p>}
          </div>
        )
      })}
    </div>
  )
}

export function PublishTab({ ch }) {
  const { query } = useRoute()
  const tab = query.ptab || 'queue'
  const hasGrid = GRID_NETWORKS.includes(ch.provider)
  const view = query.view === 'calendar' ? 'calendar' : query.view === 'grid' && hasGrid ? 'grid' : 'list'
  const tag = query.ptag || ''
  const toast = useToast()
  const [rows, setRows] = useState(null)
  const [sum, setSum] = useState(null)
  const load = async () => {
    try {
      const st = PUB_TABS.find((x) => x[0] === tab)[2]
      const [r, s] = await Promise.all([get(`/api/posts?channel_id=${ch.id}&status=${st}${tag ? `&tag=${encodeURIComponent(tag)}` : ''}`),
        get(`/api/channels/${ch.id}/publish-summary?days=${view === 'calendar' ? 7 : 14}`)])
      setRows(r); setSum(s)
    } catch (e) { toast(e.message, 'err'); setRows((x) => x || []) }
  }
  useEffect(() => { setRows(null); load() }, [ch.id, tab, tag, view])
  useEffect(() => { const f = () => load(); window.addEventListener('huhq:posts-changed', f); return () => window.removeEventListener('huhq:posts-changed', f) })

  const slots = tab === 'queue' && !tag ? (sum?.open_slots || []) : []
  // merge queued posts and empty slots into one time-ordered list, grouped by day
  const items = tab === 'queue'
    ? [...(rows || []).map((t) => ({ kind: 'post', key: t.id, at: t.scheduled_local_iso || '~', t })), ...slots.map((s) => ({ kind: 'slot', key: s.at, at: s.local, s }))].sort((a, b) => a.at.localeCompare(b.at))
    : (rows || []).map((t) => ({ kind: 'post', key: t.id, at: '', t }))
  const groups = []
  for (const it of items) {
    const k = tab === 'queue' ? dayKey(it.at) : ''
    if (!groups.length || groups[groups.length - 1].k !== k) groups.push({ k, items: [] })
    groups[groups.length - 1].items.push(it)
  }
  const EmptyIcon = { queue: Inbox, drafts: FileText, approvals: ShieldCheck, sent: Send }[tab]

  return (
    <div className="space-y-4" data-testid="channel-publish">
      <PublishHeader ch={ch} sum={sum} />
      <div className="flex flex-wrap items-center gap-3">
        <Tabs testid="channel-publish-tabs" value={tab} onChange={(v) => setQuery({ ptab: v })} items={PUB_TABS.map(([v, l]) => [v, l, sum?.counts?.[v]])} />
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Select value={tag} onChange={(e) => setQuery({ ptag: e.target.value })} aria-label="Filter by tag" data-testid="channel-tag-filter"
            options={[['', 'All tags'], ...(sum?.tags || []).map((x) => [x, `#${x}`])]} />
          <div role="radiogroup" aria-label="View" className="inline-flex rounded-xl bg-lav-100 p-1">
            {[['list', 'List', List], ['calendar', 'Calendar', CalendarDays], ...(hasGrid ? [['grid', 'Grid', Grid3x3]] : [])].map(([v, l, I]) => (
              <button key={v} role="radio" aria-checked={view === v} data-testid={`channel-view-${v}`} onClick={() => setQuery({ view: v === 'list' ? '' : v })}
                className={cx('flex cursor-pointer items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium', view === v ? 'bg-surface text-ink shadow-soft' : 'text-muted hover:text-ink')}><I className="size-3.5" />{l}</button>
            ))}
          </div>
          <BulkUpload ch={ch} onDone={() => { window.dispatchEvent(new Event('huhq:posts-changed')); load() }} />
          <Button variant="primary" onClick={() => compose(ch)} data-testid="channel-publish-new"><PenSquare className="size-4" />New post</Button>
        </div>
      </div>
      {view === 'grid' ? <PlannedGrid ch={ch} /> : rows === null ? <SkeletonList rows={3} testid="channel-publish-skeleton" /> : view === 'calendar' ? (
        <Calendar ch={ch} rows={rows} slots={tab === 'queue' && !tag ? (sum?.open_slots || []) : []} />
      ) : !items.length ? (
        <Empty icon={EmptyIcon} testid="channel-publish-empty"
          title={tag ? `No ${tab} posts tagged #${tag}` : { queue: `Nothing queued for @${ch.username}`, drafts: 'No drafts', approvals: 'Nothing waiting for approval', sent: 'Nothing sent from HuhQueue yet' }[tab]}
          action={tab === 'queue' && <Button variant="primary" onClick={() => compose(ch)} data-testid="channel-publish-empty-create">Create post</Button>}>
          {tag ? 'Try another tag or clear the filter.' : tab === 'queue' && !sum?.slots ? 'Add posting times in the posting schedule and empty slots show up here, ready to fill.'
            : tab === 'sent' ? 'Posts you publish show here with their views, reactions and comments. Sent / History shows everything on the account.' : null}
        </Empty>
      ) : (
        <div className="space-y-5">
          {groups.map((g) => (
            <section key={g.k || 'all'} className="space-y-2" data-testid="publish-day">
              {g.k && <h3 className="text-sm font-semibold text-ink-2">{g.k === '~' ? 'Waiting for a free slot' : dayLabel(g.k)}</h3>}
              {g.items.map((it) => it.kind === 'slot' ? <SlotChip key={it.key} ch={ch} slot={it.s} />
                : tab === 'sent' ? <SentCard key={it.key} t={it.t} onChange={load} /> : <PostCard key={it.key} t={it.t} onChange={load} />)}
            </section>
          ))}
        </div>
      )}
    </div>
  )
}
