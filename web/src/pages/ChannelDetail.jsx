import { motion } from 'motion/react'
import { CalendarPlus, Plug, RefreshCw, X, PartyPopper } from 'lucide-react'
import { useEffect, useState } from 'react'
import { get, post } from '../api'
import { go, setQuery, useRoute } from '../router'
import { useStore } from '../store'
import { HistoryTab } from './ChannelTabs'
import { PublishTab } from './ChannelPublish'
import { CommunityTab } from './ChannelCommunity'
import { InsightsTab } from './ChannelInsights'
import { ScheduleTab, SettingsTab } from './ChannelSettings'
import { Badge, Button, Card, ChannelAvatar, fmt, NETWORKS, Tabs, useToast } from '../ui'


export default function ChannelDetail() {
  const { parts, query } = useRoute()
  const { reload, connector } = useStore()
  const toast = useToast()
  const id = parts[1]
  const [ch, setCh] = useState(null)
  const tab = query.tab || 'publish'
  const load = () => get(`/api/channels/${id}`).then(setCh).catch((e) => toast(e.message, 'err'))
  useEffect(() => { if (!id || id === 'undefined') { go('/channels'); return } load() }, [id])
  if (!ch) return <div className="space-y-4" data-testid="channel-loading"><div className="skeleton h-40 w-full rounded-2xl" /><div className="skeleton h-10 w-96" /><div className="skeleton h-64 w-full rounded-2xl" /></div>
  const net = NETWORKS[ch.provider]?.name

  return (
    <div className="space-y-6" data-testid="page-channeldetail">
      {query.connected === '1' && (
        <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} className="flex items-center gap-3 rounded-2xl bg-emerald-50 p-4 text-sm text-emerald-900" data-testid="connected-banner">
          <PartyPopper className="size-5" /><span><b>@{ch.username}</b> is connected. Pick a weekly posting goal and we'll set your recommended posting times.</span>
          <Button data-testid="channel-detail-setquery" size="sm" variant="ghost" className="ml-auto" onClick={() => setQuery({ connected: '' })}><X className="size-4" /></Button>
        </motion.div>
      )}
      <Card className="overflow-hidden">
        <div className="bg-gradient-to-r from-lav-200 via-lav-100 to-surface p-6">
          <div className="flex flex-wrap items-center gap-5">
            <ChannelAvatar ch={ch} size={84} />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2"><h1 className="text-2xl font-semibold">@{ch.username}</h1><Badge tone={ch.status === 'active' ? 'green' : 'red'} data-testid="channel-status">{ch.status === 'active' ? 'Connected' : 'Needs reconnect'}</Badge>{ch.paused && <Badge tone="amber">Queue paused</Badge>}</div>
              <p className="text-ink-2">{ch.display_name} · {net} · {ch.account_type || 'account'}</p>
              {ch.status_detail && <p className="mt-1 text-sm text-rose-700">{ch.status_detail}</p>}
            </div>
            <div className="grid grid-cols-3 gap-6 text-center" data-testid="channel-profile-stats">
              <div><div className="text-2xl font-semibold tabular-nums">{fmt(ch.followers_count)}</div><div className="text-xs text-muted">followers</div></div>
              <div><div className="text-2xl font-semibold tabular-nums">{fmt(ch.media_count)}</div><div className="text-xs text-muted">posts</div></div>
              <div><div className="text-2xl font-semibold tabular-nums">{ch.counts?.scheduled || 0}</div><div className="text-xs text-muted">queued</div></div>
            </div>
          </div>
        </div>
        <div className="flex flex-wrap gap-2 border-t border-lav-100 p-4">
          <Button variant="primary" onClick={() => go(`/channel/${id}`, { compose: 'new', channels: id })} data-testid="channel-new-post"><CalendarPlus className="size-4" />New post</Button>
          <Button variant="outline" onClick={() => go(`/queue/${id}`)} data-testid="channel-queue">Open queue</Button>
          <Button variant="outline" onClick={async () => { try { setCh(await post(`/api/channels/${id}/refresh-profile`)); toast('Profile refreshed') } catch (e) { toast(e.message, 'err') } }} data-testid="channel-refresh-profile"><RefreshCw className="size-4" />Refresh profile</Button>
          <Button variant="ghost" onClick={() => go('/channels', { network: ch.provider })} data-testid="channel-reconnect"><Plug className="size-4" />Reconnect</Button>
        </div>
      </Card>

      <Tabs testid="channel-tabs" value={tab} onChange={(v) => setQuery({ tab: v })} items={[['publish', 'Publish', ch.counts?.scheduled || 0], ['community', 'Community'], ['insights', 'Insights'], ['history', 'Sent / History'], ['schedule', 'Posting schedule'], ['overview', 'Settings']]} />

      {tab === 'publish' ? <PublishTab ch={ch} /> : tab === 'history' ? <HistoryTab ch={ch} />
        : tab === 'community' ? <CommunityTab ch={ch} connector={connector(ch.provider)} />
        : tab === 'insights' ? <InsightsTab ch={ch} connector={connector(ch.provider)} />
        : tab === 'schedule' ? <ScheduleTab ch={ch} setCh={setCh} />
        : <SettingsTab ch={ch} setCh={setCh} net={net} />}
    </div>
  )
}
