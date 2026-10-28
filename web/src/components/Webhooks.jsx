// Settings › Webhooks: send HuhQueue events to Zapier, Make, n8n or your own server (services/webhooks.py).
import { Copy, Plus, Send, Trash2, Webhook as WebhookIcon } from 'lucide-react'
import { useEffect, useState } from 'react'
import { del, get, patch, post } from '../api'
import { Badge, Button, Card, confirmAsync, cx, Field, Input, Switch, useToast } from '../ui'

const GUIDES = [['Zapier', 'Webhooks by Zapier › Catch Hook'], ['Make', 'Webhooks › Custom webhook'], ['n8n', 'Webhook node (POST)']]

export function WebhooksSection() {
  const toast = useToast()
  const [d, setD] = useState(null)
  const [url, setUrl] = useState('')
  const [events, setEvents] = useState([])
  const [secret, setSecret] = useState(null)
  const [open, setOpen] = useState(null)
  const [log, setLog] = useState([])
  const load = () => get('/api/webhooks').then(setD).catch((e) => toast(e.message, 'err'))
  useEffect(() => { load() }, [])
  useEffect(() => { if (open) get(`/api/webhooks/${open}/deliveries`).then((r) => setLog(r.deliveries)).catch(() => setLog([])) }, [open])
  if (!d) return null
  const toggle = (e) => setEvents(events.includes(e) ? events.filter((x) => x !== e) : [...events, e])
  const add = async () => {
    try { const w = await post('/api/webhooks', { url, events }); setSecret({ id: w.id, secret: w.secret }); setUrl(''); setEvents([]); load(); toast('Webhook added') } catch (e) { toast(e.message, 'err') }
  }
  const test = async (w) => {
    try { const r = await post(`/api/webhooks/${w.id}/test`); toast(r.ok ? `Test delivered (HTTP ${r.status_code})` : `Test failed${r.status_code ? ` (HTTP ${r.status_code})` : ': no response'}`, r.ok ? undefined : 'err'); if (open === w.id) setOpen(null) } catch (e) { toast(e.message, 'err') }
  }
  const remove = async (w) => { if (await confirmAsync(`Delete the webhook to ${w.url}?`, { ok: 'Delete' })) { await del(`/api/webhooks/${w.id}`); load() } }
  const rotate = async (w) => { const r = await patch(`/api/webhooks/${w.id}`, { rotate_secret: true }); setSecret({ id: w.id, secret: r.secret }) }
  const redeliver = async (id) => { await post(`/api/webhooks/deliveries/${id}/redeliver`); toast('Queued again'); get(`/api/webhooks/${open}/deliveries`).then((r) => setLog(r.deliveries)) }
  return (
    <div className="space-y-6" data-testid="settings-webhooks">
      <Card className="space-y-4 p-6">
        <p className="text-sm text-ink-2">Send events (post sent or failed, new comments, approvals, reconnect warnings…) as signed JSON to <b>Zapier</b>, <b>Make</b>, <b>n8n</b> or your own server. To create posts from a Zap, call the REST API with a token from <a className="underline" href="#/settings?section=api" data-testid="webhooks-api-link">API &amp; MCP agents</a>.</p>
        <ul className="flex flex-wrap gap-2 text-xs text-muted">{GUIDES.map(([n, g]) => <li key={n} className="rounded-lg bg-lav-50 px-2 py-1"><b className="text-ink-2">{n}:</b> {g}</li>)}</ul>
        <Field label="Webhook URL"><Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://hooks.zapier.com/hooks/catch/…" data-testid="webhook-url" aria-label="Webhook URL" /></Field>
        <div><div className="mb-2 text-[13px] font-medium text-ink-2">Events <span className="text-muted">(none ticked = all)</span></div>
          <div className="flex flex-wrap gap-2">{d.events.filter((e) => e !== 'ping').map((e) => (
            <label key={e} className={cx('flex cursor-pointer items-center gap-2 rounded-xl border px-3 py-1.5 font-mono text-xs', events.includes(e) ? 'border-lav-600 bg-lav-100' : 'border-lav-200')}>
              <input type="checkbox" className="accent-lav-600" checked={events.includes(e)} onChange={() => toggle(e)} data-testid={`webhook-event-${e}`} aria-label={e} />{e}</label>))}</div></div>
        <Button variant="primary" onClick={add} disabled={!url} data-testid="webhook-add"><Plus className="size-4" />Add webhook</Button>
        {secret && <div className="space-y-1 rounded-2xl bg-[#141020] p-4 text-[#f3ecff]" data-testid="webhook-secret"><p className="text-xs">Signing secret - copy now, shown once. Verify <code>X-HuhQueue-Signature</code> = sha256 HMAC of <code>timestamp.body</code>:</p>
          <div className="flex items-center gap-2"><code className="break-all text-sm text-white">{secret.secret}</code><Button size="icon" variant="ghost" aria-label="Copy secret" data-testid="webhook-secret-copy" onClick={() => { navigator.clipboard?.writeText(secret.secret); toast('Copied') }}><Copy className="size-4" /></Button></div></div>}
      </Card>
      <Card className="p-6">
        <h3 className="mb-3 font-semibold">Webhooks</h3>
        <ul className="divide-y divide-lav-100">
          {d.webhooks.map((w) => (
            <li key={w.id} className="space-y-2 py-3 text-sm" data-testid="webhook-row">
              <div className="flex flex-wrap items-center gap-2">
                <WebhookIcon className="size-4 text-lav-600" /><span className="min-w-0 max-w-md truncate font-medium" title={w.url}>{w.url}</span>
                {w.all_events ? <Badge tone="lav">all events</Badge> : w.events.map((e) => <Badge key={e} tone="lav">{e}</Badge>)}
                {w.failures > 0 && <Badge tone="amber">{w.failures} failed</Badge>}
                <div className="ml-auto flex items-center gap-1">
                  <Switch checked={w.active} onChange={async (v) => { await patch(`/api/webhooks/${w.id}`, { active: v }); load() }} label="Active" testid="webhook-active" />
                  <Button size="sm" variant="ghost" onClick={() => test(w)} data-testid="webhook-test"><Send className="size-3.5" />Test</Button>
                  <Button size="sm" variant="ghost" onClick={() => setOpen(open === w.id ? null : w.id)} data-testid="webhook-log">Deliveries</Button>
                  <Button size="sm" variant="ghost" onClick={() => rotate(w)} data-testid="webhook-rotate">New secret</Button>
                  <Button size="sm" variant="danger-ghost" onClick={() => remove(w)} data-testid="webhook-delete"><Trash2 className="size-3.5" /></Button>
                </div>
              </div>
              {open === w.id && (
                <table className="w-full text-xs" data-testid="webhook-deliveries"><tbody>
                  {log.map((x) => <tr key={x.id} className="border-b border-lav-100" data-testid="webhook-delivery"><td className="py-1.5 pr-3 text-muted">{new Date(x.created_at).toLocaleString()}</td><td className="pr-3 font-mono">{x.event}</td>
                    <td className="pr-3"><Badge tone={x.status === 'delivered' ? 'green' : x.status === 'failed' ? 'red' : 'amber'}>{x.status}</Badge></td><td className="pr-3">{x.response_code || '—'}</td><td className="pr-3">{x.attempts} tr{x.attempts === 1 ? 'y' : 'ies'}</td>
                    <td>{x.status === 'failed' && <Button size="sm" variant="ghost" onClick={() => redeliver(x.id)} data-testid="webhook-redeliver">Retry</Button>}</td></tr>)}
                  {!log.length && <tr><td className="py-2 text-muted">No deliveries yet.</td></tr>}
                </tbody></table>
              )}
            </li>
          ))}
          {!d.webhooks.length && <li className="py-3 text-sm text-muted">No webhooks yet.</li>}
        </ul>
      </Card>
    </div>
  )
}
