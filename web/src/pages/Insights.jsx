import { motion } from 'motion/react'
import { CalendarClock, Download, ExternalLink, FileText, RefreshCw, Send, Trash2, Wand2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { del, get, post, put } from '../api'
import { setQuery, useRoute } from '../router'
import { useStore } from '../store'
import { Demographics, Stories, Takeaways } from '../components/InsightsExtras'
import { Badge, Button, Card, ChannelAvatar, cx, Field, fmt, Input, Select, Stat, Switch, Tabs, useToast } from '../ui'

// Metrics are only ones the networks still return in 2026 (Instagram: views replaced impressions/plays).
const COLS = [['views', 'Views'], ['reach', 'Reach'], ['likes', 'Likes'], ['comments', 'Comments'], ['shares', 'Shares'], ['saved', 'Saves'], ['engagement_rate', 'Eng. %']]
const POST_COLS = [...COLS, ['avg_watch_time', 'Avg watch'], ['profile_visits', 'Profile visits'], ['follows', 'Follows']]
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const SECTIONS = [['overview', 'Overview & KPIs'], ['audience', 'Audience growth'], ['channels', 'By channel'], ['posts', 'Top posts'], ['reels', 'Reels & video'],
  ['types', 'Post types'], ['best_times', 'Best time to post'], ['hashtags', 'Hashtags'], ['campaigns', 'Campaigns & tags']]
const FORMATS = [['', 'All formats'], ['post', 'Post'], ['carousel', 'Carousel'], ['reel', 'Reel'], ['story', 'Story'], ['short', 'Short'], ['video', 'Video']]
const VIEWS = [['overview', 'Overview'], ['audience', 'Audience'], ['posts', 'Posts'], ['reels', 'Reels'], ['stories', 'Stories'], ['best', 'Best times'], ['hashtags', 'Hashtags & campaigns'], ['report', 'Report']]

const pct = (v) => (v === null || v === undefined ? '—' : v + '%')
const val = (k, v) => (k === 'engagement_rate' ? pct(v) : k === 'avg_watch_time' ? (v == null ? '—' : v + 's') : fmt(v))

function Delta({ v, testid }) {
  if (v === null || v === undefined) return <span className="text-xs text-muted" data-testid={testid}>no prior data</span>
  return <span data-testid={testid} className={cx('text-xs font-semibold', v >= 0 ? 'text-emerald-600' : 'text-rose-600')}>{v >= 0 ? '+' : ''}{v}% vs previous</span>
}

function Bars({ cur, prev = [], k, testid, label }) {
  const max = Math.max(1, ...cur.map((d) => d[k] || 0), ...prev.map((d) => d[k] || 0))
  return (
    <div className="flex h-40 items-end gap-1" data-testid={testid} role="img" aria-label={label}>
      {cur.length ? cur.map((d, i) => (
        <div key={d.date} className="relative flex h-full flex-1 flex-col items-center justify-end gap-1" title={`${d.date}: ${d[k] || 0}${prev[i] ? ` (previous ${prev[i][k] || 0})` : ''}`}>
          {prev[i] && <div className="absolute bottom-4 w-full rounded-t-md bg-lav-200" style={{ height: `calc(${(100 * (prev[i][k] || 0)) / max}% - 1rem)` }} />}
          <motion.div className="relative w-3/5 rounded-t-md bg-gradient-to-t from-lav-600 to-lav-400" initial={{ height: 0 }} animate={{ height: `calc(${(100 * (d[k] || 0)) / max}% - 1rem)` }} transition={{ delay: i * 0.01 }} />
          <span className="h-3 text-[9px] text-muted">{cur.length <= 31 || i % Math.ceil(cur.length / 15) === 0 ? d.date.slice(5) : ''}</span>
        </div>
      )) : <p className="m-auto text-sm text-muted">Nothing in this range yet.</p>}
    </div>
  )
}

function Line({ series, testid }) {
  const pts = series.map((d, i) => [i, d.followers]).filter(([, v]) => v !== null && v !== undefined)
  if (!pts.length) return <p className="py-14 text-center text-sm text-muted" data-testid={testid}>No follower history yet. HuhQueue records it once a day from today.</p>
  let lo = Math.min(...pts.map((p) => p[1])); let hi = Math.max(...pts.map((p) => p[1]))
  if (lo === hi) { lo -= 1; hi += 1 }
  const n = Math.max(1, series.length - 1)
  const xy = ([i, v]) => [8 + (584 * i) / n, 150 - (130 * (v - lo)) / (hi - lo)]
  const d = pts.map((p, j) => (j ? 'L' : 'M') + xy(p).join(',')).join(' ')
  const area = d + ` L${xy(pts[pts.length - 1])[0]},160 L${xy(pts[0])[0]},160 Z`
  return (
    <div className="relative" data-testid={testid} role="img" aria-label="Followers over time">
      <svg viewBox="0 0 600 170" preserveAspectRatio="none" className="h-48 w-full">
        <defs><linearGradient id="fg" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#a985f3" stopOpacity="0.35" /><stop offset="1" stopColor="#a985f3" stopOpacity="0" /></linearGradient></defs>
        <path d={area} fill="url(#fg)" />
        <path d={d} fill="none" stroke="#8a63e6" strokeWidth="2.5" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
      </svg>
      <span className="absolute left-1 top-0 text-[10px] text-muted">{fmt(hi)}</span>
      <span className="absolute bottom-0 left-1 text-[10px] text-muted">{fmt(lo)}</span>
      <div className="mt-1 flex justify-between text-[10px] text-muted"><span>{series[0].date.slice(5)}</span><span>{series[series.length - 1].date.slice(5)}</span></div>
    </div>
  )
}

function Table({ cols, rows, testid, empty = 'Nothing in this range yet.', rowTestid }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" data-testid={testid}>
        <thead><tr className="border-y border-lav-100 bg-lav-50 text-left text-xs text-muted">{cols.map(([k, l, right]) => <th key={k} className={cx('px-4 py-2 font-medium', right && 'text-right')}>{l}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => <tr key={i} className="border-b border-lav-100 hover:bg-lav-50/60" data-testid={rowTestid}>{cols.map(([k, , right]) => <td key={k} className={cx('px-4 py-2.5', right && 'text-right tabular-nums')}>{r[k]}</td>)}</tr>)}
          {!rows.length && <tr><td colSpan={cols.length} className="px-4 py-8 text-center text-muted">{empty}</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

export default function Insights() {
  const { channels } = useStore()
  const { query } = useRoute()
  const toast = useToast()
  const [d, setD] = useState(null)
  const [s, setS] = useState(null)
  const [tick, setTick] = useState(0)
  const range = query.range || '30'
  const view = query.view || 'overview'
  const sort = query.sort || 'views'
  const params = useMemo(() => {
    const p = new URLSearchParams()
    if (range === 'custom') { if (query.start) p.set('start', query.start); if (query.end) p.set('end', query.end) } else p.set('days', range)
    if (query.channel) p.set('channel_id', query.channel)
    return p
  }, [range, query.channel, query.start, query.end])
  const fparams = useMemo(() => {
    const p = new URLSearchParams(params)
    if (query.format) p.set('format', query.format)
    if (query.campaign) p.set('campaign', query.campaign)
    if (query.tag) p.set('tag', query.tag)
    return p
  }, [params, query.format, query.campaign, query.tag])
  useEffect(() => { get('/api/insights?' + params).then(setD).catch(() => setD({})) }, [params])
  useEffect(() => { get('/api/reports/summary?' + fparams).then(setS).catch(() => setS(null)) }, [fparams, tick])

  const k = d?.kpis || {}
  const cmp = Object.fromEntries((s?.kpis || []).map((x) => [x.metric, x]))
  const days = Object.entries(d?.posts_by_day || {}).sort()
  const max = Math.max(1, ...days.map(([, v]) => v))
  const top = [...(d?.top_posts || [])].sort((a, b) => ((b.insights || {})[sort] || 0) - ((a.insights || {})[sort] || 0))
  const maxF = Math.max(1, ...(d?.channels || []).map((c) => c.followers_count || 0))

  return (
    <div className="space-y-6" data-testid="page-insights">
      <div className="flex flex-wrap items-center gap-3">
        <div><h1 className="text-2xl font-semibold tracking-tight">Insights</h1><p className="text-sm text-muted">Audience growth, post and Reels analytics, best times and branded reports. Refreshed from each network every 6 hours.</p></div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Select value={query.channel || ''} onChange={(e) => setQuery({ channel: e.target.value })} options={[['', 'All channels'], ...channels.map((c) => [c.id, `@${c.username} · ${c.provider}`])]} data-testid="insights-channel" aria-label="Channel" />
          <Tabs testid="insights-range" value={range} onChange={(v) => setQuery({ range: v })} items={[['7', '7 days'], ['30', '30 days'], ['90', '90 days'], ['mtd', 'Month to date'], ['custom', 'Custom']]} />
          <a href={`/api/insights/export.csv?days=${range === 'custom' ? 30 : range}`} data-testid="insights-export"><Button data-testid="insights-export-csv" variant="outline"><Download className="size-4" />Export CSV</Button></a>
        </div>
      </div>
      {range === 'custom' && <div className="flex gap-2"><Input type="date" className="w-auto" value={query.start || ''} onChange={(e) => setQuery({ start: e.target.value })} data-testid="insights-start" aria-label="Start date" /><Input type="date" className="w-auto" value={query.end || ''} onChange={(e) => setQuery({ end: e.target.value })} data-testid="insights-end" aria-label="End date" /></div>}
      <div className="flex flex-wrap items-center gap-2">
        <Tabs testid="insights-view" value={view} onChange={(v) => setQuery({ view: v })} items={VIEWS} className="flex-wrap" />
        {view !== 'overview' && view !== 'audience' && view !== 'stories' && (
          <div className="ml-auto flex flex-wrap gap-2">
            <Select value={query.format || ''} onChange={(e) => setQuery({ format: e.target.value })} options={FORMATS} data-testid="insights-format" aria-label="Format" />
            <Input className="w-36" placeholder="Campaign" value={query.campaign || ''} onChange={(e) => setQuery({ campaign: e.target.value })} data-testid="insights-campaign" aria-label="Campaign" />
            <Input className="w-28" placeholder="Tag" value={query.tag || ''} onChange={(e) => setQuery({ tag: e.target.value })} data-testid="insights-tag" aria-label="Tag" />
          </div>
        )}
      </div>

      {view === 'overview' && <>
        <Takeaways range={range} toast={toast} />
        <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-6">
          <Stat testid="kpi-posts" label="Posts" value={fmt(k.posts)} sub={<Delta v={cmp.posts?.change_pct} testid="kpi-posts-delta" />} />
          <Stat testid="kpi-followers" label="Followers" value={fmt(k.followers)} sub={s?.audience?.net != null ? <span className={cx('text-xs font-semibold', s.audience.net >= 0 ? 'text-emerald-600' : 'text-rose-600')}>{s.audience.net >= 0 ? '+' : ''}{fmt(s.audience.net)} this period</span> : null} />
          <Stat testid="kpi-views" label="Views" value={fmt(k.views)} sub={<Delta v={cmp.views?.change_pct} testid="kpi-views-delta" />} />
          <Stat testid="kpi-reach" label="Reach" value={fmt(k.reach)} sub={<Delta v={cmp.reach?.change_pct} />} />
          <Stat testid="kpi-likes" label="Likes" value={fmt(k.likes)} sub={<Delta v={cmp.likes?.change_pct} />} />
          <Stat testid="kpi-eng" label="Avg. engagement" value={k.engagement_rate != null ? k.engagement_rate + '%' : '—'} sub={<Delta v={cmp.engagement_rate?.change_pct} />} />
        </div>
        <div className="grid gap-6 lg:grid-cols-3">
          <Card className="p-6 lg:col-span-2">
            <h2 className="mb-4 font-semibold">Posts per day</h2>
            <div className="flex h-40 items-end gap-1" data-testid="insights-chart" role="img" aria-label="Posts per day chart">
              {days.length ? days.map(([day, v], i) => (
                <div key={day} className="group flex flex-1 flex-col items-center gap-1" title={`${day}: ${v}`}>
                  <motion.div className="w-full rounded-t-md bg-gradient-to-t from-lav-500 to-lav-300" initial={{ height: 0 }} animate={{ height: `${(100 * v) / max}%` }} transition={{ delay: i * 0.03 }} />
                  <span className="text-[9px] text-muted">{day.slice(5)}</span>
                </div>
              )) : <p className="m-auto text-sm text-muted">No posts in this range yet.</p>}
            </div>
          </Card>
          <Card className="p-6">
            <h2 className="mb-4 font-semibold">Followers by channel</h2>
            <ul className="space-y-3" data-testid="insights-followers">
              {(d?.channels || []).map((c) => (
                <li key={c.id}>
                  <div className="mb-1 flex items-center gap-2 text-sm"><ChannelAvatar ch={channels.find((x) => x.id === c.id) || c} size={22} />@{c.username}<span className="ml-auto tabular-nums">{fmt(c.followers_count)}</span></div>
                  <div className="h-2 rounded-full bg-lav-100"><motion.div className="h-full rounded-full bg-lav-500" initial={{ width: 0 }} animate={{ width: `${(100 * (c.followers_count || 0)) / maxF}%` }} /></div>
                </li>
              ))}
            </ul>
          </Card>
        </div>
        <Card className="p-6">
          <div className="mb-4 flex items-center gap-2"><h2 className="font-semibold">Views per day</h2><span className="text-xs text-muted">dark = this period · light = previous {s?.days || ''} days</span></div>
          <Bars cur={s?.series || []} prev={s?.previous_series || []} k="views" testid="compare-chart" label="Views per day, this period vs previous" />
        </Card>
        <Card className="overflow-hidden">
          <div className="flex items-center justify-between p-6 pb-3"><h2 className="font-semibold">Top posts</h2><span className="text-xs text-muted">click a column to sort</span></div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="insights-table">
              <thead><tr className="border-y border-lav-100 bg-lav-50 text-left text-xs text-muted">
                <th className="px-6 py-2 font-medium">Post</th>
                {COLS.map(([key, l]) => <th key={key} className="px-3 py-2 text-right font-medium"><button className={cx('cursor-pointer', sort === key && 'text-lav-700 underline')} onClick={() => setQuery({ sort: key })} data-testid={`insights-sort-${key}`}>{l}</button></th>)}
                <th />
              </tr></thead>
              <tbody>
                {top.map((t) => (
                  <tr key={t.id} className="border-b border-lav-100 hover:bg-lav-50/60" data-testid="insights-row">
                    <td className="px-6 py-3"><div className="flex items-center gap-3"><ChannelAvatar ch={t.channel} size={28} /><span className="line-clamp-1 max-w-md">{t.caption}</span></div></td>
                    {COLS.map(([key]) => <td key={key} className="px-3 py-3 text-right tabular-nums">{key === 'engagement_rate' ? (t.insights?.[key] != null ? t.insights[key] + '%' : '—') : fmt(t.insights?.[key])}</td>)}
                    <td className="pr-4">{t.permalink && <a href={t.permalink} target="_blank" rel="noreferrer" aria-label="Open post"><ExternalLink className="size-4 text-muted" /></a>}</td>
                  </tr>
                ))}
                {!top.length && <tr><td colSpan={9} className="px-6 py-8 text-center text-muted">No sent posts in this range.</td></tr>}
              </tbody>
            </table>
          </div>
        </Card>
      </>}

      {view === 'audience' && <Audience s={s} onSnap={async () => { const r = await post('/api/reports/audience/snapshot', {}); toast(r.written ? `Recorded today's followers for ${r.written} channel${r.written > 1 ? 's' : ''}` : "Today's followers are already recorded"); setTick((x) => x + 1) }} />}
      {view === 'audience' && <Demographics channel={query.channel} toast={toast} />}
      {view === 'stories' && <Stories channel={query.channel} toast={toast} />}
      {view === 'posts' && <Posts s={s} />}
      {view === 'reels' && <Reels s={s} />}
      {view === 'best' && <BestTimes s={s} channel={query.channel} channels={channels} toast={toast} />}
      {view === 'hashtags' && <Hashtags s={s} />}
      {view === 'report' && <Report fparams={fparams} query={query} toast={toast} />}
    </div>
  )
}

function Audience({ s, onSnap }) {
  const a = s?.audience
  return (
    <div className="space-y-6" data-testid="audience-view">
      <div className="grid gap-4 sm:grid-cols-3">
        <Stat testid="aud-followers" label="Followers now" value={fmt(a?.followers)} />
        <Stat testid="aud-net" label="Net growth" value={a?.net == null ? '—' : (a.net >= 0 ? '+' : '') + fmt(a.net)} sub={a?.previous_net != null ? `previous period: ${a.previous_net >= 0 ? '+' : ''}${fmt(a.previous_net)}` : 'no prior data'} />
        <Stat testid="aud-change" label="Growth vs previous" value={pct(a?.change_pct)} />
      </div>
      <Card className="p-6">
        <div className="mb-3 flex items-center gap-2"><h2 className="font-semibold">Followers over time</h2>
          <Button size="sm" variant="soft" className="ml-auto" onClick={onSnap} data-testid="aud-snapshot"><RefreshCw className="size-3.5" />Record today</Button></div>
        <Line series={a?.series || []} testid="aud-chart" />
        {a?.note && <p className="mt-2 text-xs text-muted">{a.note}</p>}
      </Card>
      <Card className="overflow-hidden">
        <Table testid="aud-table" rowTestid="aud-row" cols={[['ch', 'Channel'], ['start', 'Start', 1], ['end', 'End', 1], ['net', 'Net', 1], ['g', 'Growth', 1]]}
          rows={(a?.channels || []).map((c) => ({ ch: <span className="flex items-center gap-2"><ChannelAvatar ch={c} size={22} />@{c.username}</span>, start: fmt(c.start), end: fmt(c.end),
            net: c.net == null ? '—' : (c.net >= 0 ? '+' : '') + fmt(c.net), g: c.growth_pct == null ? '—' : c.growth_pct + '%' }))} />
      </Card>
    </div>
  )
}

function Posts({ s }) {
  const [sort, setSort] = useState('views')
  const rows = [...(s?.posts || [])].sort((a, b) => ((b.insights || {})[sort] || 0) - ((a.insights || {})[sort] || 0))
  return (
    <Card className="overflow-hidden" data-testid="posts-view">
      <div className="flex items-center justify-between p-6 pb-3"><h2 className="font-semibold">Post analytics</h2><span className="text-xs text-muted">{rows.length} posts · engagement rate = interactions ÷ reach</span></div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm" data-testid="posts-table">
          <thead><tr className="border-y border-lav-100 bg-lav-50 text-left text-xs text-muted"><th className="px-6 py-2 font-medium">Post</th><th className="px-3 py-2 font-medium">Type</th>
            {POST_COLS.map(([key, l]) => <th key={key} className="px-3 py-2 text-right font-medium"><button className={cx('cursor-pointer', sort === key && 'text-lav-700 underline')} onClick={() => setSort(key)} data-testid={`posts-sort-${key}`}>{l}</button></th>)}<th /></tr></thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t.id} className="border-b border-lav-100 hover:bg-lav-50/60" data-testid="posts-row">
                <td className="px-6 py-3"><div className="flex items-center gap-3"><ChannelAvatar ch={t.channel} size={26} /><span className="line-clamp-1 max-w-sm">{t.caption}</span></div></td>
                <td className="px-3"><Badge>{t.format}</Badge></td>
                {POST_COLS.map(([key]) => <td key={key} className="px-3 py-3 text-right tabular-nums">{val(key, t.insights?.[key])}</td>)}
                <td className="pr-4">{t.permalink && <a href={t.permalink} target="_blank" rel="noreferrer" aria-label="Open post"><ExternalLink className="size-4 text-muted" /></a>}</td>
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={POST_COLS.length + 3} className="px-6 py-8 text-center text-muted">No sent posts in this range.</td></tr>}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

function Reels({ s }) {
  const r = s?.reels
  const p = r?.previous || {}
  return (
    <div className="space-y-6" data-testid="reels-view">
      <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-6">
        <Stat testid="reels-count" label="Reels" value={fmt(r?.count)} sub={`previous ${fmt(p.count)}`} />
        <Stat testid="reels-views" label="Views" value={fmt(r?.views)} sub={`previous ${fmt(p.views)}`} />
        <Stat testid="reels-reach" label="Reach" value={fmt(r?.reach)} />
        <Stat testid="reels-avg-watch" label="Avg watch time" value={r?.avg_watch_time != null ? r.avg_watch_time + 's' : '—'} sub={p.avg_watch_time != null ? `previous ${p.avg_watch_time}s` : null} />
        <Stat testid="reels-watch" label="Total watch time" value={r?.watch_time ? fmt(Math.round(r.watch_time / 60)) + ' min' : '—'} />
        <Stat testid="reels-skip" label="Skip rate (first 3s)" value={pct(r?.skip_rate)} />
      </div>
      <Card className="overflow-hidden">
        <div className="p-6 pb-3"><h2 className="font-semibold">Top Reels and videos</h2></div>
        <Table testid="reels-table" rowTestid="reels-row" cols={[['cap', 'Reel'], ['views', 'Views', 1], ['reach', 'Reach', 1], ['aw', 'Avg watch', 1], ['er', 'Eng.', 1]]}
          rows={(r?.top || []).map((t) => ({ cap: <span className="line-clamp-1 max-w-md">{t.caption}</span>, views: fmt(t.insights.views), reach: fmt(t.insights.reach), aw: val('avg_watch_time', t.insights.avg_watch_time), er: pct(t.insights.engagement_rate) }))}
          empty="No Reels or videos in this range." />
      </Card>
    </div>
  )
}

function BestTimes({ s, channel, channels, toast }) {
  const [count, setCount] = useState('5')
  const [busy, setBusy] = useState(false)
  const bt = s?.best_times
  const grid = bt?.heatmap?.grid || []
  const scores = grid.flat().filter(Boolean).map((c) => c.score)
  const top = Math.max(1e-9, ...scores)
  const ch = channels.find((c) => c.id === channel)
  const apply = async () => {
    setBusy(true)
    try {
      const r = await post('/api/insights/best-times/apply', { channel_id: channel, count: Number(count) })
      toast(`Posting times updated: ${r.slots.length} slots a week`)
    } catch (e) { toast(e.message, 'err') } finally { setBusy(false) }
  }
  return (
    <div className="space-y-6" data-testid="best-view">
      <Card className="p-6">
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <h2 className="font-semibold">Best time to post</h2>
          <span className="text-xs text-muted">from {fmt(bt?.heatmap?.posts)} of your sent posts · darker = higher median engagement · channel local time</span>
          <div className="ml-auto flex items-center gap-2">
            <Select value={count} onChange={(e) => setCount(e.target.value)} options={[['3', '3 a week'], ['5', '5 a week'], ['7', '7 a week'], ['10', '10 a week'], ['14', '14 a week']]} data-testid="best-count" aria-label="Slots per week" />
            <Button variant="lav" onClick={apply} disabled={!channel || !bt?.top?.length} loading={busy} data-testid="best-apply" title={channel ? '' : 'Pick one channel first'}><CalendarClock className="size-4" />Use as posting slots{ch ? ` for @${ch.username}` : ''}</Button>
          </div>
        </div>
        {!channel && <p className="mb-3 text-xs text-muted" data-testid="best-pick-channel">Pick one channel above to turn these into its posting slots.</p>}
        {!bt?.enough_data && <p className="mb-3 text-xs text-amber-700">Fewer than 10 posts with results so far, so treat this as a first hint.</p>}
        <div className="overflow-x-auto">
          <div className="grid min-w-[640px] gap-[3px]" style={{ gridTemplateColumns: '36px repeat(24, minmax(0, 1fr))' }} data-testid="best-heatmap">
            <span />{Array.from({ length: 24 }, (_, h) => <span key={h} className="text-center text-[9px] text-muted">{h % 3 === 0 ? String(h).padStart(2, '0') : ''}</span>)}
            {DAYS.map((day, di) => [<span key={day} className="text-[11px] font-medium text-muted">{day}</span>,
              ...Array.from({ length: 24 }, (_, h) => { const c = grid[di]?.[h]; return <div key={day + h} title={c ? `${day} ${String(h).padStart(2, '0')}:00 · score ${c.score} · ${c.posts} posts` : `${day} ${h}:00 · no posts`} data-testid={c ? 'best-cell' : undefined} className="h-6 rounded-[4px] border border-lav-100" style={{ background: c ? `rgba(109,70,201,${0.12 + 0.88 * (c.score / top)})` : 'transparent' }} /> })])}
          </div>
        </div>
      </Card>
      <Card className="overflow-hidden">
        <Table testid="best-top" rowTestid="best-row" cols={[['rank', '#'], ['slot', 'Time'], ['score', 'Median score', 1], ['posts', 'Posts', 1]]}
          rows={(bt?.top || []).map((t, i) => ({ rank: i + 1, slot: `${t.day} ${t.time}`, score: t.score, posts: t.posts }))} empty="No sent posts with results yet." />
      </Card>
    </div>
  )
}

function Hashtags({ s }) {
  const h = s?.hashtags || { hashtags: [], groups: [] }
  const c = s?.campaigns || { campaigns: [], tags: [] }
  const st = (x) => ({ posts: x.posts, reach: fmt(x.avg_reach), views: fmt(x.avg_views), er: pct(x.engagement_rate) })
  const cols = [['name', 'Name'], ['posts', 'Posts', 1], ['reach', 'Avg reach', 1], ['views', 'Avg views', 1], ['er', 'Eng.', 1]]
  return (
    <div className="grid gap-6 lg:grid-cols-2" data-testid="hashtags-view">
      <Card className="overflow-hidden"><div className="p-6 pb-3"><h2 className="font-semibold">Hashtags</h2><p className="text-xs text-muted">Average results of posts that used each hashtag</p></div>
        <Table testid="hashtag-table" rowTestid="hashtag-row" cols={cols} rows={h.hashtags.map((x) => ({ name: x.hashtag, ...st(x) }))} empty="No hashtags in this range." /></Card>
      <Card className="overflow-hidden"><div className="p-6 pb-3"><h2 className="font-semibold">Hashtag groups</h2><p className="text-xs text-muted">Posts with at least half a group's hashtags, vs posts without it</p></div>
        <Table testid="hashtag-group-table" cols={[...cols.slice(0, 3), ['er', 'Eng.', 1], ['vs', 'Reach vs others', 1]]} rows={h.groups.map((g) => ({ name: g.name, ...st(g), vs: g.vs_others_reach_pct == null ? '—' : (g.vs_others_reach_pct >= 0 ? '+' : '') + g.vs_others_reach_pct + '%' }))} empty="Save hashtag groups in the Library to compare them." /></Card>
      <Card className="overflow-hidden"><div className="p-6 pb-3"><h2 className="font-semibold">Campaigns</h2></div>
        <Table testid="campaign-table" rowTestid="campaign-row" cols={[['name', 'Campaign'], ['posts', 'Posts', 1], ['treach', 'Reach', 1], ['tviews', 'Views', 1], ['er', 'Eng.', 1]]}
          rows={c.campaigns.map((x) => ({ name: x.campaign, posts: x.posts, treach: fmt(x.reach), tviews: fmt(x.views), er: pct(x.engagement_rate) }))} empty="No campaign posts in this range." /></Card>
      <Card className="overflow-hidden"><div className="p-6 pb-3"><h2 className="font-semibold">Tags</h2></div>
        <Table testid="tag-table" cols={[['name', 'Tag'], ['posts', 'Posts', 1], ['treach', 'Reach', 1], ['tviews', 'Views', 1], ['er', 'Eng.', 1]]}
          rows={c.tags.map((x) => ({ name: x.tag, posts: x.posts, treach: fmt(x.reach), tviews: fmt(x.views), er: pct(x.engagement_rate) }))} empty="No tagged posts in this range." /></Card>
    </div>
  )
}

function Report({ fparams, query, toast }) {
  const [secs, setSecs] = useState(SECTIONS.map(([k]) => k))
  const [emails, setEmails] = useState('')
  const [busy, setBusy] = useState(false)
  const [sch, setSch] = useState(null)
  const [archive, setArchive] = useState([])
  const loadArchive = () => get('/api/reports/archive').then((r) => setArchive(r.reports || []))
  useEffect(() => { get('/api/reports/schedule').then(setSch); loadArchive() }, [])
  const qs = useMemo(() => { const p = new URLSearchParams(fparams); if (secs.length && secs.length < SECTIONS.length) p.set('sections', secs.join(',')); return p.toString() }, [fparams, secs])
  const toggle = (k) => setSecs((x) => (x.includes(k) ? x.filter((y) => y !== k) : SECTIONS.map(([z]) => z).filter((z) => x.includes(z) || z === k)))
  const generate = async () => {
    setBusy(true)
    try {
      const body = Object.fromEntries(new URLSearchParams(qs))
      const r = await post('/api/reports/send', { ...body, email_to: emails })
      toast(r.emailed.length ? `Report emailed to ${r.emailed.join(', ')}` : r.email_error ? `Saved to the archive (email failed: ${r.email_error})` : 'Report saved to the archive', r.email_error ? 'err' : 'ok')
      loadArchive()
    } catch (e) { toast(e.message, 'err') } finally { setBusy(false) }
  }
  const saveSchedule = async () => {
    try {
      const r = await put('/api/reports/schedule', { enabled: sch.enabled, weekday: Number(sch.weekday), time: sch.time, days: sch.days, email_to: Array.isArray(sch.email_to) ? sch.email_to : sch.email_to,
        channel_id: query.channel || '', format: query.format || '', campaign: query.campaign || '', tag: query.tag || '', sections: secs.length < SECTIONS.length ? secs : [] })
      setSch(r); toast(r.enabled ? `Weekly report on — next ${new Date(r.next_run_at).toLocaleString()}` : 'Weekly report off')
    } catch (e) { toast(e.message, 'err') }
  }
  return (
    <div className="grid gap-6 lg:grid-cols-3" data-testid="report-view">
      <Card className="space-y-4 p-6 lg:col-span-2">
        <div className="flex items-center gap-2"><FileText className="size-5 text-lav-600" /><h2 className="font-semibold">Custom report</h2><span className="text-xs text-muted">uses the range, channel and filters above · compared with the previous period</span></div>
        <div className="grid gap-2 sm:grid-cols-3" data-testid="report-sections">
          {SECTIONS.map(([k, l]) => (
            <label key={k} className="flex cursor-pointer items-center gap-2 rounded-xl border border-lav-200 px-3 py-2 text-sm hover:bg-lav-50">
              <input type="checkbox" checked={secs.includes(k)} onChange={() => toggle(k)} data-testid={`report-section-${k}`} className="accent-lav-600" />{l}
            </label>
          ))}
        </div>
        <div className="flex flex-wrap gap-2">
          <a href={`/api/reports/report.pdf?${qs}`} data-testid="report-download-pdf" download><Button data-testid="insights-download-pdf" variant="lav" disabled={!secs.length}><Download className="size-4" />Download PDF</Button></a>
          <a href={`/api/reports/report.pdf?${qs}&download=false`} target="_blank" rel="noreferrer" data-testid="report-preview-pdf"><Button data-testid="insights-preview" variant="outline">Preview</Button></a>
          <a href={`/api/reports/report.csv?${qs}`} data-testid="report-download-csv" download><Button data-testid="insights-download-csv" variant="outline"><Download className="size-4" />Download CSV</Button></a>
        </div>
        <div className="flex flex-wrap items-end gap-2 border-t border-lav-100 pt-4">
          <Field label="Email it now (optional)" className="min-w-64 flex-1"><Input value={emails} onChange={(e) => setEmails(e.target.value)} placeholder="you@example.com, team@example.com" data-testid="report-email" /></Field>
          <Button variant="primary" onClick={generate} loading={busy} data-testid="report-generate"><Send className="size-4" />Generate now</Button>
        </div>
      </Card>
      <Card className="space-y-3 p-6" data-testid="report-schedule">
        <div className="flex items-center gap-2"><CalendarClock className="size-5 text-lav-600" /><h2 className="font-semibold">Weekly report</h2>
          {sch && <span className="ml-auto" /> }{sch && <Switch checked={!!sch.enabled} onChange={(v) => setSch({ ...sch, enabled: v })} label="Weekly report" testid="report-schedule-enabled" />}</div>
        {sch && <>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Day"><Select value={String(sch.weekday)} onChange={(e) => setSch({ ...sch, weekday: e.target.value })} options={DAYS.map((x, i) => [String(i), x])} data-testid="report-schedule-day" className="w-full" /></Field>
            <Field label="Time"><Input type="time" className="w-full" value={sch.time} onChange={(e) => setSch({ ...sch, time: e.target.value })} data-testid="report-schedule-time" /></Field>
          </div>
          <Field label="Covers"><Select value={sch.days} onChange={(e) => setSch({ ...sch, days: e.target.value })} options={[['7', 'Last 7 days'], ['14', 'Last 14 days'], ['28', 'Last 28 days'], ['30', 'Last 30 days'], ['mtd', 'Month to date']]} data-testid="report-schedule-days" className="w-full" /></Field>
          <Field label="Email to" hint={sch.email_configured ? `Sent from your SMTP server · ${sch.timezone}` : 'No SMTP in .env yet: reports are kept in the archive below.'}>
            <Input value={Array.isArray(sch.email_to) ? sch.email_to.join(', ') : sch.email_to} onChange={(e) => setSch({ ...sch, email_to: e.target.value })} placeholder="you@example.com" data-testid="report-schedule-email" /></Field>
          <Button variant="lav" className="w-full" onClick={saveSchedule} data-testid="report-schedule-save">Save schedule</Button>
          {sch.enabled && sch.next_run_at && <p className="text-xs text-muted" data-testid="report-schedule-next">Next: {new Date(sch.next_run_at).toLocaleString()}{sch.last_status ? ` · last: ${sch.last_status}` : ''}</p>}
          <p className="text-xs text-muted">Uses the sections and filters chosen here when you save.</p>
        </>}
      </Card>
      <Card className="overflow-hidden lg:col-span-3">
        <div className="flex items-center p-6 pb-3"><h2 className="font-semibold">Archive</h2><Wand2 className="ml-2 size-4 text-lav-500" /></div>
        <ul className="divide-y divide-lav-100" data-testid="report-archive">
          {archive.map((r) => (
            <li key={r.name} className="flex items-center gap-3 px-6 py-2.5 text-sm" data-testid="report-archive-row">
              <FileText className="size-4 text-lav-600" /><a data-testid="insights-a" className="hover:underline" href={r.url} target="_blank" rel="noreferrer">{r.name}</a>
              <span className="ml-auto text-xs text-muted">{new Date(r.created_at).toLocaleString()} · {Math.round(r.size / 1024)} KB</span>
              <Button size="icon-sm" variant="danger-ghost" aria-label="Delete report" data-testid="report-archive-delete" onClick={async () => { await del(r.url); loadArchive() }}><Trash2 className="size-4" /></Button>
            </li>
          ))}
          {!archive.length && <li className="px-6 py-6 text-center text-sm text-muted">Generated and weekly reports appear here.</li>}
        </ul>
      </Card>
    </div>
  )
}
