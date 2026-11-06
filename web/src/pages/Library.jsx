// Media library: folders, tags, search, usage tracker, duplicate finder. Registry: media.list / media.update /
// media.delete / media.usage / media.folders / media.upload.
import { motion } from 'motion/react'
import { Camera, Copy, Crop, Folder, FolderPlus, Image as ImageIcon, Link2, Search, Tag, Trash2, Upload, Video, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { del, get, patch, post, upload } from '../api'
import { go, setQuery, useRoute } from '../router'
import ImageEditor from '../components/ImageEditor'
import UnsplashPicker from '../components/UnsplashPicker'
import { Badge, Button, cx, Empty, Input, SkeletonList, useToast, confirmAsync, promptAsync } from '../ui'

const SMART = [['', 'All files'], ['unused', 'Unused'], ['duplicates', 'Duplicates']]

export default function Library() {
  const { query } = useRoute()
  const toast = useToast()
  const [items, setItems] = useState(null)
  const [meta, setMeta] = useState({ folders: [], tags: [] })
  const [open, setOpen] = useState(null)
  const [usage, setUsage] = useState(null)
  const [editing, setEditing] = useState(null)
  const [unsplash, setUnsplash] = useState(false)
  const fileRef = useRef(null)
  const folder = query.folder ?? '*'
  // only the newest load may set items: a search typed after another one (or a filter change) used to be overwritten when
  // the earlier, slower answer arrived last (the same stale-response race as the Calendar list)
  const loadSeq = useRef(0)
  const load = () => {
    const seq = ++loadSeq.current
    const p = new URLSearchParams()
    if (query.q) p.set('q', query.q)
    if (folder !== '*') p.set('folder', folder)
    if (query.tag) p.set('tag', query.tag)
    if (query.kind) p.set('kind', query.kind)
    if (query.smart) p.set(query.smart, 'true')
    get('/api/media?' + p).then((r) => { if (seq === loadSeq.current) setItems(r) }).catch((e) => toast(e.message, 'err'))
    get('/api/media-folders').then(setMeta).catch(() => {})
  }
  useEffect(load, [query.q, folder, query.tag, query.kind, query.smart])
  useEffect(() => { if (open) get(`/api/media/${open.id}/usage`).then(setUsage); else setUsage(null) }, [open?.id])

  const onFiles = async (files) => {
    if (!files?.length) return
    try {
      const r = await upload(files)
      if (folder !== '*' && folder) for (const m of r.media) await patch(`/api/media/${m.id}`, { folder })
      toast(`${r.media.length} file(s) added${r.errors.length ? ` · ${r.errors.length} skipped` : ''}`)
      load()
    } catch (e) { toast(e.message, 'err') }
  }
  const fromUrl = async () => {
    const url = await promptAsync('Paste an https URL of an image or video', { title: 'Add from URL', placeholder: 'https://…', ok: 'Add' })
    if (!url) return
    try { await post('/api/media/from-url', { url }); toast('Added from URL'); load() } catch (e) { toast(e.message, 'err') }
  }
  const save = async (body) => {
    try { const m = await patch(`/api/media/${open.id}`, body); setOpen({ ...open, ...m }); toast('Saved'); load() } catch (e) { toast(e.message, 'err') }
  }
  const remove = async (m, force = false) => {
    if (!force && !(await confirmAsync(`Delete ${m.filename}?`, { ok: 'Delete' }))) return
    try { await del(`/api/media/${m.id}${force ? '?force=true' : ''}`); toast('Deleted'); setOpen(null); load() }
    catch (e) {
      if (e.status === 409 && await confirmAsync(`${e.message}\n\nDelete anyway? Posts using it will lose this file.`, { ok: 'Delete anyway' })) return remove(m, true)
      if (e.status !== 409) toast(e.message, 'err')
    }
  }
  const newFolder = async () => { const n = await promptAsync('Folder name (use / for sub-folders)', { title: 'New folder', ok: 'Create' }); if (n) setQuery({ folder: n.trim() }) }

  return (
    <div className="space-y-5" data-testid="page-library">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-2xl font-semibold tracking-tight">Library</h1><p className="text-sm text-muted">Every image and video, with folders, tags, usage and duplicate detection. Free and unlimited.</p></div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => setUnsplash(true)} data-testid="library-unsplash"><Camera className="size-4" />Unsplash</Button>
          <Button variant="outline" onClick={fromUrl} data-testid="library-from-url"><Link2 className="size-4" />From URL</Button>
          <Button variant="primary" onClick={() => fileRef.current?.click()} data-testid="library-upload"><Upload className="size-4" />Upload</Button>
          <input ref={fileRef} type="file" multiple hidden accept="image/*,video/*,.pdf,application/pdf,.zip" onChange={(e) => onFiles([...e.target.files])} data-testid="library-file-input" />
        </div>
      </header>
      <div className="grid gap-5 lg:grid-cols-[220px_minmax(0,1fr)]">
        <aside className="space-y-4" data-testid="library-sidebar">
          <div className="space-y-1">
            {SMART.map(([k, l]) => (
              <button key={k} onClick={() => setQuery({ smart: k, folder: '' })} data-testid={`library-smart-${k || 'all'}`}
                className={cx('flex w-full cursor-pointer items-center gap-2 rounded-xl px-3 py-2 text-sm', (query.smart || '') === k && folder === '*' ? 'bg-lav-100 font-medium' : 'hover:bg-lav-50')}>
                {k === 'duplicates' ? <Copy className="size-4" /> : <ImageIcon className="size-4" />}{l}</button>
            ))}
          </div>
          <div>
            <div className="flex items-center justify-between px-3 text-[11px] font-semibold uppercase tracking-widest text-muted">Folders
              <button className="cursor-pointer" onClick={newFolder} aria-label="New folder" data-testid="library-new-folder"><FolderPlus className="size-4" /></button></div>
            {[...meta.folders.filter((f) => f.name), ...(folder !== '*' && folder && !meta.folders.some((f) => f.name === folder) ? [{ name: folder, count: 0 }] : [])].map((f) => (
              <button key={f.name} onClick={() => setQuery({ folder: f.name, smart: '' })} data-testid="library-folder"
                className={cx('mt-1 flex w-full cursor-pointer items-center gap-2 rounded-xl px-3 py-1.5 text-sm', folder === f.name ? 'bg-lav-100 font-medium' : 'hover:bg-lav-50')}
                style={{ paddingLeft: 12 + 12 * (f.name.split('/').length - 1) }}>
                <Folder className="size-4 text-lav-600" /><span className="truncate">{f.name.split('/').pop()}</span><span className="ml-auto text-xs text-muted">{f.count}</span></button>
            ))}
          </div>
          {meta.tags.length > 0 && <div><p className="px-3 text-[11px] font-semibold uppercase tracking-widest text-muted">Tags</p>
            <div className="mt-2 flex flex-wrap gap-1.5 px-2">{meta.tags.map((t) => (
              <button key={t.name} onClick={() => setQuery({ tag: query.tag === t.name ? '' : t.name })} data-testid="library-tag-chip">
                <Badge tone={query.tag === t.name ? 'lav' : 'gray'}>#{t.name} · {t.count}</Badge></button>))}</div></div>}
        </aside>
        <section className="min-w-0 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative"><Search className="absolute left-3 top-2.5 size-4 text-muted" />
              <Input className="w-72 pl-9" placeholder="Search files, titles, alt text, tags" value={query.q || ''} onChange={(e) => setQuery({ q: e.target.value })} data-testid="library-search" aria-label="Search media" /></div>
            {['', 'image', 'video'].map((k) => <Button key={k} size="sm" variant={(query.kind || '') === k ? 'primary' : 'ghost'} onClick={() => setQuery({ kind: k })} data-testid={`library-kind-${k || 'all'}`}>{k ? k[0].toUpperCase() + k.slice(1) + 's' : 'All types'}</Button>)}
            {folder !== '*' && <Badge tone="lav" data-testid="library-current-folder"><Folder className="mr-1 inline size-3" />{folder || 'No folder'}<button className="ml-1 cursor-pointer" onClick={() => setQuery({ folder: '' })} aria-label="All folders"><X className="inline size-3" /></button></Badge>}
          </div>
          <div onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); onFiles([...e.dataTransfer.files]) }}
            className="min-h-64 rounded-3xl border border-dashed border-lav-200 p-3" data-testid="library-dropzone">
            {items === null ? <SkeletonList rows={2} /> : !items.length ? (
              <Empty icon={ImageIcon} title={query.smart === 'duplicates' ? 'No duplicates' : 'Nothing here yet'} testid="library-empty">Drop images, videos or a .zip here, or use Upload.</Empty>
            ) : (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
                {items.map((m) => (
                  <motion.button layout key={m.id} onClick={() => setOpen(m)} data-testid="library-item" data-id={m.id}
                    className={cx('group relative cursor-pointer overflow-hidden rounded-2xl border bg-surface text-left shadow-soft transition hover:-translate-y-0.5', m.duplicate_group ? 'border-amber-300' : 'border-lav-100')}>
                    <div className="aspect-square bg-lav-50">{m.kind === 'document' ? <div className="flex size-full flex-col items-center justify-center gap-1 bg-lav-50 text-[10px] font-semibold text-lav-700" data-testid="media-document"><span className="rounded bg-lav-600 px-1.5 py-0.5 text-white">PDF</span><span className="max-w-full truncate px-1">{m.filename}</span></div> : m.kind === 'video' ? <video src={m.url} className="size-full object-cover" muted /> : <img src={m.url} alt={m.alt_text || m.filename} className="size-full object-cover" loading="lazy" />}</div>
                    <div className="p-2"><p className="truncate text-xs font-medium">{m.title || m.filename}</p>
                      <p className="flex items-center gap-1 text-[11px] text-muted">{m.kind === 'video' ? <Video className="size-3" /> : <ImageIcon className="size-3" />}{m.uses ? `used ${m.uses}×` : 'unused'}{m.folder && ` · ${m.folder}`}</p></div>
                    {m.duplicate_group && <span className="absolute left-2 top-2"><Badge tone="amber">duplicate</Badge></span>}
                  </motion.button>
                ))}
              </div>
            )}
          </div>
        </section>
      </div>

      {open && (
        <div className="fixed inset-0 z-50 flex justify-end bg-night/30" onMouseDown={(e) => e.target === e.currentTarget && setOpen(null)}>
          <motion.aside initial={{ x: 40, opacity: 0 }} animate={{ x: 0, opacity: 1 }} className="h-full w-full max-w-md overflow-y-auto border-l border-lav-200 bg-surface p-5 shadow-pop" data-testid="library-drawer">
            <div className="flex items-center justify-between"><h2 className="truncate text-lg font-semibold">{open.filename}</h2><Button variant="ghost" size="icon" onClick={() => setOpen(null)} aria-label="Close" data-testid="library-drawer-close"><X className="size-4" /></Button></div>
            <div className="mt-3 overflow-hidden rounded-2xl bg-lav-50">{open.kind === 'document' ? <div className="flex size-full flex-col items-center justify-center gap-1 bg-lav-50 text-[10px] font-semibold text-lav-700" data-testid="media-document"><span className="rounded bg-lav-600 px-1.5 py-0.5 text-white">PDF</span><span className="max-w-full truncate px-1">{open.filename}</span></div> : open.kind === 'video' ? <video src={open.url} controls className="w-full" /> : <img src={open.url} alt={open.alt_text} className="w-full" />}</div>
            <div className="mt-4 space-y-3 text-sm">
              <label className="block">Title<Input defaultValue={open.title} onBlur={(e) => e.target.value !== open.title && save({ title: e.target.value })} data-testid="library-title" /></label>
              <label className="block">Alt text<Input defaultValue={open.alt_text} onBlur={(e) => e.target.value !== open.alt_text && save({ alt_text: e.target.value })} data-testid="library-alt" /></label>
              <label className="block">Folder<Input defaultValue={open.folder} placeholder="e.g. launches/2026" onBlur={(e) => e.target.value !== open.folder && save({ folder: e.target.value })} data-testid="library-folder-input" /></label>
              <label className="block"><span className="flex items-center gap-1"><Tag className="size-3.5" />Tags (comma separated)</span>
                <Input defaultValue={(open.tags || []).join(', ')} onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()} onBlur={(e) => save({ tags: e.target.value.split(',').map((x) => x.trim()).filter(Boolean) })} data-testid="library-tags-input" /></label>
              <p className="text-xs text-muted">{open.kind} · {open.width}×{open.height}{open.duration ? ` · ${Math.round(open.duration)} s` : ''} · {Math.round((open.size || 0) / 1024)} KB</p>
              <div data-testid="library-usage"><p className="font-medium">Used in {usage ? usage.uses : '…'} place(s)</p>
                <ul className="mt-1 space-y-1 text-xs text-ink-2">{usage?.posts.map((p) => <li key={p.id}><a data-testid="library-id" className="underline" href={`#/queue?post=${p.id}`}>{p.status}: {p.caption || '(no caption)'}</a></li>)}
                  {usage?.ideas.map((i) => <li key={i.id}>Idea: {i.title}</li>)}</ul></div>
              <div className="flex gap-2 pt-2">
                <Button variant="primary" onClick={() => go('/home', { compose: 'new', media: open.id })} data-testid="library-use">Use in a post</Button>
                {open.kind === 'image' && <Button variant="outline" onClick={() => setEditing(open)} data-testid="library-edit"><Crop className="size-4" />Edit image</Button>}
                <Button variant="danger" onClick={() => remove(open)} data-testid="library-delete"><Trash2 className="size-4" />Delete</Button>
              </div>
            </div>
          </motion.aside>
        </div>
      )}
      <UnsplashPicker open={unsplash} onClose={() => setUnsplash(false)} onPicked={(m) => { setOpen(m); load() }} />
      <ImageEditor media={editing} open={!!editing} onClose={() => setEditing(null)} onSaved={(m) => { setOpen(m); load() }} />
    </div>
  )
}
