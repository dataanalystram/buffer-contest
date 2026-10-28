import { Reorder } from 'motion/react'
import { CalendarDays, ListOrdered, PenSquare, Settings2, Inbox, List, MessagesSquare, ChartNoAxesColumn, ShieldCheck, Send, AlertCircle, FileText, Hand } from 'lucide-react'
import { useEffect, useState } from 'react'
import { get, post } from '../api'
import { go, setQuery, useRoute } from '../router'
import { useStore } from '../store'
import { Badge, Button, ChannelAvatar, cx, Empty, SkeletonList, Tabs, useToast } from '../ui'
import PostCard from '../components/PostCard'
import BulkBar from '../components/BulkBar'
import FilterBar, { useFilters, zoned } from '../components/FilterBar'

const TABS = [
  ['queue', 'Queue', 'scheduled,sending,processing'],
  ['drafts', 'Drafts', 'draft'],
  ['approvals', 'Approvals', 'pending_approval'],
  ['action', 'Your turn', 'needs_action'],
  ['sent', 'Sent', 'sent'],
  ['failed', 'Failed', 'failed'],
]

export default function Queue() {
  const { channels } = useStore()
  const { parts, query } = useRoute()
  const toast = useToast()
  const cid = parts[1]
  const ch = channels.find((c) => c.id === cid)
  const tab = query.tab || 'queue'
  const [rows, setRows] = useState(null)
  const [counts, setCounts] = useState({})
  const [picked, setPicked] = useState([])
  const status = TABS.find((x) => x[0] === tab)[2]
  const fields = ['ch', 'type', 'tag', 'campaign', 'q', 'tz']   // status comes from the tabs
  const fl = useFilters({ fields })
  const filterKey = fields.map((k) => fl.f[k]).join('|')

  const load = async () => {
    const params = fl.params({ status })
    if (cid) params.set('channel_id', cid)
    setRows(await get('/api/posts?' + params))
    const cp = fl.params(); cp.delete('status'); if (cid) cp.set('channel_id', cid)
    const all = await get('/api/posts?' + cp)
    const c = {}
    for (const [k, , st] of TABS) c[k] = all.filter((t) => st.split(',').includes(t.status)).length
    setCounts(c)
  }
  useEffect(() => { setPicked([]); load() }, [cid, tab, filterKey])
  // a deep link to one post (#/queue?post=…, e.g. from a notification) opens the tab that post is in
  useEffect(() => {
    if (!query.post || query.tab) return
    get(`/api/targets/${query.post}`).then((t) => { const hit = TABS.find(([, , st]) => st.split(',').includes(t.status)); if (hit && hit[0] !== 'queue') setQuery({ tab: hit[0] }) }).catch(() => {})
  }, [query.post])
  useEffect(() => { const f = () => load(); window.addEventListener('huhq:posts-changed', f); return () => window.removeEventListener('huhq:posts-changed', f) })
  useEffect(() => { if (tab !== 'queue') return; const i = setInterval(load, 8000); return () => clearInterval(i) }, [tab, cid])

  async function reorder(next) {
    setRows(next)
    if (!cid) return
    const queued = next.filter((t) => t.schedule_mode === 'queue' && t.status === 'scheduled')
    try { setRows(await post(`/api/queue/${cid}/order`, { ids: queued.map((t) => t.id) })); toast('Queue reordered') } catch (e) { toast(e.message, 'err'); load() }
  }
  const canDrag = tab === 'queue' && !!cid && !fl.active

  return (
    <div className="space-y-5" data-testid="page-queue">
      <div className="flex flex-wrap items-center gap-4">
        {ch ? <ChannelAvatar ch={ch} size={48} /> : <div className="grid size-12 place-items-center rounded-2xl bg-lav-200 text-lav-700"><ListOrdered className="size-6" /></div>}
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-semibold tracking-tight">{ch ? `@${ch.username}` : 'All channels'}</h1>
          <p className="text-sm text-muted">{ch ? `${ch.provider} · ${ch.timezone} · ${ch.daily_limit}/day limit${ch.paused ? ' · PAUSED' : ''}` : 'Every post across every channel'}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex rounded-xl border border-lav-200 bg-surface p-1" role="group" aria-label="View">
            <span className="flex items-center gap-1.5 rounded-lg bg-lav-100 px-3 py-1.5 text-[13px] font-medium" aria-current="true"><List className="size-3.5" />List</span>
            <button onClick={() => go('/calendar', cid ? { ch: cid } : {})} data-testid="queue-calendar" className="flex cursor-pointer items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] text-muted hover:text-ink"><CalendarDays className="size-3.5" />Calendar</button>
          </div>
          {ch && <Button variant="outline" onClick={() => go(`/channel/${cid}`, { tab: 'schedule' })} data-testid="queue-posting-schedule"><Settings2 className="size-4" />Posting schedule</Button>}
          <Button variant="primary" onClick={() => go(parts.length ? '/' + parts.join('/') : '/queue', { ...query, compose: 'new', channels: cid || '' })} data-testid="queue-new"><PenSquare className="size-4" />New post</Button>
        </div>
      </div>
      {ch && (
        <nav className="-mt-1 flex gap-1 border-b border-lav-200" aria-label="Channel sections" data-testid="channel-section-tabs">
          {[['Publish', `#/queue/${cid}`, ListOrdered, true], ['Community', `#/channel/${cid}?tab=community`, MessagesSquare], ['Insights', `#/channel/${cid}?tab=insights`, ChartNoAxesColumn]].map(([l, h, I, on]) => (
            <a data-testid="queue-a" key={l} href={h} className={cx('-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm', on ? 'border-lav-600 font-medium text-ink' : 'border-transparent text-muted hover:text-ink')}><I className="size-4" />{l}</a>
          ))}
        </nav>
      )}

      <FilterBar fl={fl} fields={fields} page="publish" lockChannel={!!cid} />

      <div className="flex flex-wrap items-center gap-3">
        <Tabs testid="queue-tabs" value={tab} onChange={(v) => setQuery({ tab: v })} items={TABS.map(([v, l]) => [v, l, counts[v]])} />
        {cid && <a href="#/queue" className="ml-auto text-[13px] font-medium text-lav-700 hover:underline" data-testid="queue-filter-all">All channels</a>}
      </div>

      {picked.length > 0 && <BulkBar picked={picked} rows={rows || []} tab={tab} onPick={setPicked} onDone={() => { setPicked([]); load() }} />}

      {canDrag && rows?.length > 1 && <p className="text-xs text-muted">Drag posts to reorder - they keep the slot times, in the new order.</p>}
      {rows === null ? <SkeletonList rows={3} testid="queue-loading" /> : rows.length === 0 ? (
        <Empty icon={{ queue: Inbox, drafts: FileText, approvals: ShieldCheck, action: Hand, sent: Send, failed: AlertCircle }[tab] || Inbox} title={{ queue: 'Your queue is empty', drafts: 'No drafts', approvals: 'Nothing waiting for approval', action: 'Nothing for you to finish', sent: 'Nothing sent yet', failed: 'No failed posts' }[tab]}
          action={tab === 'queue' && <Button data-testid="queue-create-post" variant="primary" onClick={() => setQuery({ compose: 'new', channels: cid || '' })}>Create post</Button>}>
          {{ queue: 'Line up a few posts and HuhQueue drops each one into the channel’s next posting slot.',
            drafts: 'Save anything half-finished here. Drafts never go out until you queue them.',
            approvals: 'Posts from contributors and agent tokens wait here. Approve, or reject with a note saying what to change.',
            action: '“Notify me” posts and TikTok inbox uploads appear here when it is time to finish them in the app.',
            sent: 'Published posts show up here with their live numbers.', failed: 'Nothing failed. Errors would show here with a one-click retry.' }[tab]}
        </Empty>
      ) : canDrag ? (
        <Reorder.Group axis="y" values={rows} onReorder={setRows} className="space-y-3" data-testid="queue-list">
          {rows.map((t) => (
            <Reorder.Item key={t.id} value={t} onDragEnd={() => reorder(rows)}>
              <PostCard t={t} tz={fl.tz} onChange={load} draggable highlight={query.post === t.id} selected={picked.includes(t.id)} onSelect={(on) => setPicked(on ? [...picked, t.id] : picked.filter((x) => x !== t.id))} />
            </Reorder.Item>
          ))}
        </Reorder.Group>
      ) : (
        <div className="space-y-3" data-testid="queue-list">
          {rows.map((t, i) => {
            const dayOf = (x) => { const iso = x.status === 'sent' ? x.published_at : x.scheduled_at; return iso ? new Date(iso).toLocaleDateString(undefined, { timeZone: fl.tz, weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' }) : '' }
            const day = dayOf(t)
            const prev = i ? dayOf(rows[i - 1]) : null
            return (
              <div key={t.id}>
                {['queue', 'sent'].includes(tab) && day !== prev && <h3 className="mb-2 mt-4 text-xs font-semibold uppercase tracking-wider text-muted">{day || 'Unscheduled'}</h3>}
                <PostCard t={t} tz={fl.tz} onChange={load} highlight={query.post === t.id} selected={picked.includes(t.id)} onSelect={(on) => setPicked(on ? [...picked, t.id] : picked.filter((x) => x !== t.id))} />
              </div>
            )
          })}
        </div>
      )}
      {tab === 'approvals' && rows?.length > 0 && <Badge tone="amber">Contributors and agents request; editors and up approve.</Badge>}
    </div>
  )
}
