import { motion } from 'motion/react'
import { AlertTriangle, CalendarClock, Flame, Globe, MessageCircle, MessagesSquare, PenSquare, PlugZap, Rocket, Send, Target, Zap } from 'lucide-react'
import { get, post } from '../api'
import { go, href } from '../router'
import { useStore } from '../store'
import { Badge, Button, Card, ChannelAvatar, Empty, fmt, Skeleton, SpotlightCard, Stat, useAsync, useToast } from '../ui'

function greeting() {
  const h = new Date().getHours()
  return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'
}

function Bars({ values = [], testid }) {
  const max = Math.max(1, ...values)
  return (
    <div className="flex h-10 items-end gap-1" data-testid={testid} aria-hidden="true">
      {values.map((v, i) => (
        <motion.span key={i} initial={{ height: 0 }} animate={{ height: `${Math.max(8, (100 * v) / max)}%` }} transition={{ delay: i * 0.04, type: 'spring', stiffness: 200, damping: 20 }}
          className={`w-2.5 rounded-sm ${v ? (i === values.length - 1 ? 'bg-lav-600' : 'bg-lav-400') : 'bg-lav-200'}`} />
      ))}
    </div>
  )
}

function Ring({ pct = 0, size = 56 }) {
  const r = size / 2 - 5, c = 2 * Math.PI * r
  return (
    <svg width={size} height={size} className="-rotate-90" aria-hidden="true">
      <circle cx={size / 2} cy={size / 2} r={r} strokeWidth="6" className="fill-none stroke-lav-200" />
      <motion.circle cx={size / 2} cy={size / 2} r={r} strokeWidth="6" strokeLinecap="round" className="fill-none stroke-lav-600"
        strokeDasharray={c} initial={{ strokeDashoffset: c }} animate={{ strokeDashoffset: c - (c * pct) / 100 }} transition={{ duration: 0.9 }} />
    </svg>
  )
}

const ago = (iso) => {
  if (!iso) return ''
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  return m < 60 ? `${Math.max(1, m)}m ago` : m < 1440 ? `${Math.round(m / 60)}h ago` : `${Math.round(m / 1440)}d ago`
}

export default function Home() {
  const { channels } = useStore()
  const toast = useToast()
  const { data, reload } = useAsync(() => get('/api/home'), [])
  const d = data || { upcoming: [], attention: [], counts: {}, recent_comments: [], weekly_sent: [] }
  const pct = d.weekly_goal ? Math.min(100, Math.round((100 * d.sent_this_week) / d.weekly_goal)) : 0
  const chById = Object.fromEntries(channels.map((c) => [c.id, c]))
  const publishNow = async (t) => {
    try { await post(`/api/targets/${t.id}/publish-now`); toast('Publishing…'); reload() } catch (e) { toast(e.message, 'err') }
  }
  return (
    <div className="space-y-6" data-testid="page-home">
      <div className="flex flex-wrap items-end gap-4">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-lav-700">{new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })}</p>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight">{greeting()}, Ram.</h1>
          <p className="mt-1 text-ink-2">{data ? (d.upcoming.length ? `${d.counts.scheduled || 0} posts queued across ${channels.length} channel${channels.length === 1 ? '' : 's'}.` : 'Your queue is empty. Let’s line up today’s AI news.') : '\u00a0'}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="ghost" data-testid="home-connect" onClick={() => go('/channels')}><PlugZap className="size-4" />Connect channel</Button>
          <Button variant="outline" data-testid="home-post-all" onClick={() => go('/home', { compose: 'new', channels: 'all' })}><Zap className="size-4" />Post to all channels</Button>
          <Button variant="primary" data-testid="home-create" onClick={() => go('/home', { compose: 'new' })}><PenSquare className="size-4" />Create post</Button>
        </div>
      </div>

      {data && !d.public_base_url && channels.length > 0 && (
        <Card className="flex items-center gap-4 border-amber-200 bg-amber-50 p-4" data-testid="home-tunnel-banner">
          <Globe className="size-5 text-amber-600" />
          <p className="flex-1 text-sm text-amber-900">Instagram and TikTok photo posts fetch media from a public URL. Set one before posts go out.</p>
          <Button data-testid="home-set-up-public-url" size="sm" variant="primary" onClick={() => go('/settings', { section: 'public-url' })}>Set up public URL</Button>
        </Card>
      )}

      <section aria-labelledby="scorecard-h">
        <h2 id="scorecard-h" className="mb-3 text-sm font-semibold text-ink-2">Your scorecard</h2>
        <div className="grid gap-4 md:grid-cols-3" data-testid="home-scorecard">
          <SpotlightCard className="p-5" data-testid="score-streak">
            <div className="flex items-start justify-between">
              <div>
                <div className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-muted"><Flame className="size-3.5 text-orange-500" />Week streak</div>
                {data ? <div className="mt-1 text-3xl font-semibold tabular-nums">{d.week_streak ?? 0}<span className="ml-1 text-sm font-normal text-muted">week{d.week_streak === 1 ? '' : 's'}</span></div> : <Skeleton className="mt-2 h-8 w-20" />}
              </div>
              <Bars values={d.weekly_sent} testid="score-streak-bars" />
            </div>
            <p className="mt-3 text-xs text-muted">{d.week_streak ? 'Weeks in a row with at least one post. Keep it going.' : 'Post this week to start a streak.'}</p>
          </SpotlightCard>
          <SpotlightCard className="p-5" data-testid="score-goal">
            <div className="flex items-start justify-between">
              <div>
                <div className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-muted"><Target className="size-3.5 text-lav-600" />Posting goal</div>
                {data ? <div className="mt-1 text-3xl font-semibold tabular-nums">{d.sent_this_week ?? 0}<span className="text-base font-normal text-muted"> / {d.weekly_goal || 0}</span></div> : <Skeleton className="mt-2 h-8 w-24" />}
              </div>
              <div className="relative grid place-items-center"><Ring pct={pct} /><span className="absolute text-[11px] font-semibold tabular-nums">{pct}%</span></div>
            </div>
            <p className="mt-3 text-xs text-muted">Posts sent in the last 7 days against your daily limits × 7.</p>
          </SpotlightCard>
          <SpotlightCard className="p-5" data-testid="score-comments">
            <div className="flex items-start justify-between">
              <div>
                <div className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-muted"><MessageCircle className="size-3.5 text-sky-500" />Comments to answer</div>
                {data ? <div className="mt-1 text-3xl font-semibold tabular-nums">{d.unanswered_comments || 0}</div> : <Skeleton className="mt-2 h-8 w-12" />}
              </div>
              <div className="text-right"><div className="text-xs text-muted">Comment score</div><div className="text-lg font-semibold tabular-nums" data-testid="score-comment-pct">{d.comment_score === null || d.comment_score === undefined ? '—' : `${d.comment_score}%`}</div></div>
            </div>
            <Button size="sm" variant="soft" className="mt-3" onClick={() => go('/community', { status: 'open' })} data-testid="score-open-inbox">Open inbox</Button>
          </SpotlightCard>
        </div>
      </section>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat testid="stat-scheduled" label="Scheduled" value={fmt(d.counts.scheduled || 0)} sub="in the queue" />
        <Stat testid="stat-drafts" label="Drafts" value={fmt(d.counts.draft || 0)} sub="waiting for you" />
        <Stat testid="stat-sent" label="Sent" value={fmt(d.counts.sent || 0)} sub="all time" />
        <Stat testid="stat-comments" label="Unanswered" value={fmt(d.unanswered_comments || 0)} sub="comments" />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="p-6 lg:col-span-2">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="flex items-center gap-2 font-semibold"><CalendarClock className="size-4 text-lav-600" />Up next</h2>
            <a data-testid="home-view-queue" href="#/queue" className="text-sm font-medium text-lav-700 hover:underline">View queue</a>
          </div>
          {!data ? <div className="space-y-3">{[0, 1, 2].map((i) => <div key={i} className="flex items-center gap-3"><Skeleton className="size-9 rounded-full" /><div className="flex-1 space-y-2"><Skeleton className="h-3 w-3/4" /><Skeleton className="h-3 w-1/3" /></div></div>)}</div>
            : d.upcoming.length ? (
              <ul className="divide-y divide-lav-100" data-testid="home-upnext">
                {d.upcoming.map((t, i) => (
                  <motion.li key={t.id} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.04 }} className="group flex items-center gap-4 py-3">
                    <ChannelAvatar ch={t.channel} size={36} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm">{t.caption || <em className="text-muted">No caption</em>}</p>
                      <p className="text-xs text-muted">{t.scheduled_local} · {t.channel.timezone}</p>
                    </div>
                    {t.media[0] && (t.media[0].kind === 'video'
                      ? <video src={t.media[0].url + '#t=0.5'} muted preload="metadata" className="size-11 rounded-lg object-cover" />
                      : <img src={t.media[0].url} alt="" className="size-11 rounded-lg object-cover" />)}
                    <Button size="sm" variant="ghost" className="opacity-60 group-hover:opacity-100" onClick={() => publishNow(t)} data-testid="home-publish-now"><Rocket className="size-3.5" />Now</Button>
                    <Button size="sm" variant="outline" onClick={() => go('/home', { compose: t.id })} data-testid="home-edit">Edit</Button>
                  </motion.li>
                ))}
              </ul>
            ) : <Empty icon={Send} title="Nothing queued yet" action={<Button data-testid="home-create-your-first-post" variant="primary" onClick={() => go('/home', { compose: 'new' })}>Create your first post</Button>}>Write once, pick Instagram, TikTok, YouTube and X, and HuhQueue drops it into each channel’s next posting slot.</Empty>}
        </Card>
        <div className="space-y-6">
          <Card className="p-6" data-testid="home-comments">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="flex items-center gap-2 font-semibold"><MessagesSquare className="size-4 text-lav-600" />Recent comments</h2>
              <a data-testid="home-inbox" href="#/community?status=open" className="text-sm font-medium text-lav-700 hover:underline">Inbox</a>
            </div>
            {d.recent_comments?.length ? (
              <ul className="space-y-2">
                {d.recent_comments.map((c) => (
                  <li key={c.id}>
                    <a href={href('/community', { status: 'open', comment: c.id })} className="flex gap-3 rounded-xl p-2 hover:bg-lav-50" data-testid="home-comment">
                      {chById[c.channel_id] ? <ChannelAvatar ch={chById[c.channel_id]} size={28} /> : <span className="size-7" />}
                      <span className="min-w-0 flex-1 text-sm"><b>@{c.author}</b> <span className="text-xs text-muted">{ago(c.at)}</span><span className="block truncate text-ink-2">{c.text}</span></span>
                    </a>
                  </li>
                ))}
              </ul>
            ) : <p className="text-sm text-muted">No comments waiting. Inbox zero.</p>}
          </Card>
          <Card className="p-6">
            <h2 className="mb-3 flex items-center gap-2 font-semibold"><AlertTriangle className="size-4 text-amber-500" />Needs attention</h2>
            {d.attention?.length ? d.attention.map((t) => (
              <a data-testid="home-ready-for-you-to" key={t.id} href={href('/queue', { tab: t.status === 'failed' ? 'failed' : 'action', post: t.id })} className="mb-2 flex items-center gap-3 rounded-xl bg-lav-50 p-3 text-sm hover:bg-lav-100">
                <ChannelAvatar ch={t.channel} size={28} />
                <span className="min-w-0 flex-1 truncate">{t.last_error || 'Ready for you to post'}</span>
                <Badge tone={t.status === 'failed' ? 'red' : 'amber'}>{t.status === 'failed' ? 'Failed' : 'Your turn'}</Badge>
              </a>
            )) : <p className="text-sm text-muted">No failed posts.</p>}
          </Card>
        </div>
      </div>

      <Card className="p-6">
        <h2 className="mb-4 font-semibold">Channels</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {channels.map((c) => (
            <a key={c.id} href={`#/channel/${c.id}`} className="flex items-center gap-3 rounded-2xl border border-lav-100 p-4 transition hover:-translate-y-0.5 hover:border-lav-300 hover:shadow-soft" data-testid="home-channel">
              <ChannelAvatar ch={c} size={44} />
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">@{c.username}</div>
                <div className="text-xs text-muted">{fmt(c.followers_count)} followers · {c.counts?.scheduled || 0} scheduled</div>
              </div>
              <Badge tone={c.status === 'active' ? 'green' : 'red'}>{c.status === 'active' ? 'Live' : 'Reconnect'}</Badge>
            </a>
          ))}
          <a data-testid="home-connect-a-channel" href="#/channels" className="grid place-items-center rounded-2xl border border-dashed border-lav-300 p-4 text-sm font-medium text-lav-700 hover:bg-lav-50"><span className="flex items-center gap-2"><PlugZap className="size-4" />Connect a channel</span></a>
        </div>
      </Card>
    </div>
  )
}
