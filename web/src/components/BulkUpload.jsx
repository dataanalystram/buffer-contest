// Per-channel "Bulk upload" (Buffer parity): pick a CSV (Buffer's template imports unchanged), choose Queue or Drafts,
// check every row (rows over the limit are flagged, never dropped), then import. Same API as agents:
// POST /api/import/csv/preview and POST /api/import/csv with channel_ids=[this channel] and when=queue|draft.
import { Download, Upload } from 'lucide-react'
import { useRef, useState } from 'react'
import { post } from '../api'
import { Badge, Button, cx, Dialog, useToast } from '../ui'

export default function BulkUpload({ ch, onDone }) {
  const toast = useToast()
  const fileRef = useRef(null)
  const [open, setOpen] = useState(false)
  const [csv, setCsv] = useState('')
  const [name, setName] = useState('')
  const [mode, setMode] = useState('queue')
  const [pre, setPre] = useState(null)
  const [busy, setBusy] = useState(false)
  const body = { csv, channel_ids: [ch.id], when: mode }
  const li = ch.provider === 'linkedin' || ch.provider === 'linkedin_page'
  const reset = () => { setCsv(''); setName(''); setPre(null) }
  const check = async (text = csv, m = mode) => {
    setBusy(true)
    try { setPre(await post('/api/import/csv/preview', { ...body, csv: text, when: m })) } catch (e) { toast(e.message, 'err'); setPre(null) } finally { setBusy(false) }
  }
  const onFile = async (f) => { if (!f) return; const t = await f.text(); setCsv(t); setName(f.name); check(t) }
  const run = async () => {
    setBusy(true)
    try {
      const r = await post('/api/import/csv', body)
      toast(`${r.imported} post${r.imported === 1 ? '' : 's'} added to ${mode === 'draft' ? 'Drafts' : 'the queue'}${r.failed ? ` · ${r.failed} skipped` : ''}`)
      setOpen(false); reset(); onDone?.()
    } catch (e) { toast(e.message, 'err') } finally { setBusy(false) }
  }
  return (
    <>
      <Button onClick={() => setOpen(true)} data-testid="channel-bulk-upload" aria-label={`Bulk upload posts to @${ch.username}`}><Upload className="size-4" />Bulk upload</Button>
      <Dialog open={open} onClose={() => { setOpen(false); reset() }} title={`Bulk upload to @${ch.username}`} testid="bulk-upload-dialog" wide={!!pre}
        footer={<div className="flex items-center justify-end gap-2">
          <Button variant="ghost" onClick={() => { setOpen(false); reset() }} data-testid="bulk-upload-cancel">Cancel</Button>
          <Button variant="primary" onClick={run} loading={busy && !!pre} disabled={!pre?.valid} data-testid="bulk-upload-run">
            {pre ? `Add ${pre.valid} to ${mode === 'draft' ? 'Drafts' : 'Queue'}` : 'Add posts'}</Button></div>}>
        <div className="space-y-4">
          <p className="text-sm text-muted">Columns: <b>Text</b>, <b>Image URL</b>, <b>Tags</b>, <b>Posting Time</b> (YYYY-MM-DD HH:mm, blank = next slot) - Buffer's template works as is.
            {li && ' LinkedIn also reads Format (text, image, multi_image, document, video, article), Document URL, Document Title, Alt Text, Link and First Comment.'} Up to 500 rows.</p>
          <div className="flex flex-wrap gap-2">
            <a href="/api/import/csv/template?style=buffer" download data-testid="bulk-upload-template-buffer" className="inline-flex h-8 items-center gap-1.5 rounded-xl border border-lav-200 px-3 text-[13px] font-medium hover:bg-lav-50"><Download className="size-3.5" />Buffer template</a>
            {li && <a href="/api/import/csv/template?style=linkedin" download data-testid="bulk-upload-template-linkedin" className="inline-flex h-8 items-center gap-1.5 rounded-xl border border-lav-200 px-3 text-[13px] font-medium hover:bg-lav-50"><Download className="size-3.5" />LinkedIn template</a>}
            <Button size="sm" variant="primary" onClick={() => fileRef.current?.click()} data-testid="bulk-upload-choose"><Upload className="size-3.5" />{name || 'Choose CSV'}</Button>
            <input ref={fileRef} type="file" accept=".csv,text/csv,text/plain" className="hidden" aria-label="CSV file" data-testid="bulk-upload-file" onChange={(e) => onFile(e.target.files?.[0])} />
          </div>
          <div role="radiogroup" aria-label="Add the posts to" className="inline-flex rounded-xl bg-lav-100 p-1" data-testid="bulk-upload-mode">
            {[['queue', 'Queue'], ['draft', 'Drafts']].map(([v, l]) => (
              <button key={v} type="button" role="radio" aria-checked={mode === v} data-testid={`bulk-upload-mode-${v}`} onClick={() => { setMode(v); if (csv) check(csv, v) }}
                className={cx('cursor-pointer rounded-lg px-3 py-1.5 text-[13px] font-medium', mode === v ? 'bg-surface text-ink shadow-soft' : 'text-muted hover:text-ink')}>{l}</button>))}
          </div>
          {pre && (
            <div className="space-y-2" data-testid="bulk-upload-preview">
              <div className="flex flex-wrap gap-2 text-sm"><Badge tone="green">{pre.valid} ready</Badge>
                {pre.invalid - (pre.over_limit || 0) > 0 && <Badge tone="red">{pre.invalid - (pre.over_limit || 0)} with errors</Badge>}
                {pre.over_limit > 0 && <Badge tone="amber" data-testid="bulk-upload-over-limit">{pre.over_limit} over the {pre.max_rows}-row limit</Badge>}
                {pre.unknown_columns?.length > 0 && <span className="text-xs text-muted">Ignored columns: {pre.unknown_columns.join(', ')}</span>}</div>
              <div className="max-h-[50vh] overflow-auto"><table className="w-full text-sm"><thead className="text-left text-xs text-muted"><tr><th className="py-1">Row</th><th>Text</th><th>When</th><th>Media</th><th>Issues</th></tr></thead>
                <tbody>{pre.rows.map((r) => (
                  <tr key={r.row} data-testid="bulk-upload-row" data-ok={r.ok ? '1' : '0'} className={cx('border-t border-lav-50 align-top', !r.ok && 'bg-rose-50/50')}>
                    <td className="py-1.5">{r.row}</td><td className="max-w-[320px] truncate">{r.caption}</td>
                    <td className="whitespace-nowrap">{r.when === 'custom' ? new Date(r.scheduled_at).toLocaleString() : r.when}</td><td>{r.media.length || ''}</td>
                    <td className="text-xs">{r.errors.map((e) => <div key={e} className="text-rose-700">{e}</div>)}{r.warnings.map((w) => <div key={w} className="text-amber-700">{w}</div>)}</td></tr>))}</tbody></table></div>
            </div>
          )}
        </div>
      </Dialog>
    </>
  )
}
