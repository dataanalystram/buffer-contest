import { motion } from 'motion/react'
import { ArrowLeft, Check, ExternalLink, KeyRound, Link2, ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import { post } from '../api'
import { go, setQuery, useRoute } from '../router'
import { useStore } from '../store'
import { Badge, Button, Card, ChannelAvatar, Field, fmt, Input, NETWORKS, NetIcon, Textarea, useToast } from '../ui'
import { AppKeys, CopyText } from '../components/AppKeys'

export const GUIDE = 'https://github.com/dataanalystram/bufferopensource/blob/main/docs/CONNECT_INTEGRATIONS.md'
const GUIDE_ANCHOR = { linkedin: '#1-linkedin-personal-profile', linkedin_page: '#2-linkedin-company-pages', instagram: '#3-instagram-and-facebook-meta',
  youtube: '#4-youtube', x: '#5-x-twitter', tiktok: '#6-tiktok' }

const COPY = {
  instagram: {
    title: 'Connect an Instagram Professional account',
    perks: ['Automatic posting: photos, carousels (up to 20), Reels and Stories', 'First comment + "Follow @huhbyte" CTA', 'Comment inbox: read & reply', 'Post insights: views, reach, likes, saves, shares'],
    note: 'Uses Instagram Business Login. You will see Instagram’s own “Continue as …?” screen. Business or Creator accounts only - Instagram offers to switch a personal account for free.',
  },
  tiktok: {
    title: 'Connect TikTok',
    perks: ['Direct post videos and photo carousels', '“Send to TikTok inbox” to finish in the app', 'Profile stats and video views'],
    note: 'Uses TikTok Login Kit. Until TikTok audits your app, direct posts are private (Only me) - use inbox mode to go public from the app.',
  },
  youtube: {
    title: 'Connect a YouTube channel',
    perks: ['Upload Shorts and videos (resumable upload)', 'First comment + CTA', 'Comment inbox: read & reply', 'Views / likes / comments'],
    note: 'Uses Google sign-in (PKCE). Unverified Google projects upload as Private until Google verifies the project.',
  },
  x: {
    title: 'Connect an X account',
    perks: ['Posts with up to 4 images, or a video / GIF (chunked upload)', 'Reply under the post for the follow CTA', 'Past posts and views / likes / reposts'],
    note: 'Uses X OAuth 2.0 with PKCE. You will see X’s own “Authorize app” screen.',
  },
  linkedin: {
    title: 'Connect your LinkedIn profile',
    perks: ['Text, image, multi-image (up to 20), PDF document carousels and video', 'Link previews with the page’s own image', 'First comment under the post', 'Reminder 7 days before the 60-day login runs out'],
    note: 'Uses “Sign In with LinkedIn” (OpenID) + “Share on LinkedIn” - no LinkedIn review needed. LinkedIn logins last 60 days; HuhQueue warns you a week before.',
  },
  linkedin_page: {
    title: 'Connect LinkedIn company pages',
    perks: ['Every page you administer becomes its own channel', 'All LinkedIn post types + first comment', 'Page post stats'],
    note: 'Needs a second LinkedIn app with only the Community Management API (LinkedIn approves it for registered companies). Turn on with LINKEDIN_PAGES_ENABLED=1.',
  },
  fake: { title: 'Demo network', perks: ['Safe sandbox for trying every button'], note: 'Never leaves your laptop.' },
}

export default function Channels() {
  const { channels, providers, reload } = useStore()
  const { query } = useRoute()
  const toast = useToast()
  const [token, setToken] = useState('')
  const [pasted, setPasted] = useState('')
  const [busy, setBusy] = useState(false)
  const net = query.network
  const conn = providers?.connectors.find((c) => c.id === net)

  async function startOAuth() {
    setBusy(true)
    try {
      const r = await post(`/api/connect/${net}`)
      window.location.href = r.url
    } catch (e) { toast(e.message, 'err'); setBusy(false) }
  }
  async function useToken() {
    setBusy(true)
    try {
      const ch = await post(`/api/connect/${net}/token`, { access_token: token })
      await reload()
      toast(`Connected @${ch.username}`)
      go(`/channel/${ch.id}`, { connected: '1' })
    } catch (e) { toast(e.message, 'err') } finally { setBusy(false) }
  }
  async function usePasted() {
    setBusy(true)
    try {
      const ch = await post('/api/oauth/complete', { url: pasted })
      await reload()
      go(`/channel/${ch.id}`, { connected: '1' })
    } catch (e) { toast(e.message, 'err') } finally { setBusy(false) }
  }

  if (net && conn) {
    const c = COPY[net] || { title: `Connect ${conn.name}`, perks: [], note: '' }
    return (
      <div className="mx-auto max-w-3xl space-y-6" data-testid="connect-explainer">
        <button onClick={() => setQuery({ network: '' })} className="flex items-center gap-1 text-sm text-muted hover:text-ink cursor-pointer" data-testid="connect-back"><ArrowLeft className="size-4" />All networks</button>
        <Card className="overflow-hidden">
          <div className="flex items-center gap-4 bg-gradient-to-r from-lav-100 to-surface p-6">
            <NetIcon provider={net} size="lg" />
            <div><h1 className="text-xl font-semibold">{c.title}</h1><p className="text-sm text-muted">{c.note}</p></div>
          </div>
          <div className="grid gap-6 p-6 md:grid-cols-2">
            <ul className="space-y-2">
              {c.perks.map((p) => <li key={p} className="flex gap-2 text-sm"><Check className="mt-0.5 size-4 shrink-0 text-emerald-600" />{p}</li>)}
              <li className="flex gap-2 text-sm text-muted"><ShieldCheck className="mt-0.5 size-4 shrink-0 text-lav-600" />Tokens are encrypted on this computer. Nothing goes to a third party.</li>
            </ul>
            <div className="space-y-3">
              {conn.configured ? (
                <Button variant="primary" size="lg" className="w-full justify-center" loading={busy} onClick={startOAuth} data-testid="connect-oauth">
                  <ExternalLink className="size-4" />Continue to {conn.name}
                </Button>
              ) : (
                <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900" data-testid="connect-not-configured">
                  <b>{net === 'instagram' ? 'Add your Meta App ID first.' : `Add your ${conn.name} app keys first.`}</b> HuhQueue signs in through <i>your own</i> developer app, so {conn.name} shows your app’s name on the consent screen. Paste the keys below - no terminal needed.
                </div>
              )}
              <Field label="Redirect URI to register in the developer app"><CopyText text={providers.redirect_uris[net]} testid="connect-redirect-uri" /></Field>
              <a href={GUIDE + (GUIDE_ANCHOR[net] || '')} target="_blank" rel="noreferrer" data-testid="connect-guide-link"
                className="inline-flex items-center gap-1.5 text-sm font-medium text-lav-700 hover:underline"><ExternalLink className="size-3.5" />Step-by-step guide: create the {conn.name} app</a>
            </div>
          </div>
          {!conn.configured && (
            <div className="border-t border-lav-100 p-6" data-testid="connect-keys-form">
              <AppKeys only={net} compact />
            </div>
          )}
          <details className="border-t border-lav-100 p-6" open={query.advanced === '1'}>
            <summary className="cursor-pointer text-sm font-medium text-ink-2" data-testid="connect-advanced">Other ways to connect</summary>
            <div className="mt-4 grid gap-6 md:grid-cols-2">
              {conn.capabilities.manual_token && (
                <Field label={<span className="flex items-center gap-1"><KeyRound className="size-3.5" />Paste an access token</span>} hint={net === 'instagram' ? 'Meta dashboard → Instagram → API setup with Instagram login → Generate token.' : 'From your developer dashboard.'}>
                  <Input value={token} onChange={(e) => setToken(e.target.value)} placeholder="IGAA…" data-testid="connect-token-input" aria-label="Access token" />
                  <Button className="mt-2" variant="soft" disabled={!token} loading={busy} onClick={useToken} data-testid="connect-token-submit">Connect with token</Button>
                </Field>
              )}
              <Field label={<span className="flex items-center gap-1"><Link2 className="size-3.5" />Tunnel was off? Paste the URL you landed on</span>} hint="After consent the browser lands on …/oauth/{network}/callback?code=…">
                <Textarea rows={2} value={pasted} onChange={(e) => setPasted(e.target.value)} placeholder="https://…/oauth/instagram/callback?code=…&state=…" data-testid="connect-paste-input" aria-label="Callback URL" />
                <Button className="mt-2" variant="soft" disabled={!pasted} loading={busy} onClick={usePasted} data-testid="connect-paste-submit">Finish connecting</Button>
              </Field>
            </div>
          </details>
        </Card>
      </div>
    )
  }

  return (
    <div className="space-y-8" data-testid="page-channels">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Connect a channel</h1>
        <p className="text-muted">Pick a network. You sign in on the network’s own page and come straight back. No channel limits.</p>
      </div>
      {query.connect_error && <Card className="border-rose-200 bg-rose-50 p-4 text-sm text-rose-800" role="alert" data-testid="connect-error">{query.connect_error}</Card>}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {providers?.connectors.map((c, i) => (
          <motion.button key={c.id} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }}
            onClick={() => setQuery({ network: c.id })} data-testid={`connect-${c.id}`} aria-label={`Connect ${c.name}`}
            className="group flex cursor-pointer items-center gap-4 rounded-2xl border border-lav-200 bg-surface p-5 text-left shadow-soft transition hover:-translate-y-0.5 hover:border-lav-400 hover:shadow-pop">
            <NetIcon provider={c.id} size="lg" />
            <div className="flex-1">
              <div className="font-semibold">{c.name}</div>
              <div className="text-xs text-muted">{c.capabilities.post_types?.join(' · ')}</div>
            </div>
            <Badge tone={c.configured ? 'green' : 'amber'}>{c.configured ? 'Ready' : 'Needs keys'}</Badge>
          </motion.button>
        ))}
        {providers?.planned.map((p) => (
          <div key={p.id} className="flex items-center gap-4 rounded-2xl border border-dashed border-lav-200 bg-surface/50 p-5 opacity-70" data-testid={`planned-${p.id}`}>
            <NetIcon provider={p.id} size="lg" />
            <div className="flex-1"><div className="font-semibold">{p.name}</div><div className="text-xs text-muted">Plugin slot - see docs/CONNECTORS.md</div></div>
            <Badge tone="gray">Soon</Badge>
          </div>
        ))}
      </div>
      <section>
        <h2 className="mb-3 font-semibold">Connected ({channels.length})</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {channels.map((c) => (
            <a key={c.id} href={`#/channel/${c.id}`} className="flex items-center gap-3 rounded-2xl border border-lav-100 bg-surface p-4 hover:border-lav-300" data-testid="connected-channel">
              <ChannelAvatar ch={c} size={44} />
              <div className="min-w-0 flex-1"><div className="truncate font-medium">@{c.username}</div><div className="text-xs text-muted">{fmt(c.followers_count)} followers · {NETWORKS[c.provider]?.name}</div></div>
              <Badge tone={c.status === 'active' ? 'green' : 'red'}>{c.status === 'active' ? 'Live' : 'Reconnect'}</Badge>
            </a>
          ))}
        </div>
      </section>
    </div>
  )
}
