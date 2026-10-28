import { Check, Copy, ExternalLink, KeyRound } from 'lucide-react'
import { useEffect, useState } from 'react'
import { get, put } from '../api'
import { useStore } from '../store'
import { Badge, Button, Field, Input, NetIcon, useToast, confirmAsync } from '../ui'

// Where Ram creates each developer app, and the one-line "what to paste" for the form.
export const KEY_HELP = {
  instagram: { url: 'https://developers.facebook.com/apps/', label: 'Meta for Developers', text: 'Meta app → Instagram → API setup with Instagram login → copy the Instagram app ID and secret.' },
  youtube: { url: 'https://console.cloud.google.com/apis/credentials', label: 'Google Cloud credentials', text: 'OAuth client (Web application) with YouTube Data API v3 enabled.' },
  x: { url: 'https://developer.x.com/en/portal/dashboard', label: 'X developer portal', text: 'App → User authentication settings → OAuth 2.0, Web App (confidential client) → Keys and tokens → OAuth 2.0 Client ID and Secret.' },
  tiktok: { url: 'https://developers.tiktok.com/apps/', label: 'TikTok for Developers', text: 'Login Kit + Content Posting API → client key and secret.' },
  unsplash: { url: 'https://unsplash.com/oauth/applications', label: 'Unsplash developers', text: 'Free photo picker in the Library and composer. New application → copy the Access Key (demo apps get 50 searches an hour).' },
}

export function CopyText({ text, testid }) {
  const [ok, setOk] = useState(false)
  return (
    <span className="flex items-center gap-1">
      <code className="min-w-0 flex-1 break-all rounded bg-lav-100 px-1.5 py-0.5 text-xs" data-testid={testid}>{text}</code>
      <Button size="icon" variant="ghost" aria-label="Copy" onClick={() => { navigator.clipboard?.writeText(text); setOk(true); setTimeout(() => setOk(false), 1500) }}>
        {ok ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
      </Button>
    </span>
  )
}

/** One network's developer-app keys. Saved encrypted on the server; secrets are never sent back. */
export function AppKeysForm({ provider, data, onSaved, compact }) {
  const toast = useToast()
  const [vals, setVals] = useState({})
  const [busy, setBusy] = useState(false)
  const help = KEY_HELP[provider]
  const save = async () => {
    setBusy(true)
    try {
      const out = await put(`/api/app-keys/${provider}`, vals)
      setVals({})
      toast(out.configured ? `${data.name} keys saved - ready to connect` : `${data.name} keys saved`)
      onSaved?.(out)
    } catch (e) { toast(e.message, 'err') } finally { setBusy(false) }
  }
  const clear = async () => {
    if (!(await confirmAsync(`Remove the ${data.name} keys saved here?`, { ok: 'Remove' }))) return
    const body = Object.fromEntries(data.fields.map((f) => [f.key, '']))
    try { onSaved?.(await put(`/api/app-keys/${provider}`, body)); toast('Keys removed') } catch (e) { toast(e.message, 'err') }
  }
  return (
    <div className="space-y-4" data-testid={`appkeys-${provider}`}>
      {!compact && (
        <div className="flex items-center gap-3">
          <NetIcon provider={provider} />
          <div className="flex-1 font-semibold">{data.name}</div>
          <Badge tone={data.configured ? 'green' : 'amber'} data-testid={`appkeys-${provider}-status`}>{data.configured ? 'Ready' : 'Needs keys'}</Badge>
        </div>
      )}
      {help && <p className="text-sm text-ink-2">{help.text} <a data-testid="app-keys-a" className="inline-flex items-center gap-0.5 text-lav-700 underline" href={help.url} target="_blank" rel="noreferrer">{help.label}<ExternalLink className="size-3" /></a></p>}
      {data.redirect_uri && <Field label="Redirect URI to register in the developer app"><CopyText text={data.redirect_uri} testid={`appkeys-${provider}-redirect`} /></Field>}
      {data.deauthorize_url && <div className="grid gap-3 md:grid-cols-2">
        <Field label="Deauthorize callback URL" hint="Meta app → Instagram → App settings"><CopyText text={data.deauthorize_url} testid={`appkeys-${provider}-deauthorize`} /></Field>
        <Field label="Data deletion request URL" hint="Meta app → Instagram → App settings"><CopyText text={data.data_deletion_url} testid={`appkeys-${provider}-data-deletion`} /></Field>
      </div>}
      <div className="grid gap-3 md:grid-cols-2">
        {data.fields.map((f) => (
          <Field key={f.key} label={f.label} hint={f.set ? (f.source === 'env' ? 'Set in the server environment' : 'Saved') + (f.secret ? ` (${f.hint})` : '') : 'Not set'}>
            <Input type={f.secret ? 'password' : 'text'} autoComplete="off" spellCheck={false}
              placeholder={f.secret ? (f.set ? 'Leave empty to keep the saved secret' : 'Paste the secret') : (f.value || 'Paste the ID')}
              value={vals[f.key] ?? (f.secret ? '' : f.value || '')} onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })}
              data-testid={`appkeys-${f.key}`} aria-label={f.label} />
          </Field>
        ))}
      </div>
      <div className="flex gap-2">
        <Button variant="primary" loading={busy} disabled={!Object.values(vals).some((v) => v && v.trim())} onClick={save} data-testid={`appkeys-${provider}-save`}><KeyRound className="size-4" />Save keys</Button>
        {data.fields.some((f) => f.source === 'ui') && <Button variant="danger-ghost" onClick={clear} data-testid={`appkeys-${provider}-clear`}>Remove saved keys</Button>}
      </div>
    </div>
  )
}

/** Loads /api/app-keys and renders the form for one or all networks. */
export function AppKeys({ only, compact, onConfigured }) {
  const { reload } = useStore()
  const [keys, setKeys] = useState(null)
  const toast = useToast()
  useEffect(() => { get('/api/app-keys').then(setKeys).catch((e) => toast(e.message, 'err')) }, [])
  if (!keys) return <p className="text-sm text-muted">Loading…</p>
  const list = Object.entries(keys).filter(([p]) => !only || p === only)
  return (
    <div className="space-y-8">
      {list.map(([p, d]) => (
        <AppKeysForm key={p} provider={p} data={d} compact={compact}
          onSaved={async (out) => { setKeys({ ...keys, [p]: out }); await reload(); if (out.configured) onConfigured?.(p) }} />
      ))}
    </div>
  )
}
