// Approve / Reject with a note (Buffer-style approvals) and the comment thread on a post (internal notes with @mentions).
import { MessageSquare, Send, ShieldCheck, Trash2, XCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { del, get, post } from '../api'
import { useStore } from '../store'
import { Button, Dialog, Textarea, useToast } from '../ui'

export function DecisionDialog({ target, mode, onClose, onDone }) {
  const toast = useToast()
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => setNote(''), [target, mode])
  const reject = mode === 'reject'
  const human = target?.approval?.kind === 'human'
  const submit = async () => {
    setBusy(true)
    try {
      if (reject) await post(`/api/targets/${target.id}/reject`, { note })
      else {
        try { await post(`/api/targets/${target.id}/approve`, { note }) } catch (e) {
          if (e.status === 409 && !Object.keys(e.detail?.issues || {}).length && confirm(`${e.message}\n\nApprove anyway as major news?`)) await post(`/api/targets/${target.id}/approve`, { note, override_rate_limit: true })
          else throw e
        }
      }
      toast(reject ? 'Sent back with your note - moved to drafts' : 'Approved and scheduled')
      onDone?.(); onClose()
    } catch (e) { toast(e.message, 'err') } finally { setBusy(false) }
  }
  return (
    <Dialog open={!!target && !!mode} onClose={onClose} title={reject ? 'Reject and request changes' : 'Approve post'} testid={reject ? 'reject-dialog' : 'approve-dialog'}
      footer={<><Button data-testid="approvals-cancel" variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant={reject ? 'danger-ghost' : 'accent'} loading={busy} disabled={reject && human && !note.trim()} onClick={submit} data-testid={reject ? 'reject-confirm' : 'approve-confirm'}>
          {reject ? <><XCircle className="size-4" />Reject</> : <><ShieldCheck className="size-4" />Approve</>}</Button></>}>
      {target && <div className="space-y-3">
        <p className="line-clamp-3 rounded-xl bg-lav-50 p-3 text-sm text-ink-2">{target.caption || <em>No caption</em>}</p>
        <p className="text-xs text-muted">Requested by <b>{target.approval?.requested_by}</b>{target.approval?.when ? ` · ${target.approval.when}${target.approval.scheduled_at ? ` at ${target.approval.scheduled_at}` : ''}` : ''}</p>
        <Textarea rows={3} autoFocus value={note} onChange={(e) => setNote(e.target.value)} data-testid="decision-note"
          placeholder={reject ? 'What needs to change? (they get this note)' : 'Add a note (optional)'} aria-label="Note" />
        {reject && human && <p className="text-xs text-muted">A note is required when you send a teammate's post back.</p>}
      </div>}
    </Dialog>
  )
}

export function Comments({ postId, initialOpen = false }) {
  const { can, me } = useStore()
  const toast = useToast()
  const [open, setOpen] = useState(initialOpen)
  const [rows, setRows] = useState(null)
  const [text, setText] = useState('')
  const [people, setPeople] = useState([])
  const load = () => get(`/api/posts/${postId}/notes`).then(setRows).catch(() => setRows([]))
  useEffect(() => { if (open) { load(); get('/api/team/directory').then(setPeople).catch(() => {}) } }, [open, postId])
  const at = text.match(/@([\w.-]*)$/)
  const hits = at ? people.filter((p) => p.id !== me?.id && (p.handle || '').toLowerCase().startsWith(at[1].toLowerCase())).slice(0, 5) : []
  const send = async () => {
    if (!text.trim()) return
    try { await post(`/api/posts/${postId}/notes`, { text }); setText(''); load() } catch (e) { toast(e.message, 'err') }
  }
  return (
    <div data-testid="post-comments">
      <button className="inline-flex cursor-pointer items-center gap-1 text-xs font-medium text-lav-700 hover:underline" data-testid="post-comments-toggle" onClick={() => setOpen(!open)}>
        <MessageSquare className="size-3.5" />{open ? 'Hide comments' : `Comments${rows?.length ? ` (${rows.length})` : ''}`}
      </button>
      {open && (
        <div className="mt-2 space-y-2 rounded-xl border border-lav-100 bg-lav-50/40 p-3">
          <ul className="space-y-2" data-testid="comment-list">
            {(rows || []).map((n) => (
              <li key={n.id} className="group text-[13px]" data-testid="comment">
                <span className="font-semibold">{n.author}</span> <time className="text-[11px] text-muted">{new Date(n.at).toLocaleString()}</time>
                {(n.author_id === me?.id || can.manage_team) && <button aria-label="Delete comment" data-testid="comment-delete" className="ml-1 cursor-pointer text-muted opacity-0 hover:text-rose-600 group-hover:opacity-100"
                  onClick={async () => { await del(`/api/posts/${postId}/notes/${n.id}`); load() }}><Trash2 className="inline size-3" /></button>}
                <p className="whitespace-pre-wrap text-ink-2">{n.text.split(/(@[\w.-]+)/).map((part, i) => part.startsWith('@') ? <b key={i} className="text-lav-700">{part}</b> : part)}</p>
              </li>
            ))}
            {rows && !rows.length && <li className="text-xs text-muted">No comments yet.</li>}
          </ul>
          {can.comment && (
            <div className="relative">
              <div className="flex gap-2">
                <Textarea rows={1} value={text} onChange={(e) => setText(e.target.value)} placeholder="Leave a comment… @mention a teammate" data-testid="comment-input" aria-label="Comment"
                  onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }} />
                <Button size="icon" variant="primary" aria-label="Send comment" onClick={send} data-testid="comment-send"><Send className="size-4" /></Button>
              </div>
              {hits.length > 0 && <ul className="absolute bottom-full left-0 z-10 mb-1 w-60 rounded-xl border border-lav-200 bg-popover p-1 shadow-pop" data-testid="mention-suggestions">
                {hits.map((p) => <li key={p.id}><button data-testid="approvals-settext" className="w-full cursor-pointer rounded-lg px-2 py-1.5 text-left text-sm hover:bg-lav-100" onClick={() => setText(text.replace(/@[\w.-]*$/, `@${p.handle} `))}>@{p.handle} <span className="text-xs text-muted">{p.name}</span></button></li>)}
              </ul>}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
