// Channel page "Sent / History": everything on the account from the network, including posts made elsewhere.
// (Publish, Community, Insights and Settings live in ChannelPublish / ChannelCommunity / ChannelInsights / ChannelSettings.)
import { ExternalLink, Film, Heart, Images, MessageCircle, RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { get } from '../api'
import { Button, Empty, fmt, useToast } from '../ui'
import FilterBar, { useFilters } from '../components/FilterBar'

const when = (ts) => (ts ? new Date(ts.replace(/\+0000$/, 'Z')).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '')

// network media_type/product_type -> the same post types the filter bar uses
const histType = (m) => (m.media_product_type === 'STORY' || m.product_type === 'STORY') ? 'story'
  : (m.product_type === 'REELS') ? 'reel' : m.media_type === 'CAROUSEL_ALBUM' ? 'carousel' : m.media_type === 'VIDEO' ? (m.product_type === 'SHORTS' ? 'short' : 'video') : 'post'

export function HistoryTab({ ch }) {
  const toast = useToast()
  const fl = useFilters({ fields: ['type', 'q'] })
  const [items, setItems] = useState(null)
  const [next, setNext] = useState('')
  const [busy, setBusy] = useState(false)
  const [supported, setSupported] = useState(true)
  const load = async (after = '') => {
    setBusy(true)
    try {
      const r = await get(`/api/channels/${ch.id}/history?limit=24${after ? `&after=${encodeURIComponent(after)}` : ''}`)
      setSupported(r.supported)
      setItems((x) => (after ? [...(x || []), ...r.items] : r.items))
      setNext(r.next || '')
    } catch (e) { toast(e.message, 'err'); setItems((x) => x || []) } finally { setBusy(false) }
  }
  useEffect(() => { load() }, [ch.id])
  if (items === null) return <p className="text-muted" data-testid="history-loading">Loading posts from the network…</p>
  if (!supported) return <Empty icon={Images} title="History isn’t available for this network">Posts you publish with HuhQueue still show in the queue’s Sent tab.</Empty>
  if (!items.length) return <Empty icon={Images} title="No posts yet">Nothing has been posted on @{ch.username} yet.</Empty>
  const types = fl.list('type'), q = (fl.f.q || '').toLowerCase()
  const shown = items.filter((m) => (!types.length || types.includes(histType(m))) && (!q || (m.caption || '').toLowerCase().includes(q)))
  return (
    <div className="space-y-4" data-testid="channel-history">
      <div className="flex items-center gap-2 text-sm text-muted"><span>Everything on @{ch.username}, newest first - including posts made outside HuhQueue.</span>
        <Button size="sm" variant="ghost" className="ml-auto" loading={busy} onClick={() => load()} data-testid="history-refresh"><RefreshCw className="size-3.5" />Refresh</Button></div>
      <FilterBar fl={fl} fields={['type', 'q']} page="history" />
      {!shown.length && <Empty icon={Images} title="No posts match these filters"><Button data-testid="channel-tabs-clear-filters" variant="outline" onClick={fl.clear}>Clear filters</Button></Empty>}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4" data-testid="history-grid">
        {shown.map((m) => (
          <a key={m.external_id} href={m.permalink} target="_blank" rel="noreferrer" data-testid="history-item"
            className="group overflow-hidden rounded-2xl border border-lav-100 bg-surface shadow-soft transition hover:-translate-y-0.5 hover:shadow-pop">
            <div className="relative aspect-square bg-lav-50">
              {m.thumbnail_url ? <img src={m.thumbnail_url} alt="" loading="lazy" referrerPolicy="no-referrer" className="size-full object-cover" />
                : <div className="grid size-full place-items-center p-4 text-center text-xs text-ink-2">{(m.caption || '').slice(0, 140) || 'Text post'}</div>}
              {(m.media_type === 'VIDEO' || m.product_type === 'REELS') && <span className="absolute right-2 top-2 rounded-full bg-black/60 p-1 text-white"><Film className="size-3.5" /></span>}
              {m.media_type === 'CAROUSEL_ALBUM' && <span className="absolute right-2 top-2 rounded-full bg-black/60 p-1 text-white"><Images className="size-3.5" /></span>}
            </div>
            <div className="space-y-1 p-3">
              <p className="line-clamp-2 text-sm">{m.caption || <span className="text-muted">No caption</span>}</p>
              <div className="flex items-center gap-3 text-xs text-muted">
                <span>{when(m.timestamp)}</span>
                <span className="ml-auto flex items-center gap-0.5"><Heart className="size-3" />{fmt(m.like_count)}</span>
                <span className="flex items-center gap-0.5"><MessageCircle className="size-3" />{fmt(m.comments_count)}</span>
                <ExternalLink className="size-3 opacity-0 group-hover:opacity-100" />
              </div>
            </div>
          </a>
        ))}
      </div>
      {next && <div className="text-center"><Button variant="outline" loading={busy} onClick={() => load(next)} data-testid="history-more">Load older posts</Button></div>}
    </div>
  )
}
