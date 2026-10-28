// Analytics tab: views, clicks, CTR and signups per day, clicks per link, devices and sources (bio.get).
import { motion } from 'motion/react'
import { RefreshCw } from 'lucide-react'
import { Button, cx, fmt, SkeletonList, Stat } from '../../ui'
import { Panel } from './shared'

function Bars({ data, keys, testid, height = 120 }) {
  const max = Math.max(1, ...data.flatMap((d) => keys.map(([k]) => d[k] || 0)))
  return (
    <div className="flex items-end gap-[3px]" style={{ height }} data-testid={testid} aria-label="Daily chart">
      {data.map((d) => (
        <div key={d.day} className="relative flex h-full flex-1 items-end gap-px" title={`${d.day}: ${keys.map(([k]) => `${d[k] || 0} ${k}`).join(', ')}`}>
          {keys.map(([k, color]) => (
            <motion.div key={k} initial={{ height: 0 }} animate={{ height: `${((d[k] || 0) / max) * 100}%` }} transition={{ duration: 0.4 }}
              className={cx('min-h-[2px] flex-1 rounded-t', color, !(d[k] > 0) && 'opacity-25')} />
          ))}
        </div>
      ))}
    </div>
  )
}

export function AnalyticsTab({ stats, reload }) {
  return (
    <Panel testid="bio-analytics" title="Last 30 days"
      action={<Button size="sm" variant="ghost" onClick={reload} data-testid="bio-refresh-stats"><RefreshCw className="size-3.5" />Refresh</Button>}>
      {!stats ? <SkeletonList rows={1} /> : (
        <div className="space-y-5">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Stat label="Views" value={fmt(stats.views)} testid="bio-stat-views" />
            <Stat label="Clicks" value={fmt(stats.clicks)} testid="bio-stat-clicks" />
            <Stat label="CTR" value={`${stats.ctr}%`} testid="bio-stat-ctr" />
            <Stat label="Signups" value={fmt(stats.signups || 0)} testid="bio-stat-signups" />
          </div>
          <div>
            <Bars data={stats.daily} keys={[['views', 'bg-lav-300'], ['clicks', 'bg-lav-600'], ['signups', 'bg-emerald-500']]} testid="bio-chart" />
            <div className="mt-2 flex gap-3 text-[11px] text-muted">
              <span className="flex items-center gap-1"><i className="size-2 rounded-sm bg-lav-300" />views</span>
              <span className="flex items-center gap-1"><i className="size-2 rounded-sm bg-lav-600" />clicks</span>
              <span className="flex items-center gap-1"><i className="size-2 rounded-sm bg-emerald-500" />signups</span>
            </div>
          </div>
          {stats.blocks.length > 0 && (
            <div>
              <h4 className="mb-2 text-sm font-semibold">Clicks per link</h4>
              <ul className="space-y-2 text-sm" data-testid="bio-top-links">
                {stats.blocks.map((b) => (
                  <li key={b.id} className="flex items-center gap-3"><span className="min-w-0 flex-1 truncate">{b.label}</span>
                    <span className="h-2 w-32 overflow-hidden rounded-full bg-lav-100"><span className="block h-full rounded-full bg-lav-500" style={{ width: `${stats.clicks ? (100 * b.clicks_in_range) / stats.clicks : 0}%` }} /></span>
                    <span className="w-10 text-right tabular-nums text-muted">{b.clicks}</span></li>
                ))}
              </ul>
            </div>
          )}
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-2xl bg-lav-50 p-3 text-xs" data-testid="bio-devices"><div className="mb-1 font-semibold">Devices</div>{stats.devices?.length ? stats.devices.map((d) => `${d.device} ${d.clicks}`).join(' · ') : 'No clicks yet'}</div>
            <div className="rounded-2xl bg-lav-50 p-3 text-xs"><div className="mb-1 font-semibold">Top sources</div>{stats.referrers.length ? stats.referrers.slice(0, 5).map((r) => `${r.referrer} (${r.clicks})`).join(' · ') : 'No clicks yet'}</div>
          </div>
        </div>
      )}
    </Panel>
  )
}
