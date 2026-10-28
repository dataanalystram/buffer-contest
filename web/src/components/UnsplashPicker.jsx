// Unsplash photo picker (free key in Settings › App keys): search, click a photo to copy it into the Library with
// the photographer credit. Backed by /api/media/unsplash/* (actions media.unsplash_search / media.unsplash_import).
import { Search } from 'lucide-react'
import { useState } from 'react'
import { get, post } from '../api'
import { Button, Dialog, Input, Select, useToast } from '../ui'

export default function UnsplashPicker({ open, onClose, onPicked }) {
  const toast = useToast()
  const [q, setQ] = useState('')
  const [orientation, setOrientation] = useState('')
  const [d, setD] = useState(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const search = async (page = 1) => {
    setBusy('search'); setErr('')
    try { setD(await get(`/api/media/unsplash/search?${new URLSearchParams({ q, page, orientation })}`)) } catch (e) { setErr(e.message) } finally { setBusy('') }
  }
  const pick = async (p) => {
    setBusy(p.id)
    try { const r = await post('/api/media/unsplash/import', { id: p.id }); toast(`Added · ${r.credit}`); onPicked?.(r.media, r); onClose() } catch (e) { toast(e.message, 'err') } finally { setBusy('') }
  }
  return (
    <Dialog open={open} onClose={onClose} title="Free photos from Unsplash" wide testid="unsplash">
      <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); search(1) }}>
        <div className="relative flex-1"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted" />
          <Input className="pl-9" autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search photos, e.g. data center" data-testid="unsplash-q" aria-label="Search Unsplash" /></div>
        <Select value={orientation} onChange={(e) => setOrientation(e.target.value)} options={[['', 'Any shape'], ['landscape', 'Landscape'], ['portrait', 'Portrait'], ['squarish', 'Square']]} data-testid="unsplash-orientation" aria-label="Orientation" />
        <Button type="submit" variant="primary" loading={busy === 'search'} disabled={!q.trim()} data-testid="unsplash-search">Search</Button>
      </form>
      {err && <p className="mt-3 rounded-xl bg-amber-50 p-3 text-sm text-amber-900" data-testid="unsplash-error">{err}{err.includes('App keys') && <> <a className="underline" href="#/settings?section=app-keys" data-testid="unsplash-keys-link">Open App keys</a></>}</p>}
      {d && (
        <>
          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4" data-testid="unsplash-results">
            {d.results.map((p) => (
              <figure key={p.id} className="overflow-hidden rounded-2xl border border-lav-100">
                <button onClick={() => pick(p)} disabled={!!busy} className="block aspect-[4/3] w-full cursor-pointer bg-lav-50" style={{ backgroundColor: p.color }} aria-label={`Use ${p.alt || 'photo'} by ${p.photographer}`} data-testid="unsplash-photo">
                  <img src={p.thumb} alt={p.alt} className="size-full object-cover" loading="lazy" />
                </button>
                <figcaption className="truncate px-2 py-1 text-[11px] text-muted">{busy === p.id ? 'Adding…' : <>Photo by <a href={p.photographer_url} target="_blank" rel="noreferrer" className="underline" data-testid="unsplash-credit">{p.photographer}</a> on <a href={p.unsplash_url} target="_blank" rel="noreferrer" className="underline" data-testid="unsplash-link">Unsplash</a></>}</figcaption>
              </figure>
            ))}
          </div>
          {!d.results.length && <p className="mt-4 text-sm text-muted">No photos found.</p>}
          {d.total_pages > 1 && <div className="mt-3 flex items-center justify-center gap-2 text-sm">
            <Button size="sm" variant="ghost" disabled={d.page <= 1} onClick={() => search(d.page - 1)} data-testid="unsplash-prev">Previous</Button>
            <span className="text-muted">Page {d.page} of {d.total_pages}</span>
            <Button size="sm" variant="ghost" disabled={d.page >= d.total_pages} onClick={() => search(d.page + 1)} data-testid="unsplash-next">Next</Button></div>}
        </>
      )}
    </Dialog>
  )
}
