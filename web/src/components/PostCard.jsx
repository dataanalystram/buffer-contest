import { motion } from 'motion/react'
import { CheckCircle2, Copy, Send, CopyPlus, Download, ExternalLink, GripVertical, Pencil, RefreshCw, Rocket, RotateCcw, ShieldCheck, Trash2, Undo2, XCircle } from 'lucide-react'
import { useState } from 'react'
import { del, get, post } from '../api'
import { go, parseHash } from '../router'
import { useStore } from '../store'
import { Badge, Button, ChannelAvatar, cx, Dialog, fmt, useToast, confirmAsync } from '../ui'
import { Comments, DecisionDialog } from './Approvals'

export const STATUS = {
  draft: ['Draft', 'gray'], pending_approval: ['Needs approval', 'amber'], scheduled: ['Scheduled', 'lav'],
  sending: ['Sending…', 'blue'], processing: ['Processing…', 'blue'], needs_action: ['Your turn', 'amber'],
  sent: ['Sent', 'green'], failed: ['Failed', 'red'], cancelled: ['Cancelled', 'gray'],
}

export function DuplicateDialog({ target, onClose, onDone }) {
  const { channels } = useStore()
  const [sel, setSel] = useState([])
  const toast = useToast()
  return (
    <Dialog open={!!target} onClose={onClose} title="Duplicate to channels" testid="duplicate-dialog"
      footer={<Button variant="primary" disabled={!sel.length} data-testid="duplicate-confirm" onClick={async () => {
        try { await post(`/api/targets/${target.id}/duplicate`, { channel_ids: sel, when: 'draft' }); toast(`Copied to ${sel.length} channel(s) as drafts`); onDone?.(); onClose() } catch (e) { toast(e.message, 'err') }
      }}>Copy as drafts</Button>}>
      <div className="grid gap-2">
        {channels.map((c) => (
          <label key={c.id} className="flex cursor-pointer items-center gap-3 rounded-xl border border-lav-100 p-3 hover:bg-lav-50">
            <input type="checkbox" className="accent-lav-600" checked={sel.includes(c.id)} data-testid="duplicate-channel"
              onChange={(e) => setSel(e.target.checked ? [...sel, c.id] : sel.filter((x) => x !== c.id))} />
            <ChannelAvatar ch={c} size={28} /><span className="text-sm">@{c.username} · {c.provider}</span>
          </label>
        ))}
      </div>
    </Dialog>
  )
}

export function Timeline({ id }) {
  const [ev, setEv] = useState(null)
  const [att, setAtt] = useState([])
  const open = () => Promise.all([get(`/api/targets/${id}/events`), get(`/api/targets/${id}/attempts`)])
    .then(([e, a]) => { setEv(e); setAtt(a) })
  return (
    <div>
      <button className="cursor-pointer text-xs font-medium text-lav-700 hover:underline" data-testid="post-timeline-toggle"
        onClick={() => (ev ? setEv(null) : open())}>{ev ? 'Hide timeline' : 'Show timeline'}</button>
      {ev && <ol className="mt-2 space-y-1 border-l-2 border-lav-200 pl-3 text-xs" data-testid="post-timeline">
        {ev.map((e, i) => <li key={i} className={cx(e.level === 'error' && 'text-rose-700', e.level === 'success' && 'text-emerald-700')}><time className="text-muted">{new Date(e.at).toLocaleString()}</time> · {e.message}</li>)}
      </ol>}
      {ev && att.length > 0 && <table className="mt-2 w-full text-left text-xs" data-testid="post-attempts">
        <thead className="text-muted"><tr><th className="font-medium">Try</th><th className="font-medium">Started</th><th className="font-medium">Outcome</th><th className="font-medium">Details</th></tr></thead>
        <tbody>{att.map((a) => (
          <tr key={a.attempt} className={cx(['failed', 'auth', 'unconfirmed', 'interrupted'].includes(a.outcome) && 'text-rose-700', a.outcome === 'published' && 'text-emerald-700')}>
            <td>{a.attempt}</td><td>{new Date(a.started_at).toLocaleString()}</td><td>{a.outcome}</td>
            <td className="break-all">{[a.http_status && `HTTP ${a.http_status}`, a.error_code, a.retry_after && `retry after ${a.retry_after}s`, a.error].filter(Boolean).join(' · ')}</td>
          </tr>))}</tbody>
      </table>}
    </div>
  )
}

const inTz = (iso, tz) => new Date(iso).toLocaleString(undefined, { timeZone: tz, weekday: 'short', day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })

export default function PostCard({ t, onChange, draggable, selected, onSelect, highlight, tz }) {
  const toast = useToast()
  const { can } = useStore()
  const [decide, setDecide] = useState(null)   // 'approve' | 'reject'
  const [dup, setDup] = useState(null)
  const [busy, setBusy] = useState('')
  const [gone, setGone] = useState(false)
  const [label, tone] = STATUS[t.status] || [t.status, 'gray']
  // optimistic: actions that move the post out of the current list hide it at once and restore it on error
  const run = async (name, fn, msg, optimistic = false) => {
    setBusy(name)
    if (optimistic) setGone(true)
    try { await fn(); msg && toast(msg); onChange?.() } catch (e) { if (optimistic) setGone(false); toast(e.message, 'err') } finally { setBusy('') }
  }
  if (gone) return null
  const edit = () => { const { path, query } = parseHash(); go(path, { ...query, compose: t.id }) }
  const ins = t.insights || {}
  return (
    <motion.article layout initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} data-testid="post-card" data-status={t.status} data-id={t.id}
      className={cx('group rounded-2xl border bg-surface p-4 shadow-soft transition', highlight ? 'border-lav-500 ring-4 ring-lav-200' : 'border-lav-200/70 hover:border-lav-300')}>
      <div className="flex gap-4">
        {draggable && <GripVertical className="mt-2 size-4 shrink-0 cursor-grab text-lav-300 group-hover:text-lav-500" aria-label="Drag to reorder" />}
        {onSelect && <input type="checkbox" aria-label="Select post" className="mt-2 accent-lav-600" checked={selected} onChange={(e) => onSelect(e.target.checked)} data-testid="post-select" />}
        <ChannelAvatar ch={t.channel} size={40} />
        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
            <b className="text-ink">@{t.channel.username}</b>
            <Badge tone={tone} data-testid="post-status">{label}</Badge>
            {t.prioritized && <Badge tone="ink">Top of queue</Badge>}
            {t.override_rate_limit && <Badge tone="red">Major news</Badge>}
            {t.source !== 'ui' && <Badge tone="blue">via {t.source}</Badge>}
            {t.tags?.map((g) => <Badge key={g} tone="gray">#{g}</Badge>)}
            {t.format && <Badge tone="gray" data-testid="post-format" className="capitalize">{t.format}</Badge>}
            {t.campaign && <Badge tone="blue" data-testid="post-campaign">{t.campaign}</Badge>}
            <span className="ml-auto" data-testid="post-time">{t.status === 'sent' ? `Sent ${tz ? inTz(t.published_at, tz) : new Date(t.published_at).toLocaleString()}` : t.scheduled_at && tz ? inTz(t.scheduled_at, tz) : t.scheduled_local ? `${t.scheduled_local}` : t.status === 'scheduled' ? 'No free slot' : ''}</span>
          </div>
          <p className="whitespace-pre-wrap text-[14px] leading-relaxed line-clamp-4">{t.caption || <em className="text-muted">No caption</em>}</p>
          {t.media.length > 0 && (
            <div className="flex gap-2">
              {t.media.slice(0, 6).map((m) => m.kind === 'video'
                ? <video key={m.id} src={m.url + '#t=0.5'} muted preload="metadata" className="size-16 rounded-lg object-cover" />
                : <img key={m.id} src={m.url} alt={m.alt_text} className="size-16 rounded-lg object-cover" />)}
            </div>
          )}
          {t.last_error && <p className="rounded-xl bg-rose-50 px-3 py-2 text-[13px] text-rose-800" role="alert">{t.last_error}</p>}
          {t.status === 'pending_approval' && <p className="rounded-xl bg-amber-50 px-3 py-2 text-[13px] text-amber-900" data-testid="post-approval-request">Requested by <b>{t.approval?.requested_by}</b>: {t.approval?.when}{t.approval?.scheduled_at ? ` at ${t.approval.scheduled_at}` : ''}{t.approval?.note ? ` · “${t.approval.note}”` : ''}{!can.approve && ' · waiting for an approver'}</p>}
          {t.status === 'draft' && t.approval?.decision === 'rejected' && <p className="rounded-xl bg-rose-50 px-3 py-2 text-[13px] text-rose-900" data-testid="post-changes-requested">Changes requested by <b>{t.approval.decided_by}</b>{t.approval.reject_note ? `: ${t.approval.reject_note}` : ''}</p>}
          {['scheduled', 'sent'].includes(t.status) && t.approval?.decision === 'approved' && <p className="text-xs text-emerald-700" data-testid="post-approved-by">✓ Approved by {t.approval.decided_by}{t.approval.decision_note ? `: “${t.approval.decision_note}”` : ''}</p>}
          {t.status === 'sent' && Object.keys(ins).length > 0 && (
            <div className="flex flex-wrap gap-4 text-xs text-ink-2" data-testid="post-insights">
              {['views', 'reach', 'likes', 'comments', 'shares', 'saved'].filter((k) => ins[k] !== undefined && ins[k] !== null).map((k) => <span key={k}><b className="text-ink">{fmt(ins[k])}</b> {k}</span>)}
              {ins.engagement_rate !== undefined && <span><b className="text-ink">{ins.engagement_rate}%</b> eng.</span>}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-1 pt-1">
            {can.write && ['draft', 'scheduled', 'failed', 'pending_approval'].includes(t.status) && <Button size="sm" variant="outline" onClick={edit} data-testid="post-edit"><Pencil className="size-3.5" />Edit</Button>}
            {can.needs_approval && t.status === 'draft' && <Button size="sm" variant="accent" data-testid="post-request-approval" onClick={() => run('ra', () => post(`/api/targets/${t.id}/request-approval`, { when: 'queue' }), 'Sent for approval', true)}><Send className="size-3.5" />Submit for approval</Button>}
            {can.publish && ['draft', 'scheduled'].includes(t.status) && <Button size="sm" variant="soft" loading={busy === 'now'} data-testid="post-publish-now" onClick={() => run('now', async () => {
              try { await post(`/api/targets/${t.id}/publish-now`) } catch (e) {
                if (e.status === 409 && await confirmAsync(`${e.message}\n\nPublish anyway as major news?`, { ok: 'Publish anyway' })) await post(`/api/targets/${t.id}/publish-now`, { override_rate_limit: true })
                else throw e
              }
            }, 'Publishing…')}><Rocket className="size-3.5" />Publish now</Button>}
            {can.publish && t.status === 'draft' && <Button size="sm" variant="ghost" data-testid="post-queue" onClick={() => run('q', () => post('/api/bulk', { action: 'queue', ids: [t.id] }), 'Added to queue', true)}><RotateCcw className="size-3.5" />Add to queue</Button>}
            {can.publish && t.status === 'scheduled' && <Button size="sm" variant="ghost" data-testid="post-to-drafts" onClick={() => run('d', () => post('/api/bulk', { action: 'draft', ids: [t.id] }), 'Moved to drafts', true)}><Undo2 className="size-3.5" />To drafts</Button>}
            {t.status === 'pending_approval' && can.approve && <>
              <Button size="sm" variant="accent" data-testid="post-approve" onClick={() => setDecide('approve')}><ShieldCheck className="size-3.5" />Approve</Button>
              <Button size="sm" variant="ghost" data-testid="post-reject" onClick={() => setDecide('reject')}><XCircle className="size-3.5" />Reject</Button>
            </>}
            {can.publish && t.status === 'failed' && <Button size="sm" variant="soft" loading={busy === 'retry'} data-testid="post-retry" onClick={() => run('retry', () => post(`/api/targets/${t.id}/retry`), 'Retrying…')}><RefreshCw className="size-3.5" />Retry</Button>}
            {can.publish && t.status === 'needs_action' && <Button size="sm" variant="accent" data-testid="post-mark-posted" onClick={() => run('m', () => post(`/api/targets/${t.id}/mark-posted`), 'Marked as posted')}><CheckCircle2 className="size-3.5" />Mark as posted</Button>}
            {t.status === 'sent' && t.permalink && <a className="inline-flex h-8 items-center gap-1.5 rounded-xl px-3 text-[13px] text-ink-2 hover:bg-lav-100" href={t.permalink} target="_blank" rel="noreferrer" data-testid="post-open"><ExternalLink className="size-3.5" />Open post</a>}
            {t.status === 'sent' && <Button size="sm" variant="ghost" data-testid="post-insights-refresh" onClick={() => run('i', () => post(`/api/targets/${t.id}/refresh-insights`), 'Insights refreshed')}><RefreshCw className="size-3.5" />Insights</Button>}
            <span className="ml-auto" />
            <Button size="icon-sm" variant="ghost" title="Copy caption" aria-label="Copy caption" data-testid="post-copy" onClick={() => { navigator.clipboard?.writeText(t.caption); toast('Caption copied') }}><Copy className="size-3.5" /></Button>
            {t.media[0] && <a className="inline-grid size-8 place-items-center rounded-xl text-ink-2 hover:bg-lav-100" href={t.media[0].url} download title="Download media" aria-label="Download media" data-testid="post-download"><Download className="size-3.5" /></a>}
            {can.write && <Button size="icon-sm" variant="ghost" title="Duplicate to other channels" aria-label="Duplicate to other channels" data-testid="post-duplicate" onClick={() => setDup(t)}><CopyPlus className="size-3.5" /></Button>}
            {can.write && !['sending', 'processing'].includes(t.status) && (can.publish || ['draft', 'pending_approval', 'failed'].includes(t.status)) && <Button size="icon-sm" variant="danger-ghost" title="Delete" aria-label="Delete post" data-testid="post-delete" onClick={async () => (await confirmAsync('Delete this post?', { ok: 'Delete' })) && run('del', () => del(`/api/targets/${t.id}`), 'Deleted', true)}><Trash2 className="size-3.5" /></Button>}
          </div>
          <div className="flex flex-wrap items-start gap-4"><Timeline id={t.id} /><Comments postId={t.post_id} initialOpen={highlight && t.status === 'pending_approval'} /></div>
        </div>
      </div>
      <DecisionDialog target={decide ? t : null} mode={decide} onClose={() => setDecide(null)} onDone={() => { setGone(true); onChange?.() }} />
      {dup && <DuplicateDialog target={dup} onClose={() => setDup(null)} onDone={onChange} />}
    </motion.article>
  )
}
