import { ImagePlus, ListOrdered, Plus, Scissors, Trash2, X } from 'lucide-react'
import { useRef, useState } from 'react'
import { post, upload } from '../api'
import { Button, cx, Switch, Textarea, useToast } from '../ui'

// X counts every link as 23 and CJK / emoji as 2 (same rule as huhqueue/connectors/x.py weighted_len)
const URL_RE = /https?:\/\/\S+/g
export function weightedLen(text) {
  const t = text || ''
  let n = (t.match(URL_RE) || []).length * 23
  for (const ch of t.replace(URL_RE, '')) {
    const cp = ch.codePointAt(0)
    n += (cp <= 0x10ff || (cp >= 0x2000 && cp <= 0x200d) || (cp >= 0x2010 && cp <= 0x201f) || (cp >= 0x2032 && cp <= 0x2037)) ? 1 : 2
  }
  return n
}

// Parts are stored on the channel options as options.thread = [{text, media_ids}]; the API also takes plain strings.
export const asParts = (v) => (Array.isArray(v) ? v : []).map((p) => (typeof p === 'string' ? { text: p, media_ids: [] } : { text: p?.text || '', media_ids: p?.media_ids || [] }))

function Part({ i, part, limit, media, onChange, onRemove }) {
  const toast = useToast()
  const fileRef = useRef(null)
  const [busy, setBusy] = useState(false)
  const n = weightedLen(part.text)
  async function addFiles(files) {
    if (!files?.length) return
    setBusy(true)
    try {
      const r = await upload([...files])
      r.errors.forEach((e) => toast(e, 'err'))
      onChange({ ...part, media_ids: [...part.media_ids, ...r.media.map((m) => m.id)] }, r.media)
    } catch (e) { toast(e.message, 'err') } finally { setBusy(false) }
  }
  return (
    <li className="relative pl-8" data-testid="thread-part">
      <span className="absolute left-0 top-2 grid size-6 place-items-center rounded-full bg-lav-100 text-[11px] font-semibold text-lav-700">{i + 2}</span>
      <span className="absolute bottom-0 left-3 top-9 w-px bg-lav-200" aria-hidden />
      <div className="rounded-xl border border-lav-200 bg-surface">
        <Textarea rows={2} value={part.text} onChange={(e) => onChange({ ...part, text: e.target.value })} placeholder={`Post ${i + 2} of the thread…`}
          className="border-0 shadow-none focus-visible:ring-0" data-testid="thread-part-text" aria-label={`Thread post ${i + 2}`} />
        <div className="flex items-center gap-2 border-t border-lav-100 px-2 py-1">
          {part.media_ids.map((id) => (
            <span key={id} className="relative">
              {media[id]?.kind === 'video' ? <video src={`/files/${id}`} className="size-8 rounded object-cover" muted /> : <img src={`/files/${id}`} alt="" className="size-8 rounded object-cover" />}
              <button type="button" onClick={() => onChange({ ...part, media_ids: part.media_ids.filter((x) => x !== id) })} aria-label="Remove media"
                data-testid="thread-part-media-remove" className="absolute -right-1 -top-1 grid size-4 cursor-pointer place-items-center rounded-full bg-ink text-white"><X className="size-2.5" /></button>
            </span>
          ))}
          <input ref={fileRef} type="file" accept="image/*,video/*" multiple hidden onChange={(e) => { addFiles(e.target.files); e.target.value = '' }} data-testid="thread-part-file" aria-label={`Media for thread post ${i + 2}`} />
          <Button type="button" size="sm" variant="ghost" loading={busy} disabled={part.media_ids.length >= 4} onClick={() => fileRef.current?.click()}
            data-testid="thread-part-media" aria-label={`Add media to thread post ${i + 2}`}><ImagePlus className="size-3.5" /></Button>
          <span className={cx('ml-auto text-[11px] tabular-nums', n > limit ? 'font-semibold text-rose-600' : 'text-muted')} data-testid="thread-part-count">{n}/{limit}</span>
          <Button type="button" size="sm" variant="ghost" onClick={onRemove} data-testid="thread-part-remove" aria-label={`Remove thread post ${i + 2}`}><Trash2 className="size-3.5" /></Button>
        </div>
      </div>
    </li>
  )
}

// The thread editor: the main caption is post 1; these are posts 2..n, each published as a reply to the one before.
export default function ThreadEditor({ parts, setParts, limit = 280, provider = 'x', caption, onSplit, networks }) {
  const toast = useToast()
  const [numbering, setNumbering] = useState(false)
  const [media, setMedia] = useState({})
  const [busy, setBusy] = useState(false)
  const update = (i, p, added) => {
    if (added?.length) setMedia((m) => ({ ...m, ...Object.fromEntries(added.map((x) => [x.id, x])) }))
    setParts(parts.map((x, j) => (j === i ? p : x)))
  }
  async function split() {
    const text = [caption, ...parts.map((p) => p.text)].filter((t) => t?.trim()).join('\n\n')
    setBusy(true)
    try {
      const r = await post('/api/threads/split', { text, provider, numbering })
      onSplit(r.caption, r.thread.map((t) => ({ text: t, media_ids: [] })))
      toast(`Split into ${r.count} posts`)
    } catch (e) { toast(e.message, 'err') } finally { setBusy(false) }
  }
  const tooLong = weightedLen(caption) > limit
  return (
    <div className="space-y-3" data-testid="composer-thread">
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1.5 text-[13px] font-medium text-ink-2"><ListOrdered className="size-3.5 text-lav-600" />Thread <span className="font-normal text-muted">· {networks}</span></span>
        {parts.length > 0 && <span className="text-xs text-muted" data-testid="thread-count">{parts.length + 1} posts</span>}
        <div className="ml-auto flex items-center gap-2">
          <span className="flex items-center gap-1.5 text-xs text-muted"><Switch checked={numbering} onChange={setNumbering} label="Number posts (1/n)" testid="thread-numbering" />1/n</span>
          <Button type="button" size="sm" variant={tooLong ? 'primary' : 'soft'} loading={busy} disabled={!caption?.trim()} onClick={split} data-testid="thread-split"
            title="Break the text into posts that fit, at paragraphs and sentences"><Scissors className="size-3.5" />Split into thread</Button>
        </div>
      </div>
      {tooLong && !parts.length && <p className="text-xs text-amber-800" data-testid="thread-hint">The caption is over {limit} characters for {networks}. Split it into a thread, or turn on Long post (X Premium).</p>}
      {parts.length > 0 && (
        <ol className="space-y-2">
          {parts.map((p, i) => <Part key={i} i={i} part={p} limit={limit} media={media} onChange={(np, added) => update(i, np, added)} onRemove={() => setParts(parts.filter((_, j) => j !== i))} />)}
        </ol>
      )}
      <Button type="button" size="sm" variant="ghost" onClick={() => setParts([...parts, { text: '', media_ids: [] }])} disabled={parts.length >= 24} data-testid="thread-add">
        <Plus className="size-3.5" />{parts.length ? 'Add another post' : 'Add a post to make a thread'}
      </Button>
    </div>
  )
}
