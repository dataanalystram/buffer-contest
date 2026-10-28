// The Publish list's bulk bar: one /api/bulk call per action (each id goes through its own route on the server,
// so roles, approvals and the daily rate guard still apply per post). Failures are listed, never hidden.
import { useState } from 'react'
import { ArrowRightLeft, CalendarClock, Check, Copy, Download, Flag, ListOrdered, Tag, Trash2, Undo2, X, Zap } from 'lucide-react'
import { post } from '../api'
import { useStore } from '../store'
import { Button, Dialog, Field, Input, Select, confirmAsync, promptAsync, useToast } from '../ui'

export default function BulkBar({ picked, rows, tab, onPick, onDone }) {
  const { channels, library, can } = useStore()
  const toast = useToast()
  const [busy, setBusy] = useState('')
  const [dlg, setDlg] = useState(null)     // 'reschedule' | 'move'
  const [at, setAt] = useState('')
  const [shift, setShift] = useState('1')
  const [dest, setDest] = useState('')
  const all = rows.map((t) => t.id)
  const allOn = all.length > 0 && all.every((id) => picked.includes(id))

  const report = (r, verb) => {
    const fails = (r.results || []).filter((x) => !x.ok)
    const held = r.pending_approval ? ` · ${r.pending_approval} waiting for approval` : ''
    toast(`${r.count} post(s) ${verb}${held}`, fails.length ? 'err' : undefined)
    if (fails.length) toast(`${fails.length} skipped: ${[...new Set(fails.map((x) => x.error))].slice(0, 2).join(' · ')}`, 'err')
  }
  const run = async (key, path, body, verb) => {
    setBusy(key)
    try { report(await post(path, { ids: picked, ...body }), verb); setDlg(null); onDone() } catch (e) { toast(e.message, 'err') }
    setBusy('')
  }
  const bulk = (action, extra = {}, verb = 'updated') => run(action, '/api/bulk', { action, ...extra }, verb)

  const askTag = async () => {
    const t = await promptAsync('Tag to add', { placeholder: (library.tags || [])[0]?.id || 'launch', ok: 'Add tag' })
    if (t && t.trim()) bulk('tag', { tag: t.trim() }, 'tagged')
  }
  const askCampaign = async () => {
    const c = await promptAsync('Campaign name', { ok: 'Set campaign', placeholder: 'DevDay' })
    if (c) bulk('campaign', { campaign: c.trim() }, 'updated')
  }
  const csv = `/api/posts/export.csv?ids=${encodeURIComponent(picked.join(','))}`

  return (
    <>
      <div className="sticky top-0 z-10 flex flex-wrap items-center gap-2 rounded-2xl bg-ink px-4 py-2 text-sm text-surface shadow-pop" data-testid="bulk-bar">
        <label className="flex cursor-pointer items-center gap-2">
          <input type="checkbox" className="accent-lav-400" checked={allOn} aria-label="Select all" data-testid="bulk-select-all"
            onChange={(e) => onPick(e.target.checked ? all : [])} />
          <b data-testid="bulk-count">{picked.length} selected</b>
        </label>
        {tab === 'approvals' && can.approve && <>
          <Button size="sm" variant="soft" loading={busy === 'approve'} data-testid="bulk-approve"
            onClick={() => run('approve', '/api/bulk/approve', { decision: 'approve' }, 'approved')}><Check className="size-3.5" />Approve</Button>
          <Button size="sm" variant="soft" loading={busy === 'reject'} data-testid="bulk-reject"
            onClick={async () => { const note = await promptAsync('What should change?', { ok: 'Reject', placeholder: 'Note for the author' }); if (note) run('reject', '/api/bulk/approve', { decision: 'reject', note }, 'rejected') }}><X className="size-3.5" />Reject</Button>
        </>}
        {can.publish && tab !== 'sent' && <>
          <Button size="sm" variant="soft" onClick={() => bulk('queue', {}, 'added to the queue')} data-testid="bulk-queue"><ListOrdered className="size-3.5" />Add to queue</Button>
          <Button size="sm" variant="soft" onClick={() => setDlg('reschedule')} data-testid="bulk-reschedule"><CalendarClock className="size-3.5" />Reschedule</Button>
        </>}
        {tab !== 'sent' && <Button size="sm" variant="soft" onClick={() => bulk('draft', {}, 'moved to drafts')} data-testid="bulk-draft"><Undo2 className="size-3.5" />To drafts</Button>}
        {!can.publish && tab === 'drafts' && <Button size="sm" variant="soft" onClick={() => bulk('request_approval', {}, 'sent for approval')} data-testid="bulk-request-approval"><Zap className="size-3.5" />Request approval</Button>}
        <Button size="sm" variant="soft" onClick={askTag} data-testid="bulk-tag"><Tag className="size-3.5" />Tag</Button>
        <Button size="sm" variant="soft" onClick={askCampaign} data-testid="bulk-campaign"><Flag className="size-3.5" />Campaign</Button>
        <Button size="sm" variant="soft" onClick={() => bulk('duplicate', {}, 'duplicated as drafts')} data-testid="bulk-duplicate"><Copy className="size-3.5" />Duplicate</Button>
        {tab !== 'sent' && channels.length > 1 && <Button size="sm" variant="soft" onClick={() => { setDest(''); setDlg('move') }} data-testid="bulk-move"><ArrowRightLeft className="size-3.5" />Move</Button>}
        <a href={csv} download className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-white/10 px-3 text-[13px] font-medium hover:bg-white/20" data-testid="bulk-export"><Download className="size-3.5" />CSV</a>
        <Button size="sm" variant="danger" onClick={async () => (await confirmAsync(`Delete ${picked.length} posts?`, { ok: 'Delete' })) && bulk('delete', {}, 'deleted')} data-testid="bulk-delete"><Trash2 className="size-3.5" />Delete</Button>
        <Button data-testid="queue-clear" size="sm" variant="ghost" className="text-white" onClick={() => onPick([])}>Clear</Button>
      </div>

      <Dialog open={dlg === 'reschedule'} onClose={() => setDlg(null)} title={`Reschedule ${picked.length} post(s)`} testid="bulk-reschedule-dialog"
        footer={<>
          <Button data-testid="bulk-shift-apply" loading={busy === 'reschedule'} disabled={!Number(shift)} onClick={() => bulk('reschedule', { shift_minutes: Math.round(Number(shift) * 1440) }, 'moved')}>Shift by {shift || 0} day(s)</Button>
          <Button data-testid="bulk-at-apply" variant="primary" loading={busy === 'reschedule'} disabled={!at} onClick={() => bulk('reschedule', { scheduled_at: at, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone }, 'rescheduled')}>Set time</Button>
        </>}>
        <div className="space-y-4">
          <Field label="Move every post to one time" hint="In your browser's timezone. Each channel's daily limit still applies.">
            <Input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} data-testid="bulk-at" />
          </Field>
          <Field label="Or shift each post by days" hint="Negative moves them earlier. Unscheduled drafts are skipped.">
            <Input type="number" step="0.5" value={shift} onChange={(e) => setShift(e.target.value)} data-testid="bulk-shift" />
          </Field>
        </div>
      </Dialog>

      <Dialog open={dlg === 'move'} onClose={() => setDlg(null)} title={`Move ${picked.length} post(s) to another channel`} testid="bulk-move-dialog"
        footer={<Button data-testid="bulk-move-apply" variant="primary" disabled={!dest} loading={busy === 'move'} onClick={() => bulk('move', { channel_id: dest }, 'moved')}>Move</Button>}>
        <Field label="Channel" hint="Posts keep their schedule; the new channel's rules and approvals apply.">
          <Select value={dest} onChange={(e) => setDest(e.target.value)} data-testid="bulk-move-channel"
            options={[['', 'Pick a channel'], ...channels.map((c) => [c.id, `${c.provider} · @${c.username}`])]} />
        </Field>
      </Dialog>
    </>
  )
}
