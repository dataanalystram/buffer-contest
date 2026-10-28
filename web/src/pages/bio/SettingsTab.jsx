// Settings tab: page address, SEO / share image, page options, custom domain, email subscribers, export / duplicate / delete.
import { Copy, Download, Globe, Search, Share2, Trash2, Users } from 'lucide-react'
import { useEffect, useState } from 'react'
import { del, get, patch, post } from '../../api'
import { setQuery } from '../../router'
import { Badge, Button, confirmAsync, Field, Input, Switch, Textarea, useToast } from '../../ui'
import { download, Panel } from './shared'

const pick = (p) => ({ slug: p.slug, domain: p.domain || '', settings: { seo_title: '', seo_description: '', og_image: '', share_button: false, hide_branding: false, ...(p.settings || {}) } })

export function SettingsTab({ p, onSaved, onChanged }) {
  const toast = useToast()
  const [f, setF] = useState(() => pick(p))
  const [saving, setSaving] = useState(false)
  const [domainInfo, setDomainInfo] = useState(null)
  const dirty = JSON.stringify(f) !== JSON.stringify(pick(p))
  const st = f.settings
  const setS = (o) => setF({ ...f, settings: { ...st, ...o } })
  const save = async () => {
    setSaving(true)
    try {
      const { socials: _a, socials_position: _b, ...settings } = st
      const d = await patch(`/api/bio/${p.id}`, { slug: f.slug, domain: f.domain, settings })
      onSaved(d); setF(pick(d)); toast('Settings saved')
    } catch (e) { toast(e.message, 'err') } finally { setSaving(false) }
  }
  const checkDomain = async () => { try { setDomainInfo(await get(`/api/bio/${p.id}/domain-check`)) } catch (e) { toast(e.message, 'err') } }
  const exportJson = async () => {
    try { const d = await get(`/api/bio/${p.id}/export`); download(`bio-${p.slug}.json`, JSON.stringify(d, null, 2)); toast('Exported') } catch (e) { toast(e.message, 'err') }
  }
  const duplicate = async () => {
    try { const d = await post(`/api/bio/${p.id}/duplicate`, {}); toast(`Duplicated as /bio/${d.slug} (draft)`); await onChanged?.(); setQuery({ page: d.id }) } catch (e) { toast(e.message, 'err') }
  }
  const remove = async () => {
    if (!(await confirmAsync(`Delete /bio/${p.slug}? Its email subscribers are deleted too; short links and click history are kept.`, { ok: 'Delete page' }))) return
    try { await del(`/api/bio/${p.id}`); setQuery({ page: '', tab: '' }); onChanged?.() } catch (e) { toast(e.message, 'err') }
  }
  return (
    <div className="space-y-4" data-testid="bio-settings">
      <Panel title="Page address">
        <div className="flex items-center gap-1"><span className="text-sm text-muted">/bio/</span>
          <Input value={f.slug} onChange={(e) => setF({ ...f, slug: e.target.value.toLowerCase() })} aria-label="Page address" data-testid="bio-slug" /></div>
        <p className="mt-1.5 text-xs text-muted">Lowercase letters, numbers and dashes. Changing it breaks links to the old address.</p>
      </Panel>

      <Panel title={<span className="flex items-center gap-2"><Search className="size-4 text-lav-600" />Search and sharing</span>}>
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Search / share title" hint="Defaults to the page title"><Input value={st.seo_title || ''} onChange={(e) => setS({ seo_title: e.target.value })} data-testid="bio-seo-title" /></Field>
          <Field label="Share image URL" hint="1200×630 works best; defaults to the avatar"><Input value={st.og_image || ''} onChange={(e) => setS({ og_image: e.target.value })} placeholder="https://…" data-testid="bio-og-image" /></Field>
        </div>
        <Field label="Search / share description" className="mt-3"><Textarea rows={2} value={st.seo_description || ''} onChange={(e) => setS({ seo_description: e.target.value })} data-testid="bio-seo-description" /></Field>
        <div className="mt-4 space-y-3 text-sm">
          <label className="flex items-center justify-between gap-3"><span className="flex items-center gap-2"><Share2 className="size-4 text-muted" />Share buttons at the bottom of the page</span>
            <Switch checked={!!st.share_button} onChange={(v) => setS({ share_button: v })} label="Share buttons" testid="bio-share-button" /></label>
          <label className="flex items-center justify-between gap-3"><span>Hide “Made with HuhQueue”</span>
            <Switch checked={!!st.hide_branding} onChange={(v) => setS({ hide_branding: v })} label="Hide Made with HuhQueue" testid="bio-hide-branding" /></label>
        </div>
      </Panel>

      <Panel title={<span className="flex items-center gap-2"><Globe className="size-4 text-lav-600" />Custom domain</span>}>
        <p className="mb-3 text-xs text-muted">Serve this page at the root of your own domain, e.g. <b>links.huhbyte.ai</b>. Point the domain at this server, then save.</p>
        <div className="flex flex-wrap gap-2">
          <Input className="max-w-xs" value={f.domain} onChange={(e) => setF({ ...f, domain: e.target.value })} placeholder="links.example.com" aria-label="Custom domain" data-testid="bio-domain" />
          <Button size="md" onClick={checkDomain} disabled={!p.domain} data-testid="bio-domain-check">Check DNS</Button>
        </div>
        {domainInfo && (
          <div className="mt-3 space-y-1 text-xs" data-testid="bio-domain-info">
            <Badge tone={domainInfo.resolves ? 'green' : 'amber'}>{domainInfo.resolves ? 'Points here' : 'Not pointing here yet'}</Badge>
            <ol className="ml-4 list-decimal space-y-0.5 text-ink-2">{domainInfo.steps.map((s) => <li key={s}>{s}</li>)}</ol>
          </div>
        )}
      </Panel>

      <div className="sticky bottom-3 z-10 flex items-center gap-2 rounded-2xl border border-lav-200 bg-surface/95 px-4 py-3 shadow-pop backdrop-blur">
        <span className="text-sm text-muted">{dirty ? 'You have unsaved settings' : 'All settings saved'}</span>
        <Button variant="primary" className="ml-auto rounded-full" loading={saving} onClick={save} disabled={!dirty} data-testid="bio-save-settings">Save settings</Button>
      </div>

      <Subscribers p={p} />

      <Panel title="Page">
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={exportJson} data-testid="bio-export"><Download className="size-3.5" />Export JSON</Button>
          <Button size="sm" onClick={duplicate} data-testid="bio-duplicate"><Copy className="size-3.5" />Duplicate</Button>
          <Button size="sm" variant="danger-ghost" className="ml-auto" onClick={remove} data-testid="bio-delete"><Trash2 className="size-3.5" />Delete page</Button>
        </div>
      </Panel>
    </div>
  )
}

function Subscribers({ p }) {
  const toast = useToast()
  const [subs, setSubs] = useState(null)
  const load = () => get(`/api/bio/${p.id}/subscribers`).then(setSubs).catch(() => {})
  useEffect(() => { load() }, [p.id])
  const removeSub = async (sid) => {
    if (!(await confirmAsync('Remove this subscriber?', { ok: 'Remove' }))) return
    try { await del(`/api/bio/${p.id}/subscribers/${sid}`); load() } catch (e) { toast(e.message, 'err') }
  }
  const exportSubs = async () => {
    try {
      const r = await fetch(`/api/bio/${p.id}/subscribers?format=csv`, { credentials: 'same-origin' })
      download(`bio-${p.slug}-subscribers.csv`, await r.text(), 'text/csv')
    } catch (e) { toast(e.message, 'err') }
  }
  return (
    <Panel testid="bio-subscribers"
      title={<span className="flex items-center gap-2"><Users className="size-4 text-lav-600" />Subscribers <span className="text-sm font-normal text-muted" data-testid="bio-subscriber-count">({subs?.count ?? 0})</span></span>}
      action={<span className="flex gap-2">
        <Button size="sm" variant="ghost" onClick={load} data-testid="bio-subscribers-refresh">Refresh</Button>
        <Button size="sm" onClick={exportSubs} disabled={!subs?.count} data-testid="bio-subscribers-csv"><Download className="size-3.5" />CSV</Button>
      </span>}>
      {!p.blocks.some((b) => b.type === 'email') && <p className="text-sm text-muted">Add an <b>Email signup</b> block to collect subscribers. Export them to any newsletter tool as CSV.</p>}
      {subs?.items?.length > 0 && (
        <ul className="divide-y divide-lav-100 text-sm">
          {subs.items.slice(0, 50).map((x) => (
            <li key={x.id} className="flex items-center gap-2 py-1.5" data-testid="bio-subscriber-row">
              <span className="min-w-0 flex-1 truncate">{x.email}{x.name && <span className="text-muted"> · {x.name}</span>}</span>
              <span className="text-xs text-muted">{new Date(x.created_at).toLocaleDateString()}</span>
              <Button size="icon-sm" variant="danger-ghost" aria-label={`Remove ${x.email}`} onClick={() => removeSub(x.id)} data-testid="bio-subscriber-remove"><Trash2 className="size-3.5" /></Button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}
