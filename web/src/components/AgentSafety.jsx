// Agent safety UI: the kill switch (card + app-wide banner), per-token limits and the filterable audit log.
// The server enforces everything (huhqueue/safety.py); this only shows state and edits it.
import { useEffect, useState } from 'react'
import { Download, OctagonPause, Play, ShieldAlert } from 'lucide-react'
import { get, patch, post } from '../api'
import { useStore } from '../store'
import { Badge, Button, Card, ChannelAvatar, cx, Dialog, Field, Input, Select, Switch, promptAsync, useToast } from '../ui'

export function useAgentSafety(poll = 0) {
  const [st, setSt] = useState(null)
  const load = () => get('/api/agents/safety').then(setSt).catch(() => {})
  useEffect(() => {
    load()
    const on = () => load()
    window.addEventListener('huhq:agents-changed', on)
    const i = poll ? setInterval(load, poll) : null
    return () => { window.removeEventListener('huhq:agents-changed', on); if (i) clearInterval(i) }
  }, [])
  return [st, load]
}

const changed = () => window.dispatchEvent(new Event('huhq:agents-changed'))

export async function pauseAgents(toast) {
  const reason = await promptAsync('Pause every agent and API token?', { ok: 'Pause agents', placeholder: 'Reason (optional, shown to agents)' })
  if (reason === null) return
  try { await post('/api/agents/pause', { reason }); toast('Agents paused'); changed() } catch (e) { toast(e.message, 'err') }
}

export async function resumeAgents(toast) {
  try { await post('/api/agents/resume'); toast('Agents resumed'); changed() } catch (e) { toast(e.message, 'err') }
}

export function AgentsBanner() {
  const [st] = useAgentSafety(30000)
  const toast = useToast()
  const { can } = useStore()
  if (!st?.paused) return null
  return (
    <div className="flex flex-wrap items-center gap-3 bg-rose-600 px-6 py-2 text-sm text-white" role="alert" data-testid="agents-paused-banner">
      <OctagonPause className="size-4 shrink-0" />
      <span><b>Agents are paused.</b> API tokens and MCP get HTTP 423 and posts agents made are held{st.reason ? `: “${st.reason}”` : ''}{st.paused_by ? ` (by ${st.paused_by})` : ''}.</span>
      {can.settings && <Button size="sm" variant="soft" className="ml-auto" onClick={() => resumeAgents(toast)} data-testid="agents-banner-resume"><Play className="size-3.5" />Resume agents</Button>}
    </div>
  )
}

export function KillSwitchCard() {
  const [st] = useAgentSafety()
  const toast = useToast()
  if (!st) return null
  return (
    <Card className={cx('flex flex-wrap items-center gap-4 p-6', st.paused && 'border-rose-300 bg-rose-50/60')} data-testid="kill-switch">
      <div className={cx('grid size-11 place-items-center rounded-2xl', st.paused ? 'bg-rose-600 text-white' : 'bg-lav-100 text-lav-700')}><ShieldAlert className="size-5" /></div>
      <div className="min-w-0 flex-1">
        <h3 className="font-semibold">Kill switch {st.paused ? <Badge tone="red">Agents paused</Badge> : <Badge tone="green">Agents running</Badge>}</h3>
        <p className="text-sm text-muted">{st.paused
          ? `Paused ${st.paused_at ? new Date(st.paused_at).toLocaleString() : ''}${st.paused_by ? ` by ${st.paused_by}` : ''}${st.reason ? ` - ${st.reason}` : ''}. Only a person can resume.`
          : 'One click stops every API token and MCP agent, and holds the posts they created until you resume.'}</p>
      </div>
      {st.paused
        ? <Button variant="primary" onClick={() => resumeAgents(toast)} data-testid="kill-switch-resume"><Play className="size-4" />Resume agents</Button>
        : <Button variant="danger" onClick={() => pauseAgents(toast)} data-testid="kill-switch-pause"><OctagonPause className="size-4" />Pause all agents</Button>}
    </Card>
  )
}

const EMPTY_LIMITS = { max_public_per_day: 0, max_actions_per_day: 0, allowed_channel_ids: [], expires_in_days: '', auto_confirm: false }

// Token limit fields, shared by the create form and the edit dialog. value/onChange carry the API payload keys.
export function TokenLimitFields({ value, onChange }) {
  const { channels } = useStore()
  const v = { ...EMPTY_LIMITS, ...value }
  const set = (k, x) => onChange({ ...v, [k]: x })
  const ids = v.allowed_channel_ids || []
  return (
    <div className="space-y-4" data-testid="token-limits">
      <div className="grid gap-4 md:grid-cols-3">
        <Field label="Public actions per day" hint="Schedules, publishes, replies. 0 = no cap">
          <Input type="number" min={0} value={v.max_public_per_day} onChange={(e) => set('max_public_per_day', Number(e.target.value))} data-testid="token-max-public" />
        </Field>
        <Field label="All changes per day" hint="0 = no cap">
          <Input type="number" min={0} value={v.max_actions_per_day} onChange={(e) => set('max_actions_per_day', Number(e.target.value))} data-testid="token-max-actions" />
        </Field>
        <Field label="Expires in (days)" hint="Empty = never">
          <Input type="number" min={1} value={v.expires_in_days ?? ''} onChange={(e) => set('expires_in_days', e.target.value === '' ? '' : Number(e.target.value))} data-testid="token-expires" />
        </Field>
      </div>
      <div>
        <div className="mb-2 text-[13px] font-medium text-ink-2">Channels <span className="text-muted">(none picked = every channel)</span></div>
        <div className="flex flex-wrap gap-2">{channels.map((c) => {
          const on = ids.includes(c.id)
          return <button key={c.id} type="button" aria-pressed={on} data-testid={`token-channel-${c.id}`} onClick={() => set('allowed_channel_ids', on ? ids.filter((x) => x !== c.id) : [...ids, c.id])}
            className={cx('flex cursor-pointer items-center gap-2 rounded-full border px-2 py-1 text-sm', on ? 'border-lav-600 bg-lav-100' : 'border-lav-200')}><ChannelAvatar ch={c} size={20} />@{c.username}</button>
        })}</div>
      </div>
      <label className="flex items-center gap-3 text-sm"><Switch checked={!!v.auto_confirm} onChange={(x) => set('auto_confirm', x)} label="Skip MCP preview" testid="token-auto-confirm" />Let MCP public actions run without the preview-and-confirm step</label>
    </div>
  )
}

export function limitsPayload(v) {
  const out = { ...EMPTY_LIMITS, ...v }
  if (out.expires_in_days === '' || out.expires_in_days === null) { delete out.expires_in_days; out.expires_at = null } else out.expires_in_days = Number(out.expires_in_days)
  return out
}

export function TokenLimitBadges({ t }) {
  return <>
    {t.expired ? <Badge tone="red">expired</Badge> : t.expires_at && <Badge tone="amber">expires {new Date(t.expires_at).toLocaleDateString()}</Badge>}
    {t.max_public_per_day > 0 && <Badge tone="lav">{t.used_today?.public ?? 0}/{t.max_public_per_day} public today</Badge>}
    {t.max_actions_per_day > 0 && <Badge tone="lav">{t.used_today?.actions ?? 0}/{t.max_actions_per_day} changes today</Badge>}
    {t.allowed_channel_ids?.length > 0 && <Badge tone="lav">{t.allowed_channel_ids.length} channel(s)</Badge>}
    {t.auto_confirm && <Badge tone="amber">no MCP preview</Badge>}
  </>
}

export function EditLimitsDialog({ token, onClose, onSaved }) {
  const toast = useToast()
  const [v, setV] = useState(null)
  useEffect(() => {
    if (!token) return
    const days = token.expires_at ? Math.max(1, Math.ceil((new Date(token.expires_at) - Date.now()) / 86400000)) : ''
    setV({ max_public_per_day: token.max_public_per_day, max_actions_per_day: token.max_actions_per_day,
      allowed_channel_ids: token.allowed_channel_ids || [], auto_confirm: token.auto_confirm, expires_in_days: days })
  }, [token])
  const save = async () => {
    try { await patch(`/api/tokens/${token.id}`, limitsPayload(v)); toast('Limits saved'); onSaved() } catch (e) { toast(e.message, 'err') }
  }
  return (
    <Dialog open={!!token} onClose={onClose} title={`Limits for ${token?.name || ''}`} testid="token-limits-dialog" wide
      footer={<Button variant="primary" onClick={save} data-testid="token-limits-save">Save limits</Button>}>
      {v && <TokenLimitFields value={v} onChange={setV} />}
    </Dialog>
  )
}

const STATUS = [['', 'Any result'], ['ok', 'OK'], ['error', 'Errors'], ['denied', 'Denied']]

export function AuditLog() {
  const [f, setF] = useState({ actor: '', risk: '', status: '', q: '', since: '', until: '' })
  const [rows, setRows] = useState(null)
  const [facets, setFacets] = useState({ actors: [], risks: [] })
  const qs = new URLSearchParams(Object.entries({ ...f, limit: 200 }).filter(([, x]) => x !== '' && x !== null)).toString()
  useEffect(() => { get('/api/audit/facets').then(setFacets).catch(() => {}) }, [])
  useEffect(() => { const t = setTimeout(() => get('/api/audit?' + qs).then(setRows).catch(() => setRows([])), 200); return () => clearTimeout(t) }, [qs])
  const set = (k, x) => setF({ ...f, [k]: x })
  return (
    <Card className="space-y-4 p-6" data-testid="audit-log">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="mr-auto font-semibold">Audit log</h3>
        <a href={'/api/audit/export?' + new URLSearchParams(Object.entries(f).filter(([, x]) => x)).toString()} download
          className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-lav-200 px-3 text-[13px] hover:border-lav-400" data-testid="audit-export"><Download className="size-3.5" />Export CSV</a>
      </div>
      <div className="grid gap-2 md:grid-cols-6">
        <Select value={f.actor} onChange={(e) => set('actor', e.target.value)} data-testid="audit-filter-actor" options={[['', 'Anyone'], ...facets.actors.map((a) => [a, a])]} />
        <Select value={f.risk} onChange={(e) => set('risk', e.target.value)} data-testid="audit-filter-risk" options={[['', 'Any risk'], ...facets.risks.map((r) => [r, r])]} />
        <Select value={f.status} onChange={(e) => set('status', e.target.value)} data-testid="audit-filter-status" options={STATUS} />
        <Input value={f.q} onChange={(e) => set('q', e.target.value)} placeholder="Search path, action, detail" data-testid="audit-filter-q" />
        <Input type="date" value={f.since} onChange={(e) => set('since', e.target.value)} aria-label="From" data-testid="audit-filter-since" />
        <Input type="date" value={f.until} onChange={(e) => set('until', e.target.value)} aria-label="Before" data-testid="audit-filter-until" />
      </div>
      <div className="max-h-96 overflow-auto"><table className="w-full text-xs" data-testid="audit-table"><tbody>
        {(rows || []).map((a) => (
          <tr key={a.id} className="border-b border-lav-100 align-top" data-testid="audit-row">
            <td className="whitespace-nowrap py-1.5 pr-3 text-muted">{new Date(a.at).toLocaleString()}</td>
            <td className="pr-3 font-medium">{a.actor}</td>
            <td className="pr-3 font-mono">{a.action || a.tool}</td>
            <td className="pr-3">{a.risk && <Badge tone={['public', 'destructive'].includes(a.risk) ? 'amber' : 'lav'}>{a.risk}</Badge>}</td>
            <td className="pr-3 font-mono">{a.method} {a.path}</td>
            <td className="pr-3"><Badge tone={a.status < 400 ? 'green' : 'red'}>{a.status}</Badge></td>
            <td className="max-w-xs truncate text-muted" title={a.detail}>{a.result || a.detail}</td>
          </tr>
        ))}
        {rows && !rows.length && <tr><td className="py-3 text-muted">Nothing matches.</td></tr>}
      </tbody></table></div>
    </Card>
  )
}
