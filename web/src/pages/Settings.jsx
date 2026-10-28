import { Users, Bot, Bell, Globe, KeyRound, Keyboard, Library, ListOrdered, Play, Plus, Shield, SlidersHorizontal, Square, Trash2, Info, PlugZap, ExternalLink, FileText, ChevronRight, Sparkles, Webhook } from 'lucide-react'
import { useEffect, useState } from 'react'
import { del, get, patch, post, put } from '../api'
import { setQuery, useRoute } from '../router'
import { useStore } from '../store'
import { Badge, Button, Card, ChannelAvatar, cx, Field, Input, Kbd, Select, Switch, Textarea, useToast } from '../ui'
import { AppKeys } from '../components/AppKeys'
import { SecuritySection, TeamSection } from '../components/Team'
import { AuditLog, EditLimitsDialog, KillSwitchCard, limitsPayload, TokenLimitBadges, TokenLimitFields } from '../components/AgentSafety'
import { ChannelGroups } from '../components/AiTools'
import { AiSettings } from '../components/AiSettings'
import { WebhooksSection } from '../components/Webhooks'

// Grouped left rail, like Buffer's Settings (Account / Publishing / Channels / Developers / Other).
const GROUPS = [
  ['Account', [['general', 'Preferences', SlidersHorizontal], ['team', 'Team', Users], ['notifications', 'Notifications', Bell], ['security', 'Security', Shield], ['shortcuts', 'Shortcuts', Keyboard]]],
  ['Publishing', [['schedule', 'Posting schedules', ListOrdered], ['library', 'Tags, hashtags & replies', Library], ['public-url', 'Public media URL', Globe]]],
  ['Channels', [['channels', 'Channels', PlugZap], ['app-keys', 'App keys', KeyRound]]],
  ['Developers', [['ai', 'AI model', Sparkles], ['api', 'API & MCP agents', Bot], ['webhooks', 'Webhooks (Zapier, Make, n8n)', Webhook]]],
  ['Other', [['about', 'About', Info]]],
]
const SECTIONS = GROUPS.flatMap(([, items]) => items)

const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

function BulkChannelEdit({ channels, onDone }) {
  const toast = useToast()
  const [ids, setIds] = useState(channels.map((c) => c.id))
  const [f, setF] = useState({})
  const [busy, setBusy] = useState(false)
  const set = (k, v) => setF((x) => ({ ...x, [k]: v }))
  const apply = async () => {
    const body = { channel_ids: ids, settings: {} }
    if (f.timezone) body.timezone = f.timezone
    if (f.daily_limit) body.daily_limit = Number(f.daily_limit)
    if (f.paused) body.paused = f.paused === 'yes'
    if (f.copy_slots_from) body.copy_slots_from = f.copy_slots_from
    for (const k of ['posting_goal']) if (f[k]) body.settings[k] = Number(f[k])
    for (const k of ['link_shortening', 'utm_enabled', 'notify_default']) if (f[k]) body.settings[k] = f[k] === 'on'
    if (f.disabled_days) body.settings.disabled_days = f.disabled_days
    if (!Object.keys(body.settings).length) delete body.settings
    setBusy(true)
    try {
      const r = await post('/api/channels/bulk-settings', body)
      toast(`Updated ${r.count} channel(s)`, r.failed ? 'err' : undefined)
      for (const x of r.results.filter((y) => !y.ok)) toast(`${channels.find((c) => c.id === x.id)?.username || x.id}: ${x.error}`, 'err')
      setF({}); onDone()
    } catch (e) { toast(e.message, 'err') }
    setBusy(false)
  }
  const tri = (k, label) => (
    <Field label={label}><Select value={f[k] || ''} onChange={(e) => set(k, e.target.value)} data-testid={`bulk-ch-${k}`} options={[['', 'Leave as is'], ['on', 'On'], ['off', 'Off']]} /></Field>
  )
  return (
    <div className="space-y-4 p-5" data-testid="bulk-channel-edit">
      <div><h3 className="font-semibold">Bulk edit</h3><p className="text-sm text-muted">Apply the same settings to several channels at once. Fields left empty stay as they are.</p></div>
      <div className="flex flex-wrap gap-2">
        {channels.map((c) => {
          const on = ids.includes(c.id)
          return <button key={c.id} type="button" aria-pressed={on} data-testid={`bulk-ch-pick-${c.id}`} onClick={() => setIds(on ? ids.filter((x) => x !== c.id) : [...ids, c.id])}
            className={cx('flex cursor-pointer items-center gap-2 rounded-full border px-2 py-1 text-sm', on ? 'border-lav-500 bg-lav-100' : 'border-lav-200 opacity-60')}><ChannelAvatar ch={c} size={20} />@{c.username}</button>
        })}
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        <Field label="Timezone"><Input value={f.timezone || ''} onChange={(e) => set('timezone', e.target.value)} placeholder="Europe/Dublin" data-testid="bulk-ch-timezone" /></Field>
        <Field label="Daily limit"><Input type="number" min={1} max={50} value={f.daily_limit || ''} onChange={(e) => set('daily_limit', e.target.value)} data-testid="bulk-ch-daily-limit" /></Field>
        <Field label="Weekly posting goal"><Input type="number" min={1} max={100} value={f.posting_goal || ''} onChange={(e) => set('posting_goal', e.target.value)} data-testid="bulk-ch-posting-goal" /></Field>
        <Field label="Queue"><Select value={f.paused || ''} onChange={(e) => set('paused', e.target.value)} data-testid="bulk-ch-paused" options={[['', 'Leave as is'], ['yes', 'Paused'], ['no', 'Running']]} /></Field>
        {tri('link_shortening', 'Link shortening')}
        {tri('utm_enabled', 'UTM tagging')}
        {tri('notify_default', 'Notify me by default')}
        <Field label="Copy posting times from"><Select value={f.copy_slots_from || ''} onChange={(e) => set('copy_slots_from', e.target.value)} data-testid="bulk-ch-copy-slots"
          options={[['', 'Leave as is'], ...channels.map((c) => [c.id, `@${c.username}`])]} /></Field>
        <Field label="Days off" hint={f.disabled_days ? 'Replaces each channel’s days off' : 'Leave as is'}>
          <div className="flex gap-1">{DAY_NAMES.map((d, i) => {
            const on = (f.disabled_days || []).includes(i)
            return <button key={d} type="button" aria-pressed={on} data-testid={`bulk-ch-day-${i}`} onClick={() => set('disabled_days', on ? f.disabled_days.filter((x) => x !== i) : [...(f.disabled_days || []), i])}
              className={cx('h-8 w-10 cursor-pointer rounded-lg border text-xs', on ? 'border-rose-400 bg-rose-50 text-rose-700' : 'border-lav-200')}>{d}</button>
          })}</div>
        </Field>
      </div>
      <Button variant="primary" loading={busy} disabled={!ids.length || !Object.values(f).some((v) => v !== '' && v !== undefined)} onClick={apply} data-testid="bulk-ch-apply">Apply to {ids.length} channel(s)</Button>
    </div>
  )
}

function ChannelsSection() {
  const { channels, reload } = useStore()
  return (
    <Card className="divide-y divide-lav-100" data-testid="settings-channels">
      <div className="flex items-center gap-3 p-5">
        <div className="flex-1"><h2 className="font-semibold">Connected channels <span className="text-muted">({channels.length})</span></h2><p className="text-sm text-muted">Connect as many as you like. Every feature works on every channel.</p></div>
        <a data-testid="settings-connect-channel-2" href="#/channels"><Button variant="primary" data-testid="settings-connect-channel"><PlugZap className="size-4" />Connect channel</Button></a>
      </div>
      {channels.map((c) => (
        <a key={c.id} href={`#/channel/${c.id}?tab=overview`} className="flex items-center gap-3 p-4 hover:bg-lav-50" data-testid="settings-channel-row">
          <ChannelAvatar ch={c} size={36} />
          <div className="min-w-0 flex-1"><div className="font-medium">@{c.username}</div><div className="text-xs text-muted">{c.provider} · {c.counts?.scheduled || 0} scheduled · {c.timezone}</div></div>
          <Badge tone={c.status === 'active' ? 'green' : 'red'}>{c.status === 'active' ? 'Connected' : 'Reconnect'}</Badge>
          <span className="flex items-center text-sm text-lav-700">Settings for @{c.username}<ChevronRight className="size-4" /></span>
        </a>
      ))}
      {!channels.length && <p className="p-6 text-sm text-muted">No channels yet.</p>}
      {channels.length > 1 && <BulkChannelEdit channels={channels} onDone={reload} />}
      {channels.length > 1 && <ChannelGroups />}
    </Card>
  )
}

function General() {
  const { settings, reload } = useStore()
  const toast = useToast()
  const [s, setS] = useState(settings)
  const save = async () => { try { await put('/api/settings', s); await reload(); toast('Preferences saved') } catch (e) { toast(e.message, 'err') } }
  return (
    <Card className="space-y-5 p-6" data-testid="settings-general">
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="Default timezone (new channels)"><Input value={s.timezone} onChange={(e) => setS({ ...s, timezone: e.target.value })} data-testid="pref-timezone" /></Field>
        <Field label="Default posting choice"><Select className="w-full" value={s.default_when} onChange={(e) => setS({ ...s, default_when: e.target.value })} options={[['queue', 'Next available'], ['prioritize', 'Prioritize'], ['now', 'Publish now'], ['custom', 'Pick date & time']]} data-testid="pref-default-when" /></Field>
        <Field label="Hashtag warning above" hint="House style - Instagram's hard limit is 30."><Input type="number" min={0} max={30} value={s.hashtag_limit} onChange={(e) => setS({ ...s, hashtag_limit: Number(e.target.value) })} data-testid="pref-hashtag-limit" /></Field>
        <Field label="Default daily limit (new channels)"><Input type="number" min={0} value={s.default_daily_limit} onChange={(e) => setS({ ...s, default_daily_limit: Number(e.target.value) })} data-testid="pref-daily-limit" /></Field>
      </div>
      <div className="rounded-2xl bg-lav-50 p-4">
        <h3 className="mb-3 font-semibold">“Follow” call-to-action for videos</h3>
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Handle"><Input value={s.cta_handle || '@huhbyte'} onChange={(e) => setS({ ...s, cta_handle: e.target.value })} data-testid="pref-cta-handle" /></Field>
          <Field label="Default for video posts"><Select className="w-full" value={s.cta_default || 'comment'} onChange={(e) => setS({ ...s, cta_default: e.target.value })} options={[['off', 'Off'], ['card', 'End card'], ['comment', 'First comment'], ['both', 'End card + comment']]} data-testid="pref-cta-default" /></Field>
          <Field label="Comment / caption text"><Input value={s.cta_text || ''} placeholder={`Follow ${s.cta_handle || '@huhbyte'} for daily AI news`} onChange={(e) => setS({ ...s, cta_text: e.target.value })} data-testid="pref-cta-text" /></Field>
          <Field label="End card text" hint={settings.ffmpeg ? 'ffmpeg found ✓' : 'Install ffmpeg to use end cards'}><Input value={s.cta_card_text || ''} placeholder={`Follow ${s.cta_handle || '@huhbyte'}\\nfor daily AI news`} onChange={(e) => setS({ ...s, cta_card_text: e.target.value })} data-testid="pref-cta-card" /></Field>
        </div>
      </div>
      <Button variant="primary" onClick={save} data-testid="pref-save">Save preferences</Button>
    </Card>
  )
}

function Schedules() {
  const { channels } = useStore()
  return (
    <Card className="divide-y divide-lav-100" data-testid="settings-schedule">
      {channels.map((c) => (
        <a key={c.id} href={`#/channel/${c.id}?tab=schedule`} className="flex items-center gap-3 p-4 hover:bg-lav-50" data-testid="settings-schedule-channel">
          <ChannelAvatar ch={c} size={36} /><div className="flex-1"><div className="font-medium">@{c.username}</div><div className="text-xs text-muted">{c.slots.length} slots/week · {c.daily_limit}/day · {c.timezone}</div></div><span className="text-sm text-lav-700">Edit →</span>
        </a>
      ))}
      {!channels.length && <p className="p-6 text-sm text-muted">Connect a channel first.</p>}
    </Card>
  )
}

function PublicUrl() {
  const { settings, reload } = useStore()
  const toast = useToast()
  const [t, setT] = useState(null)
  const [manual, setManual] = useState(settings.public_base_url || '')
  const [domain, setDomain] = useState('')
  const [busy, setBusy] = useState('')
  const [current, setCurrent] = useState(settings.effective_public_base_url || '')
  useEffect(() => { setCurrent(settings.effective_public_base_url || '') }, [settings.effective_public_base_url])
  const load = () => get('/api/tunnel').then(setT)
  useEffect(() => { load() }, [])
  const start = async (kind) => { setBusy(kind); try { const r = await post('/api/tunnel/start', { kind, domain }); setT(r); setCurrent(r.public_base_url || ''); r.error ? toast(r.error, 'err') : toast('Tunnel running'); reload() } finally { setBusy('') } }
  return (
    <Card className="space-y-5 p-6" data-testid="settings-public-url">
      <p className="text-sm text-ink-2">Instagram (and TikTok photo posts) download your media from a public https URL. HuhQueue serves media and OAuth callbacks from a separate “edge” port (<code>{settings.edge_port}</code>) - only that port is exposed. Your dashboard stays on localhost.</p>
      <div className="flex items-center gap-3 rounded-2xl bg-lav-50 p-4">
        <span className={cx('size-2.5 rounded-full', current ? 'bg-emerald-500' : 'bg-zinc-300')} />
        <span className="text-sm">Current public URL: <b data-testid="public-url-current">{current || 'none'}</b>{settings.public_base_url_from_env && <Badge tone="gray" className="ml-2">from .env</Badge>}</span>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-2xl border border-lav-200 p-4">
          <h3 className="font-semibold">Free cloudflared quick tunnel</h3>
          <p className="mb-3 text-xs text-muted">No account. A new random *.trycloudflare.com URL each start. {t && !t.cloudflared_installed && 'Not installed - see SETUP.md.'}</p>
          <div className="flex gap-2">
            <Button variant="primary" loading={busy === 'cloudflared'} onClick={() => start('cloudflared')} data-testid="tunnel-start-cloudflared"><Play className="size-4" />Start</Button>
            <Button variant="outline" onClick={async () => { const r = await post('/api/tunnel/stop'); setT(r); setCurrent(r.public_base_url || ''); reload() }} data-testid="tunnel-stop"><Square className="size-4" />Stop</Button>
          </div>
        </div>
        <div className="rounded-2xl border border-lav-200 p-4">
          <h3 className="font-semibold">ngrok (free static domain)</h3>
          <p className="mb-3 text-xs text-muted">Stable URL - best for OAuth redirect URIs. Run <code>ngrok config add-authtoken …</code> once.</p>
          <div className="flex gap-2"><Input placeholder="your-name.ngrok-free.app" value={domain} onChange={(e) => setDomain(e.target.value)} data-testid="tunnel-ngrok-domain" /><Button variant="primary" loading={busy === 'ngrok'} onClick={() => start('ngrok')} data-testid="tunnel-start-ngrok">Start</Button></div>
        </div>
      </div>
      {t?.error && <p className="rounded-xl bg-rose-50 p-3 text-sm text-rose-800" data-testid="tunnel-error">{t.error}</p>}
      {t?.log?.length > 0 && <pre className="max-h-40 overflow-auto rounded-xl bg-[#141020] p-3 text-[11px] text-[#e6d9fe]">{t.log.join('\n')}</pre>}
      <Field label="Or set it by hand (your own domain / named Cloudflare tunnel)">
        <div className="flex gap-2"><Input value={manual} onChange={(e) => setManual(e.target.value)} placeholder="https://media.example.com" data-testid="public-url-input" />
          <Button variant="soft" loading={busy === 'save'} onClick={async () => {
            setBusy('save')
            try { const r = await put('/api/settings', { public_base_url: manual.trim() }); setCurrent(r.effective_public_base_url || ''); await reload(); toast(r.effective_public_base_url ? 'Public URL saved' : 'Public URL cleared') } catch (e) { toast(e.message, 'err') } finally { setBusy('') }
          }} data-testid="public-url-save">Save</Button></div>
      </Field>
    </Card>
  )
}

function LibraryEditor() {
  const { library, reload } = useStore()
  const toast = useToast()
  const [kind, setKind] = useState('templates')
  const [items, setItems] = useState(library[kind] || [])
  useEffect(() => { setItems(library[kind] || []) }, [kind, library])
  const save = async () => { await put(`/api/library/${kind}`, { items }); await reload(); toast('Library saved') }
  return (
    <Card className="space-y-4 p-6" data-testid="settings-library">
      <div className="flex flex-wrap gap-2">
        {[['templates', 'Caption templates'], ['hashtag_groups', 'Hashtag groups'], ['saved_replies', 'Saved replies'], ['tags', 'Tags']].map(([k, l]) =>
          <Button key={k} size="sm" variant={kind === k ? 'primary' : 'outline'} onClick={() => setKind(k)} data-testid={`library-${k}`}>{l}</Button>)}
      </div>
      {items.map((it, i) => (
        <div key={it.id || i} className="flex flex-wrap items-start gap-2 rounded-xl border border-lav-100 p-3" data-testid="library-item">
          <Input className="w-48" value={it.name} onChange={(e) => setItems(items.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} aria-label="Name" />
          {kind === 'tags' ? <Input type="color" className="w-16 p-1" value={it.color || '#C9ACFA'} onChange={(e) => setItems(items.map((x, j) => (j === i ? { ...x, color: e.target.value } : x)))} aria-label="Colour" />
            : <Textarea rows={2} className="min-w-64 flex-1" value={it.text} onChange={(e) => setItems(items.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))} aria-label="Text" />}
          <Button size="icon" variant="danger-ghost" aria-label="Remove" onClick={() => setItems(items.filter((_, j) => j !== i))} data-testid="library-remove"><Trash2 className="size-4" /></Button>
        </div>
      ))}
      <div className="flex gap-2">
        <Button variant="outline" onClick={() => setItems([...items, { name: 'New', text: '', color: '#C9ACFA' }])} data-testid="library-add"><Plus className="size-4" />Add</Button>
        <Button variant="primary" onClick={save} data-testid="library-save">Save</Button>
      </div>
    </Card>
  )
}

function Notifications() {
  const { settings, reload } = useStore()
  const toast = useToast()
  const [url, setUrl] = useState(settings.notify_webhook_url || '')
  const [mail, setMail] = useState(settings.notify_email_to || '')
  const [recap, setRecap] = useState(settings.recap_time || '08:00')
  const kinds = settings.notify_kinds || {}
  const events = settings.notify_events || {}
  const emails = settings.notify_email || {}
  const save = async (body, msg = 'Saved') => { try { await put('/api/settings', body); await reload(); toast(msg) } catch (e) { toast(e.message, 'err') } }
  const test = async () => { try { const r = await post('/api/notifications/test'); toast(`Test sent to ${r.sent_to}`) } catch (e) { toast(e.message, 'err') } }
  return (
    <Card className="space-y-5 p-6" data-testid="settings-notifications">
      <p className="text-sm text-ink-2">In-app notifications are always on (bell, top right). Optionally push them to your phone through a free webhook - e.g. an <b>ntfy.sh</b> topic, or a Slack / Discord incoming webhook.</p>
      <Field label="Webhook URL"><Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://ntfy.sh/your-secret-topic" data-testid="notify-webhook" /></Field>
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" onClick={() => save({ notify_webhook_url: url })} data-testid="notify-save">Save</Button>
        <Button onClick={test} disabled={!settings.notify_webhook_url} data-testid="notify-test"><Bell className="size-4" />Send test</Button>
      </div>
      <div className="flex items-center justify-between rounded-xl border border-lav-200 p-3">
        <div><div className="text-sm font-medium">Pause all push and email notifications</div><p className="text-xs text-muted">The bell keeps everything; nothing is sent to the webhook or by email while paused.</p></div>
        <Switch checked={!!settings.notify_paused} onChange={(v) => save({ notify_paused: v }, v ? 'Push paused' : 'Push resumed')} label="Pause push notifications" testid="notify-pause" />
      </div>
      <div className="grid gap-3 md:grid-cols-[1fr_auto_auto]">
        <Field label="Email notifications to" hint={settings.smtp_configured ? 'Sent through your SMTP server (HUHQ_SMTP_*).' : 'Needs HUHQ_SMTP_HOST etc. in .env before emails go out.'}>
          <Input type="email" value={mail} onChange={(e) => setMail(e.target.value)} placeholder="you@example.com" data-testid="notify-email-to" />
        </Field>
        <Field label="Daily recap at"><Input type="time" value={recap} onChange={(e) => setRecap(e.target.value)} data-testid="notify-recap-time" /></Field>
        <div className="flex items-end"><Button onClick={() => save({ notify_email_to: mail, recap_time: recap })} data-testid="notify-email-save">Save</Button></div>
      </div>
      <div>
        <div className="mb-2 flex items-center gap-3 px-3 text-xs font-semibold uppercase tracking-wide text-muted"><span className="flex-1">Tell me when…</span><span className="w-11 text-center">Push</span><span className="w-11 text-center">Email</span></div>
        <div className="divide-y divide-lav-100 rounded-xl border border-lav-200" data-testid="notify-kinds">
          {Object.entries(kinds).map(([k, label]) => (
            <div key={k} className="flex items-center gap-3 px-3 py-2.5">
              <span className="flex-1 text-sm">{label}</span>
              <Switch checked={events[k] !== false} disabled={!!settings.notify_paused} onChange={(v) => save({ notify_events: { [k]: v } })} label={`Push: ${label}`} testid={`notify-kind-${k}`} />
              <Switch checked={!!emails[k]} disabled={!!settings.notify_paused || !settings.notify_email_to} onChange={(v) => save({ notify_email: { [k]: v } })} label={`Email: ${label}`} testid={`notify-email-${k}`} />
            </div>
          ))}
        </div>
      </div>
    </Card>
  )
}

function Api() {
  const toast = useToast()
  const [data, setData] = useState(null)
  const [form, setForm] = useState({ name: 'huhbyte-pipeline', scopes: ['read', 'write', 'publish', 'comments'], tools: [], require_approval: true, rate_per_min: 60 })
  const [limits, setLimits] = useState({})
  const [editing, setEditing] = useState(null)
  const [minted, setMinted] = useState(null)
  const load = () => get('/api/tokens').then(setData)
  useEffect(() => { load() }, [])
  if (!data) return null
  const toggle = (k, v) => setForm({ ...form, [k]: form[k].includes(v) ? form[k].filter((x) => x !== v) : [...form[k], v] })
  const mcp = JSON.stringify({ mcpServers: { huhqueue: { command: 'huhqueue', args: ['mcp'], env: { HUHQ_URL: window.location.origin, HUHQ_API_TOKEN: minted?.token || 'hq_…' } } } }, null, 2)
  return (
    <div className="space-y-6" data-testid="settings-api">
      <KillSwitchCard />
      <Card className="space-y-4 p-6">
        <h3 className="font-semibold">Create a scoped token</h3>
        <p className="text-sm text-muted">For the CLI, your huh byte pipeline, or an AI agent via MCP.</p>
        <div className="flex flex-wrap gap-2" data-testid="api-doc-links">
          {[['/agents', 'For agents', Bot], ['/api/docs', 'REST docs (Swagger)', FileText], ['/api/openapi.json', 'OpenAPI JSON', FileText], ['/llms.txt', 'llms.txt', FileText]].map(([h, l, I]) => (
            <a data-testid="settings-a" key={h} href={h} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 rounded-xl border border-lav-200 px-3 py-1.5 text-[13px] text-ink-2 hover:border-lav-400"><I className="size-3.5 text-lav-600" />{l}<ExternalLink className="size-3 text-muted" /></a>
          ))}
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} data-testid="token-name" /></Field>
          <Field label="Rate limit (requests / minute)"><Input type="number" value={form.rate_per_min} onChange={(e) => setForm({ ...form, rate_per_min: Number(e.target.value) })} data-testid="token-rate" /></Field>
        </div>
        <div><div className="mb-2 text-[13px] font-medium text-ink-2">Scopes</div><div className="flex flex-wrap gap-2">{Object.entries(data.scopes).map(([k, d]) => (
          <label key={k} title={d} className={cx('flex cursor-pointer items-center gap-2 rounded-xl border px-3 py-1.5 text-sm', form.scopes.includes(k) ? 'border-lav-600 bg-lav-100' : 'border-lav-200')}><input type="checkbox" className="accent-lav-600" checked={form.scopes.includes(k)} onChange={() => toggle('scopes', k)} data-testid={`token-scope-${k}`} />{k}</label>))}</div></div>
        <div><div className="mb-2 text-[13px] font-medium text-ink-2">MCP tools allowed <span className="text-muted">(none ticked = all the scopes permit)</span></div><div className="flex flex-wrap gap-2">{data.tools.map((k) => (
          <label key={k} className={cx('flex cursor-pointer items-center gap-2 rounded-xl border px-3 py-1.5 font-mono text-xs', form.tools.includes(k) ? 'border-lav-600 bg-lav-100' : 'border-lav-200')}><input type="checkbox" className="accent-lav-600" checked={form.tools.includes(k)} onChange={() => toggle('tools', k)} data-testid={`token-tool-${k}`} />{k}</label>))}</div></div>
        <label className="flex items-center gap-3 text-sm"><Switch checked={form.require_approval} onChange={(v) => setForm({ ...form, require_approval: v })} label="Require approval" testid="token-approval" />Require my approval before anything this token schedules or publishes goes live</label>
        <details className="rounded-xl border border-lav-200 p-4" data-testid="token-limits-section">
          <summary className="cursor-pointer text-sm font-medium">Safety limits <span className="text-muted">(daily caps, channels, expiry)</span></summary>
          <div className="pt-4"><TokenLimitFields value={limits} onChange={setLimits} /></div>
        </details>
        <Button variant="primary" onClick={async () => { try { setMinted(await post('/api/tokens', { ...form, ...limitsPayload(limits) })); load() } catch (e) { toast(e.message, 'err') } }} data-testid="token-create"><KeyRound className="size-4" />Create token</Button>
        {minted && <div className="space-y-2 rounded-2xl bg-[#141020] p-4 text-[#f3ecff]" data-testid="token-minted"><p className="text-xs">Copy now - shown once:</p><code className="block break-all text-sm text-white">{minted.token}</code><p className="pt-2 text-xs">MCP client config:</p><pre className="overflow-auto text-[11px]">{mcp}</pre></div>}
      </Card>
      <Card className="p-6">
        <h3 className="mb-3 font-semibold">Tokens</h3>
        <ul className="divide-y divide-lav-100">
          {data.tokens.map((t) => (
            <li key={t.id} className="flex flex-wrap items-center gap-3 py-3 text-sm" data-testid="token-row">
              <b>{t.name}</b><code className="text-xs text-muted">{t.prefix}…</code>{t.scopes.map((s) => <Badge key={s} tone="lav">{s}</Badge>)}
              {t.require_approval ? <Badge tone="amber">approval</Badge> : <Badge tone="red">direct publish</Badge>}
              <TokenLimitBadges t={t} />
              <span className="text-xs text-muted">{t.rate_per_min}/min · last used {t.last_used_at ? new Date(t.last_used_at).toLocaleString() : 'never'}</span>
              <Button size="sm" variant="ghost" className="ml-auto" onClick={() => setEditing(t)} data-testid="token-edit-limits">Limits</Button>
              <Button size="sm" variant="ghost" onClick={async () => { await patch(`/api/tokens/${t.id}`, { require_approval: !t.require_approval }); load() }} data-testid="token-toggle-approval">{t.require_approval ? 'Allow direct' : 'Require approval'}</Button>
              <Button size="sm" variant="danger-ghost" onClick={async () => { await del(`/api/tokens/${t.id}`); load(); toast('Token revoked') }} data-testid="token-revoke"><Trash2 className="size-3.5" />Revoke</Button>
            </li>
          ))}
          {!data.tokens.length && <li className="py-3 text-sm text-muted">No tokens yet.</li>}
        </ul>
      </Card>
      <AuditLog />
      <EditLimitsDialog token={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load() }} />
    </div>
  )
}

function Shortcuts() {
  const rows = [['⌘K / Ctrl K or /', 'Command palette'], ['C', 'Create post'], ['G then H / I / P / C / M / A / S', 'Go to Home / Create / Publish / Calendar / Community / Insights / Settings'], ['⌘↵', 'Schedule (in composer)'], ['⌘S', 'Save draft (in composer)'], ['Esc', 'Close dialog'], ['?', 'This page']]
  return <Card className="p-6" data-testid="settings-shortcuts"><table className="w-full text-sm"><tbody>{rows.map(([k, d]) => <tr key={k} className="border-b border-lav-100"><td className="py-2 pr-4"><Kbd>{k}</Kbd></td><td>{d}</td></tr>)}</tbody></table></Card>
}

function About() {
  const { settings } = useStore()
  return (
    <Card className="space-y-2 p-6 text-sm" data-testid="settings-about">
      <p><b>HuhQueue</b> {settings.version} - free, self-hosted social scheduler for HuhByte. MIT licence.</p>
      <p>Data folder: <code>{settings.data_dir}</code></p>
      <p>Instagram app keys: {settings.configured.instagram ? '✓' : '✗'} · TikTok: {settings.configured.tiktok ? '✓' : '✗'} (audited: {String(settings.tiktok_audited)}) · YouTube: {settings.configured.youtube ? '✓' : '✗'} (verified: {String(settings.youtube_verified)}) · ffmpeg: {settings.ffmpeg ? '✓' : '✗'}</p>
    </Card>
  )
}

function AppKeysSection() {
  return (
    <Card className="space-y-4 p-6" data-testid="settings-app-keys">
      <div><h2 className="font-semibold">Developer app keys</h2><p className="text-sm text-muted">Each network signs you in through your own developer app. Keys are stored encrypted on this server and never shown again. See SETUP.md for the 5-minute walkthrough.</p></div>
      <AppKeys />
    </Card>
  )
}

export default function SettingsPage() {
  const { query } = useRoute()
  const { can } = useStore()
  const sec = query.section || 'general'
  const groups = GROUPS.map(([g, items]) => [g, items.filter(([k]) => k !== 'team' || can.manage_team)])
    .filter(([g]) => can.settings || ['Account', 'Other'].includes(g))
  const Body = { general: General, ai: AiSettings, 'app-keys': AppKeysSection, schedule: Schedules, 'public-url': PublicUrl, library: LibraryEditor, notifications: Notifications, api: Api, security: SecuritySection, team: TeamSection, shortcuts: Shortcuts, about: About, channels: ChannelsSection, webhooks: WebhooksSection }[sec] || General
  const label = SECTIONS.find(([k]) => k === sec)?.[1] || 'Preferences'
  return (
    <div className="grid gap-8 lg:grid-cols-[232px_1fr]" data-testid="page-settings">
      <nav className="lg:sticky lg:top-0 lg:self-start" aria-label="Settings sections">
        <h1 className="mb-4 text-2xl font-semibold tracking-tight">Settings</h1>
        <div className="space-y-5">
          {groups.map(([g, items]) => (
            <div key={g} data-testid={`settings-group-${g.toLowerCase()}`}>
              <div className="mb-1 px-3 text-[11px] font-semibold uppercase tracking-widest text-muted">{g}</div>
              {items.map(([k, l, I]) => (
                <a key={k} href={`#/settings?section=${k}`} aria-current={sec === k ? 'page' : undefined} data-testid={`settings-nav-${k}`}
                  className={cx('flex items-center gap-2 rounded-xl px-3 py-2 text-sm transition-colors', sec === k ? 'bg-surface font-medium text-ink shadow-soft' : 'text-ink-2 hover:bg-surface/60')}><I className="size-4 text-lav-600" />{l}</a>
              ))}
            </div>
          ))}
        </div>
      </nav>
      <div className="min-w-0 space-y-4">
        <h2 className="text-lg font-semibold" data-testid="settings-title">{label}</h2>
        <Body key={sec} />
      </div>
    </div>
  )
}
