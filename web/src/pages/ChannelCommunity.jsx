// Channel page "Community": Comments · Mentions, status tabs, select many → bulk resolve / bulk reply (text or a saved
// reply), per comment: reply box pre-filled with @author, emoji picker, AI reply suggestions, Start a post, resolve.
import { AtSign, Check, CheckCheck, ExternalLink, Inbox, MessageSquareQuote, PenSquare, RefreshCw, Reply, RotateCcw, Send, Smile, Sparkles, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { get, post } from '../api'
import { go } from '../router'
import { useStore } from '../store'
import { Badge, Button, Card, cx, Empty, Input, Select, SkeletonList, Tabs, useOutside, useToast } from '../ui'

const when = (ts) => (ts ? new Date(ts).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '')
const EMOJIS = ['🙌', '🙏', '💜', '❤️', '🔥', '😂', '😍', '👏', '👀', '🤯', '🚀', '✨', '💯', '😊', '🤝', '👍', '🎉', '🤖', '🧠', '📈']

export function EmojiPicker({ onPick, testid = 'emoji' }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  useOutside(ref, () => setOpen(false))
  return (
    <div className="relative" ref={ref}>
      <Button type="button" size="icon" variant="ghost" aria-label="Insert emoji" aria-expanded={open} data-testid={`${testid}-toggle`} onClick={() => setOpen(!open)}><Smile className="size-4" /></Button>
      {open && (
        <div role="listbox" aria-label="Emoji" data-testid={`${testid}-picker`} className="absolute bottom-11 right-0 z-40 grid w-56 grid-cols-5 gap-1 rounded-xl border border-lav-200 bg-popover p-2 shadow-pop">
          {EMOJIS.map((e) => (
            <button key={e} type="button" role="option" aria-selected="false" data-testid={`${testid}-option`} className="cursor-pointer rounded-lg p-1 text-lg hover:bg-lav-100"
              onClick={() => { onPick(e); setOpen(false) }}>{e}</button>
          ))}
        </div>
      )}
    </div>
  )
}

function CommentRow({ c, ch, selected, onSelect, onChange }) {
  const toast = useToast()
  const [text, setText] = useState('')
  const [busy, setBusy] = useState('')
  const [sugg, setSugg] = useState(null)
  const input = useRef(null)
  const prefill = () => { if (!text) setText(c.author ? `@${c.author} ` : '') }
  const send = async () => {
    setBusy('send')
    try { await post(`/api/comments/${c.id}/reply`, { text }); setText(''); setSugg(null); toast('Reply posted'); onChange() } catch (e) { toast(e.message, 'err') } finally { setBusy('') }
  }
  const status = async (action) => {
    try { await post(`/api/comments/${c.id}/${action}`); toast(action === 'resolve' ? 'Marked as done' : 'Reopened'); onChange() } catch (e) { toast(e.message, 'err') }
  }
  const suggest = async () => {
    setBusy('ai')
    try { setSugg(await post('/api/comments/suggest-reply', { comment_id: c.id })) } catch (e) { toast(e.message, 'err') } finally { setBusy('') }
  }
  const startPost = async () => {
    try {
      const d = await post('/api/comments/start-post', { comment_id: c.id })
      go(`/channel/${ch.id}`, { tab: 'publish', compose: 'new', channels: ch.id, text: d.caption })
    } catch (e) { toast(e.message, 'err') }
  }
  return (
    <Card className={cx('space-y-2 p-4', selected && 'ring-2 ring-lav-400')} data-testid="channel-comment" data-comment={c.id} data-kind={c.kind}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <input type="checkbox" checked={selected} onChange={(e) => onSelect(e.target.checked)} aria-label={`Select comment from @${c.author}`} data-testid="comment-select" className="size-4 cursor-pointer accent-lav-600" />
        <b>@{c.author}</b>
        {c.kind === 'mention' && <Badge tone="lav" data-testid="comment-mention-badge"><AtSign className="size-3" />tagged you</Badge>}
        <span className="text-xs text-muted">{when(c.at)}</span>
        {c.status !== 'open' && <Badge tone="green" data-testid="comment-status">{c.status}</Badge>}
        {c.media_permalink && <a data-testid="channel-community-on" className="ml-auto flex items-center gap-1 text-xs text-lav-700" href={c.media_permalink} target="_blank" rel="noreferrer">on “{(c.media_caption || 'post').slice(0, 40)}”<ExternalLink className="size-3" /></a>}
      </div>
      <p className="whitespace-pre-line">{c.text}</p>
      {(c.my_replies || []).map((r, i) => <p key={i} className="ml-4 border-l-2 border-lav-200 pl-3 text-sm text-ink-2"><Reply className="mr-1 inline size-3" />{r.text}</p>)}
      {sugg && (
        <div className="space-y-1.5 rounded-xl bg-lav-50 p-3" data-testid="ai-suggestions">
          <div className="flex items-center gap-2 text-xs text-muted"><Sparkles className="size-3.5 text-lav-600" />{sugg.source === 'llm' ? 'AI suggestions - pick one to edit, nothing is sent yet' : 'Quick replies'}{sugg.note && <span>· {sugg.note}</span>}
            <button type="button" className="ml-auto cursor-pointer" aria-label="Close suggestions" onClick={() => setSugg(null)}><X className="size-3.5" /></button></div>
          {sugg.suggestions.map((s) => (
            <button key={s} type="button" data-testid="ai-suggestion" onClick={() => { setText(s); input.current?.focus() }}
              className="block w-full cursor-pointer rounded-lg border border-lav-200 bg-surface px-3 py-2 text-left text-sm hover:border-lav-500">{s}</button>
          ))}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Input ref={input} className="min-w-56 flex-1" value={text} onFocus={prefill} onChange={(e) => setText(e.target.value)} placeholder={`Reply to @${c.author}`} aria-label={`Reply to @${c.author}`} data-testid="channel-comment-input"
          onKeyDown={(e) => e.key === 'Enter' && text.trim() && send()} />
        <EmojiPicker testid="comment-emoji" onPick={(e) => setText((t) => (t || (c.author ? `@${c.author} ` : '')) + e)} />
        <Button variant="soft" loading={busy === 'ai'} onClick={suggest} data-testid="comment-ai-suggest"><Sparkles className="size-4" />AI reply</Button>
        <Button variant="primary" disabled={!text.trim()} loading={busy === 'send'} onClick={send} data-testid="channel-comment-send"><Send className="size-4" />Reply</Button>
      </div>
      <div className="flex flex-wrap gap-1">
        {c.status === 'resolved'
          ? <Button size="sm" variant="ghost" onClick={() => status('reopen')} data-testid="comment-reopen"><RotateCcw className="size-3.5" />Reopen</Button>
          : <Button size="sm" variant="ghost" onClick={() => status('resolve')} data-testid="comment-resolve"><Check className="size-3.5" />Mark done</Button>}
        <Button size="sm" variant="ghost" onClick={startPost} data-testid="comment-start-post"><PenSquare className="size-3.5" />Start a post</Button>
      </div>
    </Card>
  )
}

function BulkBar({ ids, onDone, onClear }) {
  const toast = useToast()
  const { library } = useStore()
  const replies = library?.saved_replies || []
  const [mode, setMode] = useState('')
  const [text, setText] = useState('')
  const [saved, setSaved] = useState('')
  const [busy, setBusy] = useState(false)
  const run = async (body) => {
    setBusy(true)
    try {
      const r = await post('/api/comments/bulk', { ids, ...body })
      toast(r.failed ? `${r.ok} done, ${r.failed} failed` : `${r.ok} comment${r.ok === 1 ? '' : 's'} ${body.action === 'reply' ? 'replied to' : body.action === 'resolve' ? 'marked done' : 'reopened'}`, r.failed ? 'err' : undefined)
      setMode(''); setText(''); setSaved(''); onDone()
    } catch (e) { toast(e.message, 'err') } finally { setBusy(false) }
  }
  return (
    <div className="sticky top-2 z-30 flex flex-wrap items-center gap-2 rounded-2xl border border-lav-300 bg-surface p-3 shadow-pop" data-testid="comments-bulk-bar">
      <b className="text-sm" data-testid="comments-selected-count">{ids.length} selected</b>
      <Button size="sm" variant="soft" loading={busy && mode === ''} onClick={() => run({ action: 'resolve' })} data-testid="bulk-resolve"><CheckCheck className="size-3.5" />Mark done</Button>
      <Button size="sm" variant="ghost" onClick={() => run({ action: 'reopen' })} data-testid="bulk-reopen"><RotateCcw className="size-3.5" />Reopen</Button>
      <Button size="sm" variant={mode === 'reply' ? 'primary' : 'soft'} onClick={() => setMode(mode === 'reply' ? '' : 'reply')} data-testid="bulk-reply-toggle"><MessageSquareQuote className="size-3.5" />Reply to all</Button>
      <Button size="sm" variant="ghost" className="ml-auto" onClick={onClear} data-testid="bulk-clear"><X className="size-3.5" />Clear</Button>
      {mode === 'reply' && (
        <div className="flex w-full flex-wrap items-center gap-2">
          <Select value={saved} onChange={(e) => { setSaved(e.target.value); if (e.target.value) setText('') }} aria-label="Saved reply" data-testid="bulk-saved-reply"
            options={[['', 'Write a reply…'], ...replies.map((r) => [r.id, `Saved: ${r.name}`])]} />
          {!saved && <Input className="min-w-56 flex-1" value={text} onChange={(e) => setText(e.target.value)} placeholder="Reply (each one starts with @author; {author} works too)" aria-label="Bulk reply text" data-testid="bulk-reply-text" />}
          {saved && <span className="flex-1 truncate text-sm text-ink-2" data-testid="bulk-saved-preview">{replies.find((r) => r.id === saved)?.text}</span>}
          <Button variant="primary" loading={busy} disabled={!saved && !text.trim()} onClick={() => run({ action: 'reply', text, saved_reply_id: saved })} data-testid="bulk-reply-send"><Send className="size-4" />Send {ids.length} replies</Button>
        </div>
      )}
    </div>
  )
}

export function CommunityTab({ ch, connector }) {
  const toast = useToast()
  const [kind, setKind] = useState('comment')
  const [status, setStatus] = useState('open')
  const [rows, setRows] = useState(null)
  const [meta, setMeta] = useState(null)
  const [sel, setSel] = useState([])
  const [busy, setBusy] = useState(false)
  const load = async () => {
    try {
      const st = status === 'all' ? 'all' : status
      if (kind === 'mention') {
        const d = await get(`/api/channels/${ch.id}/mentions?status=${st}`)
        setMeta(d); setRows(d.items)
      } else {
        const d = await get(`/api/channels/${ch.id}/community?view=list&status=${st}`)
        setMeta(d); setRows(d.comments)
      }
    } catch (e) { toast(e.message, 'err'); setRows((x) => x || []) }
  }
  useEffect(() => { setRows(null); setSel([]); load() }, [ch.id, kind, status])
  const sync = async () => {
    setBusy(true)
    try {
      const r = kind === 'mention' ? await post(`/api/channels/${ch.id}/mentions/sync`) : await post('/api/comments/sync', { channel_id: ch.id })
      toast(`${r.new} new ${kind === 'mention' ? 'mention' : 'item'}${r.new === 1 ? '' : 's'}`); await load()
    } catch (e) { toast(e.message, 'err') } finally { setBusy(false) }
  }
  if (connector && !connector.capabilities.comments) return <Empty icon={Inbox} title="No comment inbox for this network" testid="channel-community-unsupported">{connector.capabilities.comments_note}</Empty>
  const mentionsOff = kind === 'mention' && meta && meta.supported === false
  const allIds = (rows || []).map((c) => c.id)
  return (
    <div className="space-y-4" data-testid="channel-community">
      <div className="flex flex-wrap items-center gap-3">
        <Tabs testid="channel-community-kind" value={kind} onChange={setKind} items={[['comment', 'Comments'], ['mention', 'Mentions']]} />
        <Tabs testid="channel-comments-status" value={status} onChange={setStatus} items={[['open', 'Unanswered'], ['replied', 'Replied'], ['resolved', 'Resolved'], ['all', 'All']]} />
        <Button className="ml-auto" variant="outline" loading={busy} disabled={mentionsOff} onClick={sync} data-testid="channel-comments-sync"><RefreshCw className="size-4" />{kind === 'mention' ? 'Pull mentions' : 'Pull comments on recent posts'}</Button>
      </div>
      {sel.length > 0 && <BulkBar ids={sel} onClear={() => setSel([])} onDone={() => { setSel([]); load() }} />}
      {rows === null ? <SkeletonList rows={3} testid="channel-community-loading" /> : mentionsOff ? (
        <Empty icon={AtSign} title="Mentions aren’t available here" testid="channel-mentions-unsupported">{meta.message}</Empty>
      ) : !rows.length ? (
        <Empty icon={kind === 'mention' ? AtSign : Inbox} title={kind === 'mention' ? 'No mentions here' : 'Inbox zero'} testid="channel-community-empty">
          {kind === 'mention' ? `Posts that tag @${ch.username} show up here. Press “Pull mentions” to check.` : `No comments here. Press “Pull comments” to fetch the latest from @${ch.username}.`}
        </Empty>
      ) : (
        <div className="space-y-3">
          <label className="flex items-center gap-2 text-xs text-muted">
            <input type="checkbox" className="size-4 cursor-pointer accent-lav-600" data-testid="comments-select-all" aria-label="Select all"
              checked={sel.length === allIds.length && allIds.length > 0} onChange={(e) => setSel(e.target.checked ? allIds : [])} />Select all ({allIds.length})
          </label>
          {rows.map((c) => <CommentRow key={c.id} c={c} ch={ch} selected={sel.includes(c.id)} onChange={load}
            onSelect={(on) => setSel((s) => (on ? [...new Set([...s, c.id])] : s.filter((x) => x !== c.id)))} />)}
        </div>
      )}
    </div>
  )
}
