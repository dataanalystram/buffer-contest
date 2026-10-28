// Insights extras: takeaways across all channels (with helpful / not helpful), audience demographics and Story
// insights saved before they expire. Data: /api/insights/takeaways, /demographics, /stories (services/audience.py).
import { CircleDashed, ExternalLink, RefreshCw, ThumbsDown, ThumbsUp } from 'lucide-react'
import { useEffect, useState } from 'react'
import { get, post } from '../api'
import { Badge, Button, Card, cx, Empty, fmt, Select, Stat, Tabs } from '../ui'

const TONE = { good: 'bg-emerald-50 text-emerald-900', warn: 'bg-amber-50 text-amber-900', info: 'bg-lav-50 text-ink-2' }
const TIMEFRAMES = [['this_week', 'This week'], ['this_month', 'This month'], ['last_14_days', 'Last 14 days'], ['last_30_days', 'Last 30 days'], ['last_90_days', 'Last 90 days'], ['prev_month', 'Last month']]
const BREAKDOWNS = [['age', 'Age'], ['gender', 'Gender'], ['country', 'Top countries'], ['city', 'Top cities']]

export const takeawayRange = (range) => (range === 'mtd' ? 'mtd' : ['7', '30', '90'].includes(range) ? `${range}d` : '30d')

export function Takeaways({ range, toast }) {
  const [d, setD] = useState(null)
  useEffect(() => { get(`/api/insights/takeaways?range=${takeawayRange(range)}`).then(setD).catch(() => setD({ takeaways: [] })) }, [range])
  if (!d || !d.takeaways.length) return null
  const vote = async (t, helpful) => {
    const next = t.feedback === helpful ? null : helpful
    try {
      await post('/api/insights/takeaways/feedback', { key: t.key, helpful: next })
      setD({ ...d, takeaways: d.takeaways.map((x) => (x.key === t.key ? { ...x, feedback: next } : x)) })
      if (next !== null) toast('Thanks for the feedback')
    } catch (e) { toast(e.message, 'err') }
  }
  return (
    <Card className="space-y-2 p-5" data-testid="insights-takeaways">
      <div className="flex items-center gap-2"><h2 className="font-semibold">Takeaways across your channels</h2><span className="text-xs text-muted">{d.period?.label} vs the previous period · {d.channels} channel{d.channels === 1 ? '' : 's'}</span></div>
      <ul className="space-y-1.5">
        {d.takeaways.map((t) => (
          <li key={t.key} data-testid="insights-takeaway" data-key={t.key} className={cx('flex items-start gap-2 rounded-xl px-3 py-2 text-sm', TONE[t.tone] || TONE.info)}>
            <span className="flex-1">{t.text}{t.permalink && <a href={t.permalink} target="_blank" rel="noreferrer" className="ml-1 inline-flex align-middle" aria-label="Open post" data-testid="insights-takeaway-link"><ExternalLink className="size-3.5" /></a>}</span>
            <button onClick={() => vote(t, true)} aria-label="Helpful" aria-pressed={t.feedback === true} data-testid="insights-takeaway-helpful" className={cx('cursor-pointer rounded p-1 hover:bg-white/60', t.feedback === true && 'text-emerald-700')}><ThumbsUp className="size-3.5" /></button>
            <button onClick={() => vote(t, false)} aria-label="Not helpful" aria-pressed={t.feedback === false} data-testid="insights-takeaway-unhelpful" className={cx('cursor-pointer rounded p-1 hover:bg-white/60', t.feedback === false && 'text-rose-700')}><ThumbsDown className="size-3.5" /></button>
          </li>
        ))}
      </ul>
    </Card>
  )
}

function Breakdown({ title, rows, testid }) {
  if (!rows?.length) return null
  const max = Math.max(...rows.map((r) => r.pct), 1)
  return (
    <div data-testid={testid}>
      <p className="mb-1.5 text-xs font-medium text-muted">{title}</p>
      <ul className="space-y-1">
        {rows.map((r) => (
          <li key={r.key} className="grid grid-cols-[7.5rem_1fr_3rem] items-center gap-2 text-xs" data-testid="demographics-row">
            <span className="truncate" title={r.label}>{r.label}</span>
            <span className="h-2 rounded-full bg-lav-100"><span className="block h-2 rounded-full bg-gradient-to-r from-lav-600 to-lav-400" style={{ width: `${(100 * r.pct) / max}%` }} /></span>
            <span className="text-right tabular-nums text-ink-2">{r.pct}%</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function Demographics({ channel, toast }) {
  const [d, setD] = useState(null)
  const [who, setWho] = useState('followers')
  const [tf, setTf] = useState('this_month')
  const [busy, setBusy] = useState(false)
  const load = (refresh = false) => {
    setBusy(true)
    const p = new URLSearchParams({ timeframe: tf })
    if (channel) p.set('channel_id', channel)
    if (refresh) p.set('refresh', 'true')
    return get('/api/insights/demographics?' + p).then(setD).catch((e) => toast(e.message, 'err')).finally(() => setBusy(false))
  }
  useEffect(() => { load() }, [channel, tf])
  const rows = (d?.channels || []).filter((c) => c.supported)
  const other = (d?.channels || []).filter((c) => !c.supported)
  return (
    <Card className="space-y-4 p-5" data-testid="insights-demographics">
      <div className="flex flex-wrap items-center gap-2">
        <div><h2 className="font-semibold">Audience demographics</h2><p className="text-xs text-muted">Who follows you and who engages, from the network. Refreshed daily.</p></div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Tabs testid="demographics-who" value={who} onChange={setWho} items={[['followers', 'Followers'], ['engaged', 'Engaged audience']]} />
          {who === 'engaged' && <Select value={tf} onChange={(e) => setTf(e.target.value)} options={TIMEFRAMES} data-testid="demographics-timeframe" aria-label="Engaged audience timeframe" />}
          <Button size="sm" variant="outline" loading={busy} onClick={() => load(true).then(() => toast('Demographics refreshed'))} data-testid="demographics-refresh"><RefreshCw className="size-4" />Refresh</Button>
        </div>
      </div>
      {!d ? <p className="text-sm text-muted">Loading…</p> : !rows.length ? (
        <Empty title="No audience breakdowns yet" testid="demographics-empty">Connect an Instagram professional account. Instagram shares demographics for accounts with 100 or more followers.</Empty>
      ) : rows.map((c) => {
        const sec = c[who] || {}
        return (
          <div key={c.channel_id} className="space-y-3 rounded-2xl border border-lav-100 p-4" data-testid="demographics-channel">
            <div className="flex items-center gap-2 text-sm"><b>@{c.username}</b><Badge tone="lav">{c.provider}</Badge>{c.fetched_at && <span className="text-xs text-muted">updated {new Date(c.fetched_at).toLocaleString()}</span>}</div>
            {c.error && <p className="text-xs text-rose-700" data-testid="demographics-error">{c.error}</p>}
            {c.note && <p className="text-xs text-muted">{c.note}</p>}
            {Object.keys(sec).length ? (
              <div className="grid gap-5 md:grid-cols-2">{BREAKDOWNS.map(([k, l]) => <Breakdown key={k} title={l} rows={sec[k]} testid={`demographics-${k}`} />)}</div>
            ) : !c.note && <p className="text-xs text-muted">No {who === 'engaged' ? 'engaged-audience' : 'follower'} breakdowns for this period.</p>}
            {c.notes?.length > 0 && <details className="text-xs text-muted"><summary className="cursor-pointer">What Instagram said</summary><ul className="mt-1 list-disc pl-4">{c.notes.map((n) => <li key={n}>{n}</li>)}</ul></details>}
          </div>
        )
      })}
      {other.length > 0 && <p className="text-xs text-muted">Not available for {other.map((c) => `@${c.username} (${c.provider})`).join(', ')}: their APIs do not share audience breakdowns.</p>}
    </Card>
  )
}

export function Stories({ channel, toast }) {
  const [d, setD] = useState(null)
  const [busy, setBusy] = useState(false)
  const load = () => get('/api/insights/stories' + (channel ? `?channel_id=${channel}` : '')).then(setD).catch(() => setD({ items: [], summary: {} }))
  useEffect(() => { load() }, [channel])
  const sync = async () => {
    setBusy(true)
    try {
      const r = await post('/api/insights/stories/sync', channel ? { channel_id: channel } : {})
      toast(r.errors?.length ? r.errors[0] : `Saved insights for ${r.stories} live stor${r.stories === 1 ? 'y' : 'ies'}`, r.errors?.length ? 'err' : undefined)
      await load()
    } catch (e) { toast(e.message, 'err') } finally { setBusy(false) }
  }
  const sm = d?.summary || {}
  return (
    <div className="space-y-4" data-testid="insights-stories">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm text-muted">Instagram keeps Story metrics for 24 hours only. HuhQueue saves them every 2 hours while a story is live, so you can still compare them later.</p>
        <Button className="ml-auto" size="sm" variant="outline" loading={busy} onClick={sync} data-testid="stories-sync"><RefreshCw className="size-4" />Save live stories now</Button>
      </div>
      {d?.note && <p className="rounded-xl bg-lav-50 p-3 text-sm text-ink-2" data-testid="stories-note">{d.note}</p>}
      <div className="grid gap-4 sm:grid-cols-4">
        <Stat testid="stories-count" label="Stories" value={fmt(sm.stories)} />
        <Stat testid="stories-avg-reach" label="Avg. reach" value={fmt(sm.avg_reach)} />
        <Stat testid="stories-replies" label="Replies" value={fmt(sm.replies)} />
        <Stat testid="stories-exit" label="Exit rate" value={sm.exit_rate != null ? sm.exit_rate + '%' : '—'} />
      </div>
      {!d ? null : !d.items.length ? <Empty icon={CircleDashed} title="No saved stories yet" testid="stories-empty">Post a Story on Instagram. Its numbers appear here within 2 hours.</Empty> : (
        <Card className="overflow-x-auto p-0">
          <table className="w-full text-sm" data-testid="stories-table">
            <thead><tr className="border-b border-lav-100 bg-lav-50 text-left text-xs text-muted">
              {['Story', 'Posted', 'Views', 'Reach', 'Replies', 'Shares', 'Taps fwd', 'Exits', 'Status'].map((h) => <th key={h} className="px-4 py-2 font-medium">{h}</th>)}</tr></thead>
            <tbody>
              {d.items.map((x) => (
                <tr key={x.id} className="border-b border-lav-100" data-testid="stories-row">
                  <td className="px-4 py-2"><div className="flex items-center gap-2">
                    {x.thumbnail_url ? <img src={x.thumbnail_url} alt="" className="h-12 w-7 rounded object-cover" /> : <div className="h-12 w-7 rounded bg-lav-100" />}
                    <span className="text-xs text-ink-2">@{x.username}{x.permalink && <a href={x.permalink} target="_blank" rel="noreferrer" className="ml-1 inline-flex align-middle" aria-label="Open story" data-testid="stories-link"><ExternalLink className="size-3" /></a>}</span></div></td>
                  <td className="px-4 py-2 text-xs">{x.posted_at ? new Date(x.posted_at).toLocaleString() : '—'}</td>
                  {['views', 'reach', 'replies', 'shares', 'tap_forward', 'tap_exit'].map((k) => <td key={k} className="px-4 py-2 tabular-nums">{fmt(x.metrics[k])}</td>)}
                  <td className="px-4 py-2">{x.live ? <Badge tone="lav" data-testid="stories-live">Live</Badge> : <Badge tone="ink">Saved</Badge>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  )
}
