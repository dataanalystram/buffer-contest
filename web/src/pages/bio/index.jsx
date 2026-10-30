// Link in bio (Growth › Link in bio): a block editor.
//   Links (autosaves) | Design (draft with undo / redo / cancel / save) | Settings | Analytics, next to a sticky live
//   phone preview that renders unsaved edits through bio.preview_draft. Every call is a bio.* registry action, so agents
//   can do the same through REST / MCP / the CLI.
import { AnimatePresence, motion } from 'motion/react'
import { BarChart3, Check, ChevronDown, FileJson, Layers, Link2, Loader2, Palette, Plus, Redo2, Settings2, Smartphone, Undo2, Upload } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { get, patch, post } from '../../api'
import { setQuery, useRoute } from '../../router'
import { Button, confirmAsync, cx, Dialog, Empty, Field, Input, Menu, SkeletonList, Textarea, useToast } from '../../ui'
import { AnalyticsTab } from './AnalyticsTab'
import { DesignTab, presetPatch } from './DesignTab'
import { LinksTab } from './LinksTab'
import { PhonePreview, SharePopover } from './Preview'
import { SettingsTab } from './SettingsTab'
import { FontFaces, useDebounced, useThemes } from './shared'

const TABS = [['links', 'Links', Layers], ['design', 'Design', Palette], ['settings', 'Settings', Settings2], ['analytics', 'Analytics', BarChart3]]
const SERVER_KEYS = ['clicks', 'short_url', 'link_id', 'embed_url', 'provider', 'height']
const strip = (b) => Object.fromEntries(Object.entries(b).filter(([k]) => !SERVER_KEYS.includes(k)))
const contentOf = (p) => ({ title: p.title, bio: p.bio, avatar_url: p.avatar_url, blocks: p.blocks.map(strip),
  settings: { socials: p.settings?.socials || [], socials_position: p.settings?.socials_position || 'top' } })

export function BioTab() {
  const toast = useToast()
  const { query } = useRoute()
  const [pages, setPages] = useState(null)
  const [slug, setSlug] = useState('huhbyte')
  const [busy, setBusy] = useState(false)
  const [importing, setImporting] = useState(false)
  const load = () => get('/api/bio').then(setPages).catch((e) => toast(e.message, 'err'))
  useEffect(() => { load() }, [])
  const create = async (e) => {
    e?.preventDefault()
    setBusy(true)
    try { const p = await post('/api/bio', { slug }); await load(); setQuery({ page: p.id }); toast('Page created') } catch (e2) { toast(e2.message, 'err') } finally { setBusy(false) }
  }
  const imported = async (p) => { setImporting(false); await load(); setQuery({ page: p.id }) }
  if (pages === null) return <SkeletonList rows={2} />
  const current = pages.find((p) => p.id === query.page) || pages[0]
  if (!current) {
    return (
      <>
        <Empty icon={Link2} title="Create your link-in-bio page" testid="bio-empty"
          action={(
            <div className="flex flex-col items-center gap-2">
              <form onSubmit={create} className="flex flex-wrap items-center justify-center gap-2">
                <span className="text-sm text-muted">/bio/</span>
                <Input className="w-48" value={slug} onChange={(e) => setSlug(e.target.value.toLowerCase())} aria-label="Page address" data-testid="bio-create-slug" />
                <Button variant="primary" loading={busy} data-testid="bio-create-submit"><Plus className="size-4" />Create page</Button>
              </form>
              <Button size="sm" variant="ghost" onClick={() => setImporting(true)} data-testid="bio-import-open"><Upload className="size-3.5" />Import a page (JSON)</Button>
            </div>
          )}>
          One page for the link in your Instagram, TikTok and YouTube bios: brand buttons, videos, an email signup, your contact card,
          your latest posts and your socials, with clicks counted per link. Your page, your domain, no monthly fee.
        </Empty>
        <ImportDialog open={importing} onClose={() => setImporting(false)} onDone={imported} />
      </>
    )
  }
  return (
    <>
      <BioEditor key={current.id} initial={current} pages={pages} onChanged={load} onImport={() => setImporting(true)}
        onCreate={async (s) => { const p = await post('/api/bio', { slug: s }); await load(); setQuery({ page: p.id }); toast(`Created /bio/${p.slug}`) }} />
      <ImportDialog open={importing} onClose={() => setImporting(false)} onDone={imported} />
    </>
  )
}

function ImportDialog({ open, onClose, onDone }) {
  const toast = useToast()
  const [text, setText] = useState('')
  const [slug, setSlug] = useState('')
  const file = useRef(null)
  const run = async () => {
    let data
    try { data = JSON.parse(text) } catch { toast('That is not valid JSON', 'err'); return }
    try { const p = await post('/api/bio/import', { data, slug: slug || undefined }); toast('Page imported as a draft'); setText(''); setSlug(''); onDone(p) } catch (e) { toast(e.message, 'err') }
  }
  return (
    <Dialog open={open} onClose={onClose} title="Import a bio page" testid="bio-import"
      footer={<><Button variant="ghost" onClick={onClose} data-testid="bio-import-cancel">Cancel</Button><Button variant="primary" onClick={run} disabled={!text.trim()} data-testid="bio-import-submit">Import</Button></>}>
      <div className="space-y-3">
        <p className="text-sm text-muted">Paste a page exported from HuhQueue (Export JSON), or pick the file. It is created as an unpublished draft with fresh click tracking.</p>
        <input ref={file} type="file" accept="application/json,.json" className="hidden" aria-label="JSON file" data-testid="bio-import-file"
          onChange={async (e) => { const f = e.target.files?.[0]; if (f) setText(await f.text()) }} />
        <Button size="sm" onClick={() => file.current?.click()} data-testid="bio-import-pick"><FileJson className="size-3.5" />Choose file</Button>
        <Textarea rows={6} value={text} onChange={(e) => setText(e.target.value)} placeholder='{"kind":"huhqueue.bio", "page": {...}}' aria-label="Export JSON" data-testid="bio-import-json" />
        <Field label="Page address (optional)" hint="Leave empty to keep the address in the file">
          <Input value={slug} onChange={(e) => setSlug(e.target.value.toLowerCase())} placeholder="my-page" data-testid="bio-import-slug" />
        </Field>
      </div>
    </Dialog>
  )
}

function BioEditor({ initial, pages, onChanged, onCreate, onImport }) {
  const toast = useToast()
  const { query } = useRoute()
  const tab = TABS.some(([k]) => k === query.tab) ? query.tab : 'links'
  const catalog = useThemes()
  const [p, setP] = useState(initial)
  const [stats, setStats] = useState(null)
  const [focus, setFocus] = useState('')                 // block highlighted in the preview
  const [save, setSave] = useState({ state: 'saved' })   // saving | saved | error
  const [theme, setTheme] = useState(initial.theme)      // design draft
  const [savedTheme, setSavedTheme] = useState(initial.theme)
  const [hist, setHist] = useState({ past: [], future: [] })
  const [html, setHtml] = useState('')
  const [mobilePreview, setMobilePreview] = useState(false)
  const [newOpen, setNewOpen] = useState(false)

  const loadStats = () => get(`/api/bio/${initial.id}`).then((d) => { setStats(d.stats); mergeServer(d) }).catch(() => {})
  useEffect(() => { loadStats() }, [initial.id])

  // Merge server-only fields (click counts, short links, embed URLs) without touching what the user is typing.
  const mergeServer = (d) => setP((cur) => {
    const by = Object.fromEntries(d.blocks.map((b) => [b.id, b]))
    return { ...cur, published: d.published, views: d.views, clicks: d.clicks, subscribers: d.subscribers, public_url: d.public_url,
      domain_url: d.domain_url, slug: d.slug, domain: d.domain,
      blocks: cur.blocks.map((b) => (by[b.id] ? { ...b, ...Object.fromEntries(SERVER_KEYS.filter((k) => k in by[b.id]).map((k) => [k, by[b.id][k]])) } : b)) }
  })

  // ---- autosave of content (header, links, page socials)
  const content = useMemo(() => contentOf(p), [p])
  const key = JSON.stringify(content)
  const lastSaved = useRef(JSON.stringify(contentOf(initial)))
  const latest = useRef({ key, content })
  latest.current = { key, content }
  const saving = useRef(false)
  // One save at a time, always of the newest content: the server handles requests in parallel, so an older save
  // that finished last would otherwise overwrite newer edits.
  const flush = async () => {
    if (saving.current) return
    saving.current = true
    try {
      while (latest.current.key !== lastSaved.current) {
        const { key: k, content: c } = latest.current
        setSave({ state: 'saving' })
        const d = await patch(`/api/bio/${p.id}`, c)
        lastSaved.current = k
        mergeServer(d)
      }
      setSave({ state: 'saved', at: Date.now() })
    } catch (e) { setSave({ state: 'error', msg: e.message }) } finally { saving.current = false }
  }
  useDebounced(flush, 800, [key])

  // ---- live preview of content + design draft
  const seq = useRef(0)
  useDebounced(async () => {
    const n = ++seq.current                               // drop responses that arrive after a newer request
    try { const h = await post(`/api/bio/${p.id}/preview`, { ...content, settings: { ...(p.settings || {}), ...content.settings }, theme, highlight: tab === 'links' ? focus : '' }); if (n === seq.current) setHtml(h) } catch { /* keep the last good preview while a field is half typed */ }
  }, 300, [key, JSON.stringify(theme), focus, tab])

  // ---- design draft history
  const setDraft = (patchObj) => {
    setHist((h) => ({ past: [...h.past.slice(-49), theme], future: [] }))
    setTheme((t) => ({ ...t, ...patchObj }))
  }
  const undo = () => { if (!hist.past.length) return; setTheme(hist.past.at(-1)); setHist({ past: hist.past.slice(0, -1), future: [theme, ...hist.future] }) }
  const redo = () => { if (!hist.future.length) return; setTheme(hist.future[0]); setHist({ past: [...hist.past, theme], future: hist.future.slice(1) }) }
  const designDirty = JSON.stringify(theme) !== JSON.stringify(savedTheme)
  const saveDesign = async () => {
    try { const d = await patch(`/api/bio/${p.id}`, { theme }); setSavedTheme(d.theme); setTheme(d.theme); setHist({ past: [], future: [] }); toast('Design saved') } catch (e) { toast(e.message, 'err') }
  }
  const cancelDesign = async () => {
    if (!(await confirmAsync('Undo all drafted design changes?', { title: 'Discard design changes?', ok: 'Discard' }))) return
    setTheme(savedTheme); setHist({ past: [], future: [] })
  }
  const shuffle = () => {
    const list = (catalog?.presets || []).filter((t) => t.key !== theme.preset)
    if (list.length) setDraft(presetPatch(list[Math.floor(Math.random() * list.length)]))
  }

  const publish = async () => {
    try { const d = await patch(`/api/bio/${p.id}`, { published: !p.published }); mergeServer(d); onChanged?.(); toast(d.published ? 'Page published' : 'Page unpublished') } catch (e) { toast(e.message, 'err') }
  }

  return (
    <div className="space-y-4" data-testid="bio-editor">
      <FontFaces fonts={catalog?.fonts} />
      {/* top bar: page switcher, save state, share, publish */}
      <div className="flex flex-wrap items-center gap-2 rounded-3xl border border-lav-200/70 bg-surface px-3 py-2 shadow-soft">
        <Menu label="Switch page" testid="bio-page-switch" variant="ghost" size="sm" align="left"
          items={[...pages.map((x) => [`/bio/${x.slug}${x.published ? '' : ' (draft)'}`, () => setQuery({ page: x.id, tab }), { testid: 'bio-page-tab' }]),
            ['New page…', () => setNewOpen(true), { icon: Plus, testid: 'bio-new-page' }],
            ['Import JSON…', onImport, { icon: Upload, testid: 'bio-import-open' }]]}>
          <span className="grid size-6 place-items-center overflow-hidden rounded-full bg-lav-200">{p.avatar_url ? <img src={p.avatar_url} alt="" className="size-full object-cover" /> : <Link2 className="size-3.5" />}</span>
          <span className="max-w-40 truncate font-semibold">/bio/{p.slug}</span><ChevronDown className="size-3.5 text-muted" />
        </Menu>
        <a href={p.public_url} target="_blank" rel="noreferrer" className="hidden min-w-0 truncate text-[13px] text-muted hover:text-ink hover:underline md:block" data-testid="bio-public-url">{p.public_url}</a>
        <span className={cx('rounded-full px-2 py-0.5 text-[11px] font-semibold', p.published ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700')} data-testid="bio-status">{p.published ? 'Live' : 'Draft'}</span>
        <div className="ml-auto flex items-center gap-2">
          <SaveState save={save} />
          <SharePopover page={p} />
          <Button size="sm" variant={p.published ? 'outline' : 'primary'} onClick={publish} data-testid="bio-publish" className="rounded-full">{p.published ? 'Unpublish' : 'Publish'}</Button>
        </div>
      </div>
      <NewPageDialog open={newOpen} setOpen={setNewOpen} onCreate={onCreate} />

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_360px] 2xl:grid-cols-[minmax(0,720px)_1fr]">
        <div className="flex min-w-0 gap-4">
          {/* Icon rail (a row on small screens) */}
          <nav role="tablist" aria-label="Bio editor sections" className="sticky top-2 hidden h-fit flex-col gap-1 md:flex" data-testid="bio-tabs">
            {TABS.map(([k, l, I]) => (
              <button key={k} role="tab" aria-selected={tab === k} data-testid={`bio-tab-${k}`} onClick={() => setQuery({ tab: k })}
                className={cx('group flex w-16 cursor-pointer flex-col items-center gap-1 rounded-2xl py-2 text-[11px] font-medium transition', tab === k ? 'text-ink' : 'text-muted hover:text-ink')}>
                <span className={cx('grid size-10 place-items-center rounded-full transition', tab === k ? 'bg-ink text-surface shadow-soft' : 'bg-lav-100 group-hover:bg-lav-200')}><I className="size-[18px]" /></span>{l}
              </button>
            ))}
          </nav>
          <div className="min-w-0 flex-1 space-y-4">
            <div role="tablist" aria-label="Bio editor sections" className="flex gap-1 overflow-x-auto rounded-2xl bg-lav-100 p-1 md:hidden">
              {TABS.map(([k, l, I]) => (
                <button key={k} role="tab" aria-selected={tab === k} data-testid={`bio-mtab-${k}`} onClick={() => setQuery({ tab: k })}
                  className={cx('flex flex-1 cursor-pointer items-center justify-center gap-1.5 rounded-xl px-3 py-2 text-[13px] font-medium', tab === k ? 'bg-surface text-ink shadow-soft' : 'text-muted')}><I className="size-4" />{l}</button>
              ))}
            </div>
            <AnimatePresence>
              {designDirty && (
                <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} data-testid="bio-design-bar"
                  className="sticky top-2 z-20 flex items-center gap-2 rounded-2xl border border-lav-300 bg-surface/95 px-3 py-2 shadow-pop backdrop-blur">
                  <span className="text-[13px] font-medium">Design draft</span><span className="hidden text-xs text-muted sm:inline">shown in the preview, not live yet</span>
                  <span className="ml-auto flex items-center gap-1">
                    <Button size="icon-sm" variant="ghost" aria-label="Undo" disabled={!hist.past.length} onClick={undo} data-testid="bio-design-undo"><Undo2 className="size-4" /></Button>
                    <Button size="icon-sm" variant="ghost" aria-label="Redo" disabled={!hist.future.length} onClick={redo} data-testid="bio-design-redo"><Redo2 className="size-4" /></Button>
                    <Button size="sm" variant="outline" className="rounded-full" onClick={cancelDesign} data-testid="bio-design-cancel">Cancel</Button>
                    <Button size="sm" variant="lav" className="rounded-full" onClick={saveDesign} data-testid="bio-design-save"><Check className="size-3.5" />Save</Button>
                  </span>
                </motion.div>
              )}
            </AnimatePresence>
            <AnimatePresence mode="wait" initial={false}>
              <motion.div key={tab} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.18 }}>
                {tab === 'links' && <LinksTab p={p} setP={setP} focus={focus} setFocus={setFocus} stats={stats} />}
                {tab === 'design' && <DesignTab theme={theme} setDraft={setDraft} catalog={catalog} shuffle={shuffle} />}
                {tab === 'settings' && <SettingsTab p={p} onSaved={(d) => { mergeServer(d); setP((c) => ({ ...c, settings: { ...d.settings, socials: c.settings?.socials, socials_position: c.settings?.socials_position } })); onChanged?.() }} onChanged={onChanged} />}
                {tab === 'analytics' && <AnalyticsTab p={p} stats={stats} reload={loadStats} />}
              </motion.div>
            </AnimatePresence>
          </div>
        </div>
        <PhonePreview page={p} html={html} focus={focus} open={mobilePreview} onClose={() => setMobilePreview(false)} />
      </div>
      <button type="button" onClick={() => setMobilePreview(true)} data-testid="bio-preview-toggle" aria-label="Show preview"
        className="fixed bottom-5 left-1/2 z-30 flex -translate-x-1/2 cursor-pointer items-center gap-2 rounded-full bg-ink px-5 py-3 text-sm font-semibold text-surface shadow-pop lg:hidden">
        <Smartphone className="size-4" />Preview
      </button>
    </div>
  )
}

function SaveState({ save }) {
  if (save.state === 'saving') return <span className="flex items-center gap-1 text-xs text-muted" data-testid="bio-autosave"><Loader2 className="size-3.5 animate-spin" />Saving…</span>
  if (save.state === 'error') return <span className="max-w-64 truncate text-xs text-rose-600" title={save.msg} data-testid="bio-autosave">Not saved: {save.msg}</span>
  return <span className="flex items-center gap-1 text-xs text-muted" data-testid="bio-autosave"><Check className="size-3.5 text-emerald-600" />Saved</span>
}

function NewPageDialog({ open, setOpen, onCreate }) {
  const toast = useToast()
  const [slug, setSlug] = useState('')
  const go = async (e) => {
    e?.preventDefault()
    try { await onCreate(slug); setOpen(false); setSlug('') } catch (e2) { toast(e2.message, 'err') }
  }
  return (
    <Dialog open={open} onClose={() => setOpen(false)} title="New bio page" testid="bio-new-dialog"
      footer={<><Button variant="ghost" onClick={() => setOpen(false)} data-testid="bio-new-cancel">Cancel</Button><Button variant="primary" onClick={go} disabled={!slug} data-testid="bio-new-page-create">Create</Button></>}>
      <form onSubmit={go} className="flex items-center gap-2"><span className="text-sm text-muted">/bio/</span>
        <Input data-testid="growth-new-slug" autoFocus placeholder="new-page" value={slug} onChange={(e) => setSlug(e.target.value.toLowerCase())} /></form>
    </Dialog>
  )
}
