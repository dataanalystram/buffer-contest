// Channel page "Insights": 7d / 30d / month to date / custom (free), this vs previous period per metric, a daily
// comparison chart, plain-language takeaways, posts table, CSV export (posts / daily) and "Share as post".
import { ArrowDownRight, ArrowUpRight, Download, ExternalLink, Lightbulb, Share2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { get, post } from '../api'
import { go } from '../router'
import { Badge, Button, Card, cx, Empty, fmt, Input, Select, SkeletonList, Stat, useToast } from '../ui'

const RANGES = [['7d', 'Last 7 days'], ['30d', 'Last 30 days'], ['mtd', 'Month to date'], ['custom', 'Custom range']]

function qs(range, start, end) {
  return range === 'custom' ? `range=custom&start=${start}&end=${end}` : `range=${range}`
}

function Delta({ v, testid }) {
  if (v === null || v === undefined) return <span className="text-xs text-muted" data-testid={testid}>—</span>
  const up = v >= 0
  const I = up ? ArrowUpRight : ArrowDownRight
  return <span className={cx('inline-flex items-center text-xs font-medium', up ? 'text-emerald-700' : 'text-rose-700')} data-testid={testid}><I className="size-3.5" />{Math.abs(v)}%</span>
}

export function InsightsTab({ ch, connector }) {
  const toast = useToast()
  const today = new Date().toLocaleDateString('sv-SE')
  const monthAgo = new Date(Date.now() - 29 * 864e5).toLocaleDateString('sv-SE')
  const [range, setRange] = useState('30d')
  const [start, setStart] = useState(monthAgo)
  const [end, setEnd] = useState(today)
  const [metric, setMetric] = useState('views')
  const [d, setD] = useState(null)
  const [acct, setAcct] = useState(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (range === 'custom' && (!start || !end)) return
    setD(null)
    get(`/api/channels/${ch.id}/insights/summary?${qs(range, start, end)}`).then(setD).catch((e) => { toast(e.message, 'err'); setD({ error: e.message }) })
  }, [ch.id, range, start, end])
  useEffect(() => {
    if (connector && !connector.account_insights) return
    get(`/api/channels/${ch.id}/insights?days=${range === '7d' ? 7 : 28}`).then(setAcct).catch(() => setAcct(null))
  }, [ch.id, range])
  const share = async () => {
    setBusy(true)
    try {
      const r = await post(`/api/channels/${ch.id}/insights/share`, range === 'custom' ? { range, start, end } : { range })
      go(`/channel/${ch.id}`, { tab: 'publish', compose: 'new', channels: ch.id, text: r.caption })
    } catch (e) { toast(e.message, 'err') } finally { setBusy(false) }
  }
  const rows = d?.comparison || []
  const max = Math.max(1, ...rows.map((r) => Math.max(r[metric] || 0, r[`previous_${metric}`] || 0)))
  const acctTotals = acct?.totals || {}
  return (
    <div className="space-y-4" data-testid="channel-insights">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={range} onChange={(e) => setRange(e.target.value)} options={RANGES} aria-label="Range" data-testid="channel-insights-range" />
        {range === 'custom' && <>
          <Input type="date" className="w-40" value={start} max={end} onChange={(e) => setStart(e.target.value)} aria-label="Start date" data-testid="channel-insights-start" />
          <span className="text-muted">→</span>
          <Input type="date" className="w-40" value={end} min={start} onChange={(e) => setEnd(e.target.value)} aria-label="End date" data-testid="channel-insights-end" />
        </>}
        <span className="text-sm text-muted" data-testid="channel-insights-period">{d?.period ? `${d.period.label} · vs ${d.previous_period.label}` : ''}</span>
        <div className="ml-auto flex flex-wrap gap-2">
          <a className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-lav-200 px-3 text-[13px] text-ink-2 hover:bg-lav-100" data-testid="channel-insights-csv"
            href={`/api/channels/${ch.id}/insights/export.csv?${qs(range, start, end)}&kind=posts`} download><Download className="size-3.5" />Posts CSV</a>
          <a className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-lav-200 px-3 text-[13px] text-ink-2 hover:bg-lav-100" data-testid="channel-insights-csv-daily"
            href={`/api/channels/${ch.id}/insights/export.csv?${qs(range, start, end)}&kind=daily`} download><Download className="size-3.5" />Daily CSV</a>
          <Button size="sm" variant="primary" loading={busy} onClick={share} disabled={!d || d.error} data-testid="channel-insights-share"><Share2 className="size-3.5" />Share as post</Button>
        </div>
      </div>
      {d === null ? <SkeletonList rows={2} testid="channel-insights-loading" /> : d.error ? <Empty title="Couldn’t load insights" testid="channel-insights-error">{d.error}</Empty> : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4" data-testid="channel-insights-summary">
            {d.summary.map((m) => (
              <div key={m.key} data-testid={`summary-${m.key}`} className="rounded-2xl border border-lav-200/70 bg-surface p-4 shadow-soft">
                <div className="text-xs font-medium uppercase tracking-wider text-muted">{m.label}</div>
                <div className="mt-1 flex items-baseline gap-2"><span className="text-2xl font-semibold tabular-nums">{m.key === 'engagement_rate' ? `${m.value ?? 0}%` : fmt(m.value)}</span><Delta v={m.delta} testid={`delta-${m.key}`} /></div>
                {m.previous !== null && m.previous !== undefined && <div className="text-xs text-muted">previous: {m.key === 'engagement_rate' ? `${m.previous}%` : fmt(m.previous)}</div>}
              </div>
            ))}
          </div>
          {d.takeaways.length > 0 && (
            <Card className="space-y-2 p-5" data-testid="channel-takeaways">
              <h3 className="flex items-center gap-2 text-sm font-semibold"><Lightbulb className="size-4 text-lav-600" />Takeaways</h3>
              <ul className="space-y-1.5">
                {d.takeaways.map((t, i) => <li key={i} data-testid="takeaway" className={cx('rounded-xl px-3 py-2 text-sm', t.tone === 'good' ? 'bg-emerald-50 text-emerald-900' : t.tone === 'warn' ? 'bg-amber-50 text-amber-900' : 'bg-lav-50 text-ink-2')}>{t.text}</li>)}
              </ul>
            </Card>
          )}
          <Card className="p-5" data-testid="channel-comparison">
            <div className="mb-3 flex flex-wrap items-center gap-3">
              <h3 className="text-sm font-semibold">Daily: this period vs previous</h3>
              <span className="flex items-center gap-1 text-xs text-muted"><span className="size-2.5 rounded-sm bg-lav-500" />this</span>
              <span className="flex items-center gap-1 text-xs text-muted"><span className="size-2.5 rounded-sm bg-lav-200" />previous</span>
              <Select className="ml-auto h-9" value={metric} onChange={(e) => setMetric(e.target.value)} aria-label="Metric" data-testid="channel-comparison-metric"
                options={(d.metrics || []).filter((m) => m.key !== 'engagement_rate').map((m) => [m.key, m.label])} />
            </div>
            <div className="flex h-36 items-end gap-0.5" data-testid="channel-comparison-chart">
              {rows.map((r) => (
                <div key={r.day} className="flex h-full flex-1 items-end gap-px" title={`${r.day}: ${r[metric] || 0} · previous ${r[`previous_${metric}`] ?? 0}`}>
                  <div className="flex-1 rounded-t bg-lav-200" style={{ height: `${Math.max(2, (100 * (r[`previous_${metric}`] || 0)) / max)}%` }} />
                  <div className="flex-1 rounded-t bg-lav-500" style={{ height: `${Math.max(2, (100 * (r[metric] || 0)) / max)}%` }} />
                </div>
              ))}
            </div>
          </Card>
          {Object.keys(acctTotals).length > 0 && (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4" data-testid="channel-insights-kpis">
              {Object.entries(acctTotals).slice(0, 8).map(([k, v]) => <Stat key={k} label={k.replace(/_/g, ' ')} value={fmt(v)} sub={`account · ${acct.since} → ${acct.until}`} testid={`kpi-${k}`} />)}
            </div>
          )}
          <Card className="overflow-x-auto p-0" data-testid="channel-insights-posts">
            {!d.posts.this.length ? <p className="p-5 text-sm text-muted">No posts sent from HuhQueue in this period.</p> : (
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-muted"><tr><th className="p-3">Post</th><th>Format</th><th>Views</th><th>Reactions</th><th>Comments</th><th>Eng. rate</th><th /></tr></thead>
                <tbody>
                  {d.posts.this.map((p) => (
                    <tr key={p.id} className="border-t border-lav-100" data-testid="insights-post-row">
                      <td className="max-w-80 truncate p-3">{p.caption || '—'}</td><td><Badge tone="gray">{p.format}</Badge></td>
                      <td className="tabular-nums">{fmt(p.metrics.views)}</td><td className="tabular-nums">{fmt(p.metrics.reactions)}</td>
                      <td className="tabular-nums">{fmt(p.metrics.comments)}</td><td className="tabular-nums">{p.metrics.engagement_rate ?? '—'}{p.metrics.engagement_rate !== null && p.metrics.engagement_rate !== undefined ? '%' : ''}</td>
                      <td className="pr-3">{p.permalink && <a href={p.permalink} target="_blank" rel="noreferrer" aria-label="Open post"><ExternalLink className="size-3.5 text-lav-700" /></a>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
        </>
      )}
    </div>
  )
}
