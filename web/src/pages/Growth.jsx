// Growth: Buffer's paid growth tools, free and self-hosted.
//   Link in bio (Start Page) · Short links + UTM builder · Campaigns · Hashtags · Automations · CSV import
// Every control maps to a registry action (bio.*, link.*, utm.*, campaign.*, hashtag.*, automation.*, import.*),
// so the same things work from MCP, the CLI and the ⌘K bar.
import { motion } from 'motion/react'
import {
  ArrowDown, ArrowUp, Copy, Download, ExternalLink, Eye, EyeOff, Hash, Link2, Megaphone, MessageSquare, MousePointerClick,
  Pause, Play, Plus, Recycle, Repeat, Rss, Sparkles, Trash2, Upload, Wand2,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { del, get, patch, post, put } from '../api'
import { go, setQuery, useRoute } from '../router'
import { useStore } from '../store'
import { BioTab } from './bio'
import {
  Badge, Button, Card, ChannelAvatar, cx, Dialog, Empty, Field, fmt, Input, NetIcon, Select, SkeletonList, Stat, Switch, Tabs, Textarea, useToast,
} from '../ui'

const TABS = [['bio', 'Link in bio'], ['links', 'Short links & UTM'], ['campaigns', 'Campaigns'], ['hashtags', 'Hashtags'], ['automations', 'Automations'], ['import', 'CSV import']]

export default function Growth() {
  const { parts } = useRoute()
  const tab = TABS.some(([v]) => v === parts[1]) ? parts[1] : 'bio'
  return (
    <div className="space-y-5" data-testid="page-growth">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Growth</h1>
          <p className="text-sm text-muted">A link-in-bio page, tracked short links, UTM tags, campaigns, hashtag groups, automations and bulk import. All self-hosted, no extra subscriptions.</p>
        </div>
      </header>
      <Tabs value={tab} onChange={(v) => go('/growth/' + v)} items={TABS} testid="growth-tab" />
      {tab === 'bio' && <BioTab />}
      {tab === 'links' && <LinksTab />}
      {tab === 'campaigns' && <CampaignsTab />}
      {tab === 'hashtags' && <HashtagsTab />}
      {tab === 'automations' && <AutomationsTab />}
      {tab === 'import' && <ImportTab />}
    </div>
  )
}

// ------------------------------------------------------------------ shared bits
function useCopy() {
  const toast = useToast()
  return async (text, what = 'Link') => {
    try { await navigator.clipboard.writeText(text); toast(`${what} copied`) } catch { toast(text) }
  }
}

function Bars({ data, keys = [['clicks', 'bg-lav-500']], testid, height = 96 }) {
  const max = Math.max(1, ...data.flatMap((d) => keys.map(([k]) => d[k] || 0)))
  return (
    <div className="flex items-end gap-[3px]" style={{ height }} data-testid={testid} aria-label="Daily chart">
      {data.map((d) => (
        <div key={d.day} className="group relative flex h-full flex-1 items-end gap-px" title={`${d.day}: ${keys.map(([k]) => `${d[k] || 0} ${k}`).join(', ')}`}>
          {keys.map(([k, color]) => (
            <motion.div key={k} initial={{ height: 0 }} animate={{ height: `${((d[k] || 0) / max) * 100}%` }} transition={{ duration: 0.4 }}
              className={cx('min-h-[2px] flex-1 rounded-t', color, !(d[k] > 0) && 'opacity-25')} />
          ))}
        </div>
      ))}
    </div>
  )
}

function ChannelPicker({ value = [], onChange, testid = 'channel-picker' }) {
  const { channels } = useStore()
  if (!channels.length) return <p className="text-sm text-muted">Connect a channel first (Channels page).</p>
  return (
    <div className="flex flex-wrap gap-2" data-testid={testid}>
      {channels.map((c) => {
        const on = value.includes(c.id)
        return (
          <button key={c.id} type="button" onClick={() => onChange(on ? value.filter((x) => x !== c.id) : [...value, c.id])} aria-pressed={on}
            data-testid={`${testid}-option`}
            className={cx('flex cursor-pointer items-center gap-2 rounded-full border py-1 pl-1 pr-3 text-sm transition', on ? 'border-lav-500 bg-lav-100' : 'border-lav-200 hover:border-lav-400')}>
            <ChannelAvatar ch={c} size={24} />@{c.username}
          </button>
        )
      })}
    </div>
  )
}

const channelName = (channels, id) => { const c = channels.find((x) => x.id === id); return c ? `@${c.username}` : id }
const when = (iso) => (iso ? new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '–')

// ================================================================== Link in bio: see Bio.jsx

// ================================================================== short links + UTM
const UTM_FIELDS = [['source', 'Source', '{provider}'], ['medium', 'Medium', 'social'], ['campaign', 'Campaign', '{campaign}'], ['term', 'Term', ''], ['content', 'Content', '']]

function tagUrl(url, utm) {
  try {
    const u = new URL(url)
    for (const [k, v] of Object.entries(utm)) if (v && !u.searchParams.has(`utm_${k}`)) u.searchParams.set(`utm_${k}`, v)
    return u.toString()
  } catch { return '' }
}

function LinksTab() {
  const toast = useToast()
  const copy = useCopy()
  const { query } = useRoute()
  const { channels } = useStore()
  const [rows, setRows] = useState(null)
  const [campaigns, setCampaigns] = useState([])
  const [form, setForm] = useState({ url: '', code: '', title: '', campaign: '', utm: {} })
  const [showUtm, setShowUtm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [detail, setDetail] = useState(null)
  const [defaults, setDefaults] = useState(null)
  const load = () => {
    const p = new URLSearchParams()
    if (query.q) p.set('q', query.q)
    if (query.source) p.set('source', query.source)
    get('/api/links?' + p).then(setRows).catch((e) => toast(e.message, 'err'))
  }
  useEffect(load, [query.q, query.source])
  useEffect(() => { get('/api/campaigns').then(setCampaigns).catch(() => {}); get('/api/utm/defaults').then(setDefaults).catch(() => {}) }, [])
  useEffect(() => { if (query.link) get(`/api/links/${query.link}`).then(setDetail).catch(() => setDetail(null)); else setDetail(null) }, [query.link])
  const preview = form.url ? tagUrl(form.url, form.utm) : ''
  const create = async (e) => {
    e.preventDefault()
    setBusy(true)
    try {
      const ln = await post('/api/links', { ...form, utm: showUtm ? form.utm : {} })
      setForm({ url: '', code: '', title: '', campaign: form.campaign, utm: form.utm }); load(); copy(ln.short_url, 'Short link')
    } catch (e2) { toast(e2.message, 'err') } finally { setBusy(false) }
  }
  const remove = async (ln) => {
    if (!window.confirm(`Delete /l/${ln.code}? Anyone who clicks it will get "not found".`)) return
    try { await del(`/api/links/${ln.id}`); setQuery({ link: '' }); load() } catch (e) { toast(e.message, 'err') }
  }
  const saveDefaults = async (cid, body) => {
    try { setDefaults(await put(`/api/utm/defaults/${cid}`, body)); toast('UTM defaults saved') } catch (e) { toast(e.message, 'err') }
  }
  return (
    <div className="space-y-5">
      <Card className="p-5">
        <form onSubmit={create} className="space-y-3" data-testid="link-form">
          <div className="grid gap-3 md:grid-cols-[2fr_1fr_1fr_1fr_auto] md:items-end">
            <Field label="Long link"><Input required value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="https://huhbyte.ai/story" data-testid="link-url" /></Field>
            <Field label="Custom code" hint="Optional"><Input value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} placeholder="gpt6" data-testid="link-code" /></Field>
            <Field label="Title"><Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} data-testid="link-title" /></Field>
            <Field label="Campaign">
              <Input list="growth-campaigns" value={form.campaign} onChange={(e) => setForm({ ...form, campaign: e.target.value })} data-testid="link-campaign" />
              <datalist id="growth-campaigns">{campaigns.map((c) => <option key={c.id} value={c.name} />)}</datalist>
            </Field>
            <Button variant="primary" loading={busy} data-testid="link-create"><Link2 className="size-4" />Shorten</Button>
          </div>
          <label className="flex items-center gap-2 text-sm"><Switch checked={showUtm} onChange={setShowUtm} label="Add UTM tags" testid="link-utm-toggle" />Add UTM tags (UTM builder)</label>
          {showUtm && (
            <div className="space-y-2 rounded-2xl bg-lav-50 p-3" data-testid="utm-builder">
              <div className="grid gap-2 md:grid-cols-5">
                {UTM_FIELDS.map(([k, l]) => (
                  <Field key={k} label={`utm_${k}`}><Input value={form.utm[k] || ''} placeholder={l.toLowerCase()} onChange={(e) => setForm({ ...form, utm: { ...form.utm, [k]: e.target.value } })} data-testid={`utm-${k}`} /></Field>
                ))}
              </div>
              {preview && <p className="break-all font-mono text-xs text-ink-2" data-testid="utm-preview">{preview}</p>}
            </div>
          )}
        </form>
      </Card>

      <div className={cx('grid gap-5', detail && 'xl:grid-cols-[minmax(0,1fr)_380px]')}>
        <div className="min-w-0 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Input className="w-72" placeholder="Search links" value={query.q || ''} onChange={(e) => setQuery({ q: e.target.value })} aria-label="Search links" data-testid="links-search" />
            <Tabs value={query.source || ''} onChange={(v) => setQuery({ source: v })} items={[['', 'All'], ['manual', 'Manual'], ['utm', 'From posts'], ['bio', 'Bio page']]} testid="links-source" />
          </div>
          {rows === null ? <SkeletonList rows={3} /> : !rows.length ? (
            <Empty icon={Link2} title="No short links yet" testid="links-empty">Shorten a link above, turn on "shorten links" for posts, or add links to your bio page. Every click is counted here.</Empty>
          ) : (
            <div className="overflow-x-auto rounded-2xl border border-lav-100 bg-surface">
              <table className="w-full text-sm"><thead className="bg-lav-50/60 text-left text-xs text-muted"><tr><th className="px-4 py-2">Short link</th><th>Goes to</th><th>Campaign</th><th>Source</th><th className="pr-4 text-right">Clicks</th></tr></thead>
                <tbody>{rows.map((ln) => (
                  <tr key={ln.id} onClick={() => setQuery({ link: ln.id })} data-testid="link-row" className={cx('cursor-pointer border-t border-lav-50 hover:bg-lav-50/50', query.link === ln.id && 'bg-lav-50')}>
                    <td className="px-4 py-2.5"><div className="font-medium">/l/{ln.code}</div>{ln.title && <div className="text-xs text-muted">{ln.title}</div>}</td>
                    <td className="max-w-[320px] truncate text-muted" title={ln.url}>{ln.url}</td>
                    <td>{ln.campaign && <Badge tone="gray">{ln.campaign}</Badge>}</td>
                    <td><Badge tone={ln.source === 'bio' ? 'lav' : ln.source === 'utm' ? 'blue' : 'gray'}>{ln.source}</Badge></td>
                    <td className="pr-4 text-right font-semibold tabular-nums" data-testid="link-clicks">{fmt(ln.clicks)}</td>
                  </tr>))}</tbody></table>
            </div>
          )}
        </div>
        {detail && (
          <motion.aside initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} className="h-fit space-y-3 rounded-2xl border border-lav-200 bg-surface p-5 shadow-soft" data-testid="link-detail">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0"><h2 className="text-lg font-semibold">/l/{detail.code}</h2><p className="break-all text-xs text-muted">{detail.url}</p></div>
              <Button size="icon-sm" variant="ghost" aria-label="Close" onClick={() => setQuery({ link: '' })}>✕</Button>
            </div>
            <div className="flex gap-2"><Button data-testid="growth-copy" size="sm" onClick={() => copy(detail.short_url)}><Copy className="size-3.5" />Copy</Button>
              <Button data-testid="growth-open" size="sm" onClick={() => window.open(detail.short_url, '_blank')}><ExternalLink className="size-3.5" />Open</Button>
              <Button size="sm" variant="danger-ghost" onClick={() => remove(detail)} data-testid="link-delete"><Trash2 className="size-3.5" />Delete</Button></div>
            <Stat label="Clicks (all time)" value={fmt(detail.clicks)} />
            <Bars data={detail.daily} testid="link-chart" />
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">Where clicks came from</h3>
            {detail.referrers.length ? <ul className="space-y-1 text-sm">{detail.referrers.map((r) => <li key={r.referrer} className="flex justify-between"><span>{r.referrer}</span><span className="tabular-nums text-muted">{r.clicks}</span></li>)}</ul> : <p className="text-sm text-muted">No clicks in the last 30 days.</p>}
          </motion.aside>
        )}
      </div>

      {defaults && (
        <Card className="space-y-4 p-5" data-testid="utm-defaults">
          <div>
            <h2 className="font-semibold">UTM tags on your posts</h2>
            <p className="text-sm text-muted">Links in captions and first comments get these tags per channel at publish time, so your analytics show which network sent the traffic. Placeholders: {'{provider}'} {'{username}'} {'{campaign}'}.</p>
          </div>
          <div className="flex flex-wrap gap-6">
            <label className="flex items-center gap-2 text-sm"><Switch checked={!!defaults['*'].auto} onChange={(v) => saveDefaults('*', { auto: v })} label="Tag every post" testid="utm-auto" />Tag links in every post</label>
            <label className="flex items-center gap-2 text-sm"><Switch checked={!!defaults['*'].shorten} onChange={(v) => saveDefaults('*', { shorten: v })} label="Shorten" testid="utm-shorten" />Also swap them for tracked short links</label>
          </div>
          <UtmRow label="All channels" value={defaults['*']} onSave={(v) => saveDefaults('*', v)} testid="utm-row-all" />
          {channels.map((c) => <UtmRow key={c.id} label={`@${c.username}`} provider={c.provider} value={defaults[c.id] || {}} onSave={(v) => saveDefaults(c.id, v)} onReset={() => saveDefaults(c.id, { reset: true })} />)}
        </Card>
      )}
    </div>
  )
}

function UtmRow({ label, provider, value, onSave, onReset, testid }) {
  const [v, setV] = useState(value)
  useEffect(() => setV(value), [JSON.stringify(value)])
  return (
    <div className="grid items-end gap-2 md:grid-cols-[140px_repeat(5,1fr)_auto]" data-testid={testid || 'utm-row'}>
      <div className="flex items-center gap-2 pb-2 text-sm font-medium">{provider && <NetIcon provider={provider} size="sm" />}{label}</div>
      {UTM_FIELDS.map(([k, , ph]) => <Input key={k} aria-label={`${label} utm_${k}`} value={v[k] || ''} placeholder={provider ? '(default)' : ph || k} onChange={(e) => setV({ ...v, [k]: e.target.value })} />)}
      <div className="flex gap-1"><Button data-testid="growth-save" size="sm" onClick={() => onSave(Object.fromEntries(UTM_FIELDS.map(([k]) => [k, v[k] || ''])))}>Save</Button>{onReset && <Button data-testid="growth-reset" size="sm" variant="ghost" onClick={onReset}>Reset</Button>}</div>
    </div>
  )
}

// ================================================================== campaigns
const PALETTE = ['#7C5CE0', '#C9ACFA', '#F59E0B', '#10B981', '#EC4899', '#3B82F6', '#EF4444', '#14B8A6']

function CampaignsTab() {
  const toast = useToast()
  const { query } = useRoute()
  const [rows, setRows] = useState(null)
  const [edit, setEdit] = useState(null)
  const [detail, setDetail] = useState(null)
  const load = () => get('/api/campaigns').then(setRows).catch((e) => toast(e.message, 'err'))
  useEffect(() => { load() }, [])
  useEffect(() => { if (query.campaign) get(`/api/campaigns/${encodeURIComponent(query.campaign)}`).then(setDetail).catch(() => setDetail(null)); else setDetail(null) }, [query.campaign, rows])
  const save = async () => {
    try {
      const body = { name: edit.name, color: edit.color, description: edit.description || '', start_date: edit.start_date || '', end_date: edit.end_date || '' }
      if (edit.id) await patch(`/api/campaigns/${edit.id}`, body); else await post('/api/campaigns', body)
      setEdit(null); load(); toast('Campaign saved')
    } catch (e) { toast(e.message, 'err') }
  }
  const remove = async (c) => {
    if (!window.confirm(`Delete "${c.name}"? Its posts are kept, just untagged.`)) return
    try { await del(`/api/campaigns/${c.id}`); setQuery({ campaign: '' }); load() } catch (e) { toast(e.message, 'err') }
  }
  return (
    <div className="space-y-4">
      <div className="flex justify-between"><p className="text-sm text-muted">Group posts across channels, see how each push did, and filter Publish and Calendar by campaign.</p>
        <Button variant="primary" onClick={() => setEdit({ name: '', color: PALETTE[(rows?.length || 0) % PALETTE.length] })} data-testid="campaign-new"><Plus className="size-4" />New campaign</Button></div>
      {rows === null ? <SkeletonList rows={2} /> : !rows.length ? (
        <Empty icon={Megaphone} title="No campaigns yet" testid="campaigns-empty">Create one, then pick it in the composer. Posts, clicks and engagement add up here.</Empty>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {rows.map((c) => (
            <motion.button layout key={c.id} onClick={() => setQuery({ campaign: c.id })} data-testid="campaign-card"
              className={cx('cursor-pointer rounded-2xl border bg-surface p-4 text-left shadow-soft transition hover:border-lav-400', query.campaign === c.id ? 'border-lav-500' : 'border-lav-200/70')}>
              <div className="flex items-center gap-2"><span className="size-3 rounded-full" style={{ background: c.color }} /><span className="font-semibold">{c.name}</span>
                <Badge tone={c.state === 'active' ? 'green' : c.state === 'upcoming' ? 'blue' : 'gray'} className="ml-auto">{c.state}</Badge></div>
              {(c.start_date || c.end_date) && <p className="mt-1 text-xs text-muted">{c.start_date || '…'} → {c.end_date || '…'}</p>}
              <div className="mt-3 grid grid-cols-4 gap-2 text-center text-xs">
                {[['Posts', c.stats.posts], ['Sent', c.stats.sent], ['Views', c.stats.views], ['Clicks', c.stats.clicks]].map(([l, v]) => (
                  <div key={l} className="rounded-xl bg-lav-50 py-2"><div className="text-base font-semibold tabular-nums">{fmt(v)}</div><div className="text-muted">{l}</div></div>
                ))}
              </div>
            </motion.button>
          ))}
        </div>
      )}
      {detail && (
        <Card className="space-y-4 p-5" data-testid="campaign-detail">
          <div className="flex flex-wrap items-center gap-2"><span className="size-3 rounded-full" style={{ background: detail.color }} /><h2 className="text-lg font-semibold">{detail.name}</h2>
            <span className="ml-auto flex gap-2">
              <Button data-testid="growth-open-in-publish" size="sm" onClick={() => go('/queue', { campaign: detail.name })}>Open in Publish</Button>
              <Button size="sm" onClick={() => setEdit(detail)} data-testid="campaign-edit">Edit</Button>
              <Button size="sm" variant="danger-ghost" onClick={() => remove(detail)} data-testid="campaign-delete"><Trash2 className="size-3.5" />Delete</Button>
              <Button data-testid="growth-close" size="sm" variant="ghost" onClick={() => setQuery({ campaign: '' })}>Close</Button></span></div>
          {detail.description && <p className="text-sm text-muted">{detail.description}</p>}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
            <Stat label="Posts" value={fmt(detail.stats.posts)} /><Stat label="Scheduled" value={fmt(detail.stats.scheduled)} /><Stat label="Sent" value={fmt(detail.stats.sent)} />
            <Stat label="Views" value={fmt(detail.stats.views)} /><Stat label="Engagements" value={fmt(detail.stats.engagements)} /><Stat label="Link clicks" value={fmt(detail.stats.clicks)} testid="campaign-clicks" />
          </div>
          {detail.channels.length > 0 && (
            <table className="w-full text-sm"><thead className="text-left text-xs text-muted"><tr><th className="py-1">Channel</th><th>Posts</th><th>Sent</th><th>Views</th><th>Likes</th></tr></thead>
              <tbody>{detail.channels.map((r) => <tr key={r.channel?.id || r.channel?.username} className="border-t border-lav-50"><td className="py-1.5">@{r.channel?.username}</td><td>{r.posts}</td><td>{r.sent}</td><td>{fmt(r.views)}</td><td>{fmt(r.likes)}</td></tr>)}</tbody></table>
          )}
          <div className="space-y-1.5">
            {detail.targets.slice(0, 30).map((t) => (
              <div key={t.id} className="flex items-center gap-2 rounded-xl border border-lav-100 px-3 py-2 text-sm">
                <Badge tone={t.status === 'sent' ? 'green' : t.status === 'scheduled' ? 'blue' : 'gray'}>{t.status}</Badge>
                <span className="min-w-0 flex-1 truncate">{t.caption || '(no caption)'}</span><span className="text-xs text-muted">@{t.channel?.username}</span>
              </div>
            ))}
          </div>
        </Card>
      )}
      <Dialog open={!!edit} onClose={() => setEdit(null)} title={edit?.id ? 'Edit campaign' : 'New campaign'} testid="campaign-dialog"
        footer={<><Button data-testid="growth-cancel" variant="ghost" onClick={() => setEdit(null)}>Cancel</Button><Button variant="primary" onClick={save} data-testid="campaign-save">Save</Button></>}>
        {edit && (
          <div className="space-y-3">
            <Field label="Name"><Input autoFocus value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} data-testid="campaign-name" /></Field>
            <Field label="Colour"><div className="flex gap-2">{PALETTE.map((c) => <button key={c} type="button" aria-label={c} onClick={() => setEdit({ ...edit, color: c })} className={cx('size-8 cursor-pointer rounded-full border-2', edit.color === c ? 'border-ink' : 'border-transparent')} style={{ background: c }} />)}</div></Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Starts"><Input type="date" value={edit.start_date || ''} onChange={(e) => setEdit({ ...edit, start_date: e.target.value })} /></Field>
              <Field label="Ends"><Input type="date" value={edit.end_date || ''} onChange={(e) => setEdit({ ...edit, end_date: e.target.value })} /></Field>
            </div>
            <Field label="Description"><Textarea rows={2} value={edit.description || ''} onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></Field>
          </div>
        )}
      </Dialog>
    </div>
  )
}

// ================================================================== hashtags
function HashtagsTab() {
  const toast = useToast()
  const copy = useCopy()
  const { reload } = useStore()
  const [groups, setGroups] = useState(null)
  const [stats, setStats] = useState(null)
  const [edit, setEdit] = useState(null)
  const [caption, setCaption] = useState('')
  const [sug, setSug] = useState(null)
  const load = () => {
    get('/api/hashtags/groups').then(setGroups).catch((e) => toast(e.message, 'err'))
    get('/api/hashtags/stats?days=90').then(setStats).catch(() => {})
  }
  useEffect(load, [])
  const save = async () => {
    try {
      if (edit.id) await patch(`/api/hashtags/groups/${edit.id}`, { name: edit.name, text: edit.text }); else await post('/api/hashtags/groups', { name: edit.name, text: edit.text })
      setEdit(null); load(); reload(); toast('Group saved')
    } catch (e) { toast(e.message, 'err') }
  }
  const remove = async (g) => {
    if (!window.confirm(`Delete the "${g.name}" group?`)) return
    try { await del(`/api/hashtags/groups/${g.id}`); load(); reload() } catch (e) { toast(e.message, 'err') }
  }
  const suggest = async () => { try { setSug(await get('/api/hashtags/suggest?' + new URLSearchParams({ caption }))) } catch (e) { toast(e.message, 'err') } }
  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
      <div className="min-w-0 space-y-4">
        <div className="flex items-center justify-between"><h2 className="font-semibold">Hashtag groups</h2>
          <Button variant="primary" onClick={() => setEdit({ name: '', text: '' })} data-testid="hashtag-new"><Plus className="size-4" />New group</Button></div>
        {groups === null ? <SkeletonList rows={2} /> : !groups.length ? <Empty icon={Hash} title="No hashtag groups" testid="hashtags-empty">Save sets you reuse; insert them from the composer's Hashtags button.</Empty> : (
          <div className="grid gap-3 md:grid-cols-2">
            {groups.map((g) => (
              <Card key={g.id} className="space-y-2 p-4" data-testid="hashtag-group">
                <div className="flex items-center gap-2"><span className="font-semibold">{g.name}</span><Badge tone="gray">{g.count}</Badge>
                  {g.avg_score !== null && <Badge tone="green" title="Average engagement score of sent posts using these tags">★ {g.avg_score}</Badge>}
                  <span className="ml-auto flex gap-0.5">
                    <Button size="icon-sm" variant="ghost" aria-label="Copy hashtags" onClick={() => copy(g.text, 'Hashtags')}><Copy className="size-3.5" /></Button>
                    <Button size="icon-sm" variant="ghost" aria-label="Edit group" onClick={() => setEdit(g)} data-testid="hashtag-edit"><Wand2 className="size-3.5" /></Button>
                    <Button size="icon-sm" variant="danger-ghost" aria-label="Delete group" onClick={() => remove(g)} data-testid="hashtag-delete"><Trash2 className="size-3.5" /></Button></span></div>
                <div className="flex flex-wrap gap-1">{g.tags.map((t) => <span key={t} className="rounded-full bg-lav-50 px-2 py-0.5 text-xs text-lav-700">{t}</span>)}</div>
                <p className="text-xs text-muted">Used {g.uses} times in the last 90 days</p>
              </Card>
            ))}
          </div>
        )}
        <h2 className="pt-2 font-semibold">Hashtag performance <span className="text-xs font-normal text-muted">(last 90 days)</span></h2>
        {stats === null ? <SkeletonList rows={1} /> : !stats.length ? <p className="text-sm text-muted">No hashtags in recent posts yet.</p> : (
          <div className="overflow-x-auto rounded-2xl border border-lav-100 bg-surface">
            <table className="w-full text-sm" data-testid="hashtag-stats"><thead className="bg-lav-50/60 text-left text-xs text-muted"><tr><th className="px-4 py-2">Hashtag</th><th>Uses</th><th>Sent</th><th>Avg views</th><th>Avg engagements</th><th>Networks</th><th>Last used</th></tr></thead>
              <tbody>{stats.slice(0, 100).map((r) => (
                <tr key={r.tag} className="border-t border-lav-50"><td className="px-4 py-2 font-medium">{r.tag}</td><td>{r.uses}</td><td>{r.sent}</td><td>{fmt(r.avg_views)}</td><td>{fmt(r.avg_engagements)}</td>
                  <td><span className="flex gap-1">{r.networks.map((n) => <NetIcon key={n} provider={n} size="sm" />)}</span></td><td className="text-muted">{r.last_used ? new Date(r.last_used).toLocaleDateString() : ''}</td></tr>))}</tbody></table>
          </div>
        )}
      </div>
      <Card className="h-fit space-y-3 p-4" data-testid="hashtag-suggest">
        <h3 className="flex items-center gap-2 font-semibold"><Sparkles className="size-4 text-lav-600" />Suggest for a caption</h3>
        <Textarea rows={4} value={caption} onChange={(e) => setCaption(e.target.value)} placeholder="Paste a caption…" data-testid="hashtag-suggest-caption" />
        <Button onClick={suggest} data-testid="hashtag-suggest-run">Suggest hashtags</Button>
        {sug && (
          <div className="space-y-2">
            {!sug.tags.length && <p className="text-sm text-muted">Nothing to suggest yet; suggestions come from hashtags in your own sent posts.</p>}
            <div className="flex flex-wrap gap-1">{sug.tags.map((t) => <button key={t.tag} onClick={() => setCaption((c) => `${c.trimEnd()} ${t.tag}`)} className="cursor-pointer rounded-full bg-lav-100 px-2 py-0.5 text-xs text-lav-700 hover:bg-lav-200" data-testid="hashtag-suggestion">{t.tag}</button>)}</div>
            {sug.groups.length > 0 && <p className="text-xs text-muted">Matching groups: {sug.groups.map((g) => g.name).join(', ')}</p>}
            {sug.tags.length > 0 && <Button data-testid="growth-copy-caption" size="sm" onClick={() => copy(caption, 'Caption')}><Copy className="size-3.5" />Copy caption</Button>}
          </div>
        )}
      </Card>
      <Dialog open={!!edit} onClose={() => setEdit(null)} title={edit?.id ? 'Edit hashtag group' : 'New hashtag group'} testid="hashtag-dialog"
        footer={<><Button data-testid="growth-cancel" variant="ghost" onClick={() => setEdit(null)}>Cancel</Button><Button variant="primary" onClick={save} data-testid="hashtag-save">Save</Button></>}>
        {edit && (
          <div className="space-y-3">
            <Field label="Name"><Input autoFocus value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} data-testid="hashtag-name" /></Field>
            <Field label="Hashtags" hint="Spaces, commas or new lines; the # is added for you"><Textarea rows={4} value={edit.text} onChange={(e) => setEdit({ ...edit, text: e.target.value })} data-testid="hashtag-text" /></Field>
          </div>
        )}
      </Dialog>
    </div>
  )
}

// ================================================================== automations
const KIND = {
  first_comment: { label: 'Auto first comment', icon: MessageSquare, blurb: 'Add the same first comment (sources, CTA, hashtags) to every post on a channel.' },
  evergreen: { label: 'Recycle evergreen posts', icon: Recycle, blurb: 'Re-queue your best old posts after a gap, a limited number of times.' },
  recurring: { label: 'Recurring post', icon: Repeat, blurb: 'Post the same thing every day, week or month.' },
  rss: { label: 'RSS feed', icon: Rss, blurb: 'Turn new items from a blog or news feed into drafts or queued posts.' },
}
const DEFAULT_CFG = {
  first_comment: { text: 'Sources and more in the link in bio', mode: 'if_empty' },
  evergreen: { min_age_days: 30, max_recycles: 2, per_run: 1, every_days: 7, when: 'queue' },
  recurring: { caption: '', frequency: 'weekly', weekdays: [0], time: '09:00', timezone: 'Europe/Dublin', interval: 1 },
  rss: { url: '', mode: 'draft', template: '{title}\n\n{link}', max_per_run: 3, every_minutes: 60, backfill: 0 },
}
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

function describe(a) {
  const c = a.config
  if (a.kind === 'first_comment') return `"${c.text}"${c.mode === 'append' ? ' (added after your own)' : ' (when none is written)'}`
  if (a.kind === 'evergreen') return `Posts older than ${c.min_age_days} days, up to ${c.max_recycles}× each, ${c.per_run} per channel every ${c.every_days} days`
  if (a.kind === 'recurring') return `${c.frequency === 'weekly' ? `Every ${c.interval > 1 ? c.interval + ' weeks' : 'week'} on ${(c.weekdays || []).map((d) => DAYS[d]).join(', ')}` : c.frequency === 'daily' ? `Every ${c.interval > 1 ? c.interval + ' days' : 'day'}` : `Monthly`} at ${c.time} (${c.timezone})`
  return `${c.url} → ${c.mode === 'queue' ? 'queue' : 'drafts'}, checked every ${c.every_minutes} min`
}

function AutomationsTab() {
  const toast = useToast()
  const { channels } = useStore()
  const [rows, setRows] = useState(null)
  const [edit, setEdit] = useState(null)
  const [prev, setPrev] = useState(null)
  const load = () => get('/api/automations').then(setRows).catch((e) => toast(e.message, 'err'))
  useEffect(() => { load() }, [])
  const save = async () => {
    try {
      const body = { name: edit.name, channel_ids: edit.channel_ids, config: edit.config }
      if (edit.id) await patch(`/api/automations/${edit.id}`, body); else await post('/api/automations', { ...body, kind: edit.kind })
      setEdit(null); load(); toast('Automation saved')
    } catch (e) { toast(e.message, 'err') }
  }
  const act = async (fn, msg) => { try { const r = await fn(); load(); if (msg) toast(typeof msg === 'function' ? msg(r) : msg) } catch (e) { toast(e.message, 'err') } }
  const setCfg = (c) => setEdit({ ...edit, config: { ...edit.config, ...c } })
  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {Object.entries(KIND).map(([k, v]) => (
          <button key={k} onClick={() => setEdit({ kind: k, name: '', channel_ids: channels.slice(0, 1).map((c) => c.id), config: { ...DEFAULT_CFG[k] } })} data-testid={`automation-new-${k}`}
            className="group cursor-pointer rounded-2xl border border-dashed border-lav-300 bg-surface p-4 text-left transition hover:border-lav-500 hover:shadow-soft">
            <div className="flex items-center gap-2 font-semibold"><v.icon className="size-4 text-lav-600" />{v.label}<Plus className="ml-auto size-4 text-muted group-hover:text-lav-600" /></div>
            <p className="mt-1 text-xs text-muted">{v.blurb}</p>
          </button>
        ))}
      </div>
      {rows === null ? <SkeletonList rows={2} /> : !rows.length ? <Empty icon={Repeat} title="No automations yet" testid="automations-empty">Pick one above. They run in the background with the publisher.</Empty> : (
        <div className="space-y-2">
          {rows.map((a) => {
            const K = KIND[a.kind]
            return (
              <Card key={a.id} className="flex flex-wrap items-center gap-3 p-4" data-testid="automation-row">
                <div className="grid size-10 place-items-center rounded-xl bg-lav-100 text-lav-700"><K.icon className="size-5" /></div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 font-medium">{a.name}<Badge tone={a.active ? 'green' : 'gray'}>{a.active ? 'On' : 'Paused'}</Badge></div>
                  <p className="truncate text-xs text-muted">{describe(a)}</p>
                  <p className="text-xs text-muted">{a.channel_ids.map((id) => channelName(channels, id)).join(', ')}
                    {a.next_run_at && ` · next run ${when(a.next_run_at)}`}
                    {a.state.applied ? ` · applied to ${a.state.applied} posts` : ''}{a.state.total ? ` · ${a.state.total} recycled` : ''}{a.state.posted ? ` · ${a.state.posted} posted` : ''}
                    {a.state.last_error && <span className="text-rose-600"> · {a.state.last_error}</span>}</p>
                </div>
                <span className="flex gap-1">
                  <Button size="sm" variant="ghost" onClick={() => get(`/api/automations/${a.id}/preview`).then((r) => setPrev({ a, ...r })).catch((e) => toast(e.message, 'err'))} data-testid="automation-preview">Preview</Button>
                  <Button size="sm" onClick={() => act(() => post(`/api/automations/${a.id}/run`), (r) => (a.kind === 'first_comment' ? `Updated ${r.updated || 0} queued posts` : `Created ${r.created || 0} posts`))} data-testid="automation-run"><Play className="size-3.5" />Run now</Button>
                  <Button size="sm" variant="ghost" onClick={() => act(() => patch(`/api/automations/${a.id}`, { active: !a.active }), a.active ? 'Paused' : 'Resumed')} data-testid="automation-toggle">{a.active ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}{a.active ? 'Pause' : 'Resume'}</Button>
                  <Button size="sm" variant="ghost" onClick={() => setEdit({ ...a, config: { ...a.config } })} data-testid="automation-edit">Edit</Button>
                  <Button size="icon-sm" variant="danger-ghost" aria-label="Delete automation" onClick={() => window.confirm(`Delete "${a.name}"?`) && act(() => del(`/api/automations/${a.id}`), 'Deleted')} data-testid="automation-delete"><Trash2 className="size-3.5" /></Button>
                </span>
              </Card>
            )
          })}
        </div>
      )}
      <Dialog open={!!prev} onClose={() => setPrev(null)} title={`Preview: ${prev?.a.name}`} testid="automation-preview-dialog">
        {prev && (!prev.items?.length ? <p className="text-sm text-muted">Nothing to do right now.</p> : (
          <ul className="max-h-96 space-y-2 overflow-y-auto text-sm">{prev.items.map((it, i) => (
            <li key={i} className="rounded-xl border border-lav-100 p-2">
              {it.at && when(it.at)}{it.title && <><b>{it.title}</b>{it.new === false && <Badge tone="gray" className="ml-2">seen</Badge>}</>}
              {it.caption && <p className="text-muted">{it.caption}</p>}{it.first_comment && <p className="text-xs">💬 {it.first_comment}</p>}{it.score !== undefined && <p className="text-xs text-muted">score {it.score} · recycled {it.recycled}×</p>}
            </li>))}</ul>
        ))}
      </Dialog>
      <Dialog open={!!edit} onClose={() => setEdit(null)} title={edit ? `${edit.id ? 'Edit' : 'New'}: ${KIND[edit.kind].label}` : ''} testid="automation-dialog"
        footer={<><Button data-testid="growth-cancel" variant="ghost" onClick={() => setEdit(null)}>Cancel</Button><Button variant="primary" onClick={save} data-testid="automation-save">Save</Button></>}>
        {edit && (
          <div className="space-y-3">
            <Field label="Name"><Input value={edit.name} placeholder={KIND[edit.kind].label} onChange={(e) => setEdit({ ...edit, name: e.target.value })} data-testid="automation-name" /></Field>
            <Field label="Channels"><ChannelPicker value={edit.channel_ids} onChange={(v) => setEdit({ ...edit, channel_ids: v })} testid="automation-channels" /></Field>
            {edit.kind === 'first_comment' && (<>
              <Field label="First comment" hint="{campaign} is replaced with the post's campaign. Links get your UTM tags."><Textarea rows={3} value={edit.config.text} onChange={(e) => setCfg({ text: e.target.value })} data-testid="automation-fc-text" /></Field>
              <Field label="When the post already has a first comment"><Select data-testid="automation-fc-mode" value={edit.config.mode} onChange={(e) => setCfg({ mode: e.target.value })} options={[['if_empty', 'Keep theirs (skip)'], ['append', 'Add this after it']]} /></Field>
              <div className="grid grid-cols-2 gap-3"><Field label="Only posts tagged" hint="Optional"><Input data-testid="automation-fc-tag" value={edit.config.tag || ''} onChange={(e) => setCfg({ tag: e.target.value })} /></Field>
                <Field label="Only campaign" hint="Optional"><Input data-testid="automation-fc-campaign" value={edit.config.campaign || ''} onChange={(e) => setCfg({ campaign: e.target.value })} /></Field></div>
              <label className="flex items-center gap-2 text-sm"><Switch checked={!!edit.config.video_only} onChange={(v) => setCfg({ video_only: v })} label="Videos only" />Only on video posts</label>
            </>)}
            {edit.kind === 'evergreen' && (
              <div className="grid grid-cols-2 gap-3">
                <Field label="Older than (days)"><Input type="number" min={1} value={edit.config.min_age_days} onChange={(e) => setCfg({ min_age_days: Number(e.target.value) })} data-testid="automation-ev-age" /></Field>
                <Field label="Recycle each post at most"><Input data-testid="automation-ev-max" type="number" min={1} max={50} value={edit.config.max_recycles} onChange={(e) => setCfg({ max_recycles: Number(e.target.value) })} /></Field>
                <Field label="Posts per channel per run"><Input data-testid="automation-ev-per-run" type="number" min={1} max={10} value={edit.config.per_run} onChange={(e) => setCfg({ per_run: Number(e.target.value) })} /></Field>
                <Field label="Run every (days)"><Input data-testid="automation-ev-every" type="number" min={1} max={90} value={edit.config.every_days} onChange={(e) => setCfg({ every_days: Number(e.target.value) })} /></Field>
                <Field label="Only posts tagged" hint="Optional, e.g. evergreen"><Input data-testid="automation-ev-tag" value={edit.config.tag || ''} onChange={(e) => setCfg({ tag: e.target.value })} /></Field>
                <Field label="Add as"><Select data-testid="automation-ev-when" value={edit.config.when} onChange={(e) => setCfg({ when: e.target.value })} options={[['queue', 'Next free queue slot'], ['draft', 'Draft for review']]} /></Field>
              </div>
            )}
            {edit.kind === 'recurring' && (<>
              <Field label="Caption"><Textarea rows={3} value={edit.config.caption} onChange={(e) => setCfg({ caption: e.target.value })} data-testid="automation-rec-caption" /></Field>
              <div className="grid grid-cols-3 gap-3">
                <Field label="Repeat"><Select data-testid="automation-rec-frequency" value={edit.config.frequency} onChange={(e) => setCfg({ frequency: e.target.value })} options={[['daily', 'Daily'], ['weekly', 'Weekly'], ['monthly', 'Monthly']]} /></Field>
                <Field label="Every"><Input data-testid="automation-rec-interval" type="number" min={1} max={52} value={edit.config.interval || 1} onChange={(e) => setCfg({ interval: Number(e.target.value) })} /></Field>
                <Field label="At"><Input data-testid="automation-rec-time" type="time" value={edit.config.time} onChange={(e) => setCfg({ time: e.target.value })} /></Field>
              </div>
              {edit.config.frequency === 'weekly' && (
                <div className="flex gap-1">{DAYS.map((d, i) => {
                  const on = (edit.config.weekdays || []).includes(i)
                  return <button data-testid="automation-rec-weekday" key={d} type="button" onClick={() => setCfg({ weekdays: on ? edit.config.weekdays.filter((x) => x !== i) : [...(edit.config.weekdays || []), i] })} className={cx('h-9 w-11 cursor-pointer rounded-lg border text-xs', on ? 'border-lav-500 bg-lav-100 font-semibold' : 'border-lav-200')}>{d}</button>
                })}</div>
              )}
              {edit.config.frequency === 'monthly' && <Field label="Day of month"><Input data-testid="automation-rec-day" type="number" min={1} max={31} value={edit.config.day_of_month || 1} onChange={(e) => setCfg({ day_of_month: Number(e.target.value) })} /></Field>}
              <div className="grid grid-cols-2 gap-3"><Field label="Timezone"><Input data-testid="automation-rec-timezone" value={edit.config.timezone} onChange={(e) => setCfg({ timezone: e.target.value })} /></Field>
                <Field label="Until" hint="Optional"><Input data-testid="automation-rec-until" type="date" value={edit.config.end_date || ''} onChange={(e) => setCfg({ end_date: e.target.value })} /></Field></div>
            </>)}
            {edit.kind === 'rss' && (<>
              <Field label="Feed URL"><Input value={edit.config.url} onChange={(e) => setCfg({ url: e.target.value })} placeholder="https://openai.com/news/rss.xml" data-testid="automation-rss-url" /></Field>
              <Field label="Caption template" hint="{title} {link} {summary} {feed}"><Textarea data-testid="automation-rss-template" rows={3} value={edit.config.template} onChange={(e) => setCfg({ template: e.target.value })} /></Field>
              <div className="grid grid-cols-3 gap-3">
                <Field label="Add as"><Select data-testid="automation-rss-mode" value={edit.config.mode} onChange={(e) => setCfg({ mode: e.target.value })} options={[['draft', 'Drafts'], ['queue', 'Queue']]} /></Field>
                <Field label="Check every (min)"><Input data-testid="automation-rss-every" type="number" min={15} value={edit.config.every_minutes} onChange={(e) => setCfg({ every_minutes: Number(e.target.value) })} /></Field>
                <Field label="Post newest on start"><Input data-testid="automation-rss-backfill" type="number" min={0} max={20} value={edit.config.backfill} onChange={(e) => setCfg({ backfill: Number(e.target.value) })} /></Field>
              </div>
            </>)}
          </div>
        )}
      </Dialog>
    </div>
  )
}

// ================================================================== CSV import
function ImportTab() {
  const toast = useToast()
  const { settings } = useStore()
  const fileRef = useRef(null)
  const [csv, setCsv] = useState('')
  const [opts, setOpts] = useState({ channel_ids: [], when: '', timezone: settings?.timezone || 'Europe/Dublin' })
  const [pre, setPre] = useState(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(null)
  const body = useMemo(() => ({ csv, ...opts, when: opts.when || undefined }), [csv, opts])
  const check = async () => { setBusy(true); setDone(null); try { setPre(await post('/api/import/csv/preview', body)) } catch (e) { toast(e.message, 'err'); setPre(null) } finally { setBusy(false) } }
  const run = async () => {
    setBusy(true)
    try { const r = await post('/api/import/csv', body); setDone(r); setPre(null); toast(`Imported ${r.imported} posts`) } catch (e) { toast(e.message, 'err') } finally { setBusy(false) }
  }
  const onFile = async (f) => { if (!f) return; setCsv(await f.text()); setPre(null); setDone(null) }
  return (
    <div className="space-y-4">
      <Card className="space-y-3 p-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div><h2 className="font-semibold">Bulk import posts from a spreadsheet</h2>
            <p className="text-sm text-muted">Columns: caption, channels (e.g. instagram|youtube or @handle), scheduled_at, media_urls, title, format, tags, campaign, topic_key, first_comment. Up to 500 rows.</p></div>
          <span className="flex gap-2">
            <a href="/api/import/csv/template" download className="inline-flex h-8 items-center gap-1.5 rounded-xl border border-lav-200 px-3 text-[13px] font-medium hover:bg-lav-50" data-testid="import-template"><Download className="size-3.5" />Template</a>
            <Button size="sm" onClick={() => fileRef.current?.click()} data-testid="import-file-button"><Upload className="size-3.5" />Choose CSV</Button>
            <input ref={fileRef} type="file" accept=".csv,text/csv,text/plain" className="hidden" onChange={(e) => onFile(e.target.files?.[0])} data-testid="import-file" />
          </span>
        </div>
        <Textarea rows={7} className="font-mono text-xs" value={csv} onChange={(e) => { setCsv(e.target.value); setPre(null) }} placeholder="caption,channels,scheduled_at&#10;GPT-6 is here,instagram,2026-11-03 09:30" data-testid="import-csv" />
        <div className="grid gap-3 md:grid-cols-[1fr_200px_200px]">
          <Field label="Default channels" hint="For rows without a channels column"><ChannelPicker value={opts.channel_ids} onChange={(v) => setOpts({ ...opts, channel_ids: v })} testid="import-channels" /></Field>
          <Field label="Rows without a date"><Select value={opts.when} onChange={(e) => setOpts({ ...opts, when: e.target.value })} options={[['', 'Add to queue'], ['draft', 'Save as drafts'], ['queue', 'Queue (all rows)']]} data-testid="import-when" /></Field>
          <Field label="Timezone of the dates"><Input value={opts.timezone} onChange={(e) => setOpts({ ...opts, timezone: e.target.value })} data-testid="import-timezone" /></Field>
        </div>
        <div className="flex gap-2">
          <Button onClick={check} loading={busy && !pre} disabled={!csv.trim()} data-testid="import-preview">Check rows</Button>
          {pre && <Button variant="primary" onClick={run} loading={busy} disabled={!pre.valid} data-testid="import-run">Import {pre.valid} valid row{pre.valid === 1 ? '' : 's'}</Button>}
        </div>
      </Card>
      {pre && (
        <Card className="space-y-3 p-5" data-testid="import-result">
          <div className="flex flex-wrap gap-2 text-sm"><Badge tone="green">{pre.valid} ready</Badge>{pre.invalid > 0 && <Badge tone="red">{pre.invalid} with errors</Badge>}
            {pre.unknown_columns.length > 0 && <span className="text-xs text-muted">Ignored columns: {pre.unknown_columns.join(', ')}</span>}</div>
          <div className="overflow-x-auto"><table className="w-full text-sm"><thead className="text-left text-xs text-muted"><tr><th className="py-1">Row</th><th>Caption</th><th>Channels</th><th>When</th><th>Media</th><th>Issues</th></tr></thead>
            <tbody>{pre.rows.map((r) => (
              <tr key={r.row} className={cx('border-t border-lav-50 align-top', !r.ok && 'bg-rose-50/50')} data-testid="import-row">
                <td className="py-1.5">{r.row}</td><td className="max-w-[260px] truncate">{r.caption}</td><td>{r.channels.map((c) => `@${c.username}`).join(', ')}</td>
                <td className="whitespace-nowrap">{r.when === 'custom' ? when(r.scheduled_at) : r.when}</td><td>{r.media.length || ''}</td>
                <td className="text-xs">{r.errors.map((e) => <div key={e} className="text-rose-700">{e}</div>)}{r.warnings.map((w) => <div key={w} className="text-amber-700">{w}</div>)}</td></tr>))}</tbody></table></div>
        </Card>
      )}
      {done && (
        <Card className="space-y-2 p-5" data-testid="import-done">
          <p className="font-medium">{done.imported} imported{done.failed ? `, ${done.failed} skipped` : ''}.</p>
          {done.results.filter((r) => !r.ok).map((r) => <p key={r.row} className="text-xs text-rose-700">Row {r.row}: {r.error}</p>)}
          <Button data-testid="growth-open-the-queue" size="sm" onClick={() => go('/queue')}>Open the queue</Button>
        </Card>
      )}
    </div>
  )
}
