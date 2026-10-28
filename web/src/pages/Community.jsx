// Community: one inbox for every channel, organised around people. Each comment shows a commenter chip;
// opening it shows that person's whole history across posts and channels (a lightweight contact view).
// Triage: Unanswered / Answered / Flagged / Assigned, saved replies, assign, flag and mark done.
import { AnimatePresence, motion } from 'motion/react'
import { CheckCheck, ExternalLink, Flag, Inbox, Info, MessageSquareReply, MessagesSquare, RefreshCw, RotateCcw, Sparkles, UserPlus, X, Zap } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { get, patch, post } from '../api'
import { setQuery, useRoute } from '../router'
import { useStore } from '../store'
import { Badge, Button, ChannelAvatar, cx, Empty, Input, Select, SkeletonList, Tabs, Textarea, useOutside, useToast } from '../ui'
import { CommentInsights } from '../components/AiTools'

const FILTERS = [['open', 'Unanswered'], ['answered', 'Answered'], ['flagged', 'Flagged'], ['assigned', 'Assigned'], ['all', 'All']]
const HUES = [262, 330, 200, 160, 30, 290, 10, 220]
const hue = (s) => HUES[[...(s || '?')].reduce((n, ch) => n + ch.charCodeAt(0), 0) % HUES.length]
const when = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '')

function Initials({ name, size = 28 }) {
  return (
    <span className="grid shrink-0 place-items-center rounded-full font-semibold text-white" style={{ width: size, height: size, fontSize: size * 0.4, background: `linear-gradient(135deg, hsl(${hue(name)} 70% 62%), hsl(${hue(name) + 30} 65% 45%))` }}>
      {(name || '?').replace(/^@/, '').slice(0, 2).toUpperCase()}
    </span>
  )
}

function SavedReplies({ onPick }) {
  const { library } = useStore()
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  useOutside(ref, () => setOpen(false))
  return (
    <div className="relative" ref={ref}>
      <Button size="sm" variant="ghost" onClick={() => setOpen(!open)} data-testid="comment-saved-replies" title="Saved replies"><Zap className="size-3.5" />Saved replies</Button>
      <AnimatePresence>
        {open && (
          <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="absolute bottom-10 left-0 z-30 w-72 rounded-2xl border border-lav-200 bg-popover p-1.5 shadow-pop">
            {(library.saved_replies || []).map((r) => (
              <button key={r.id} onClick={() => { onPick(r.text); setOpen(false) }} data-testid="saved-reply" className="block w-full cursor-pointer rounded-xl px-3 py-2 text-left text-[13px] hover:bg-lav-50">
                <b>{r.name}</b><span className="block truncate text-xs text-muted">{r.text}</span>
              </button>
            ))}
            {!(library.saved_replies || []).length && <p className="px-3 py-2 text-xs text-muted">Add saved replies in Settings → Tags, hashtags & replies.</p>}
            <a data-testid="community-manage-saved-replies" href="#/settings?section=library" className="block rounded-xl px-3 py-2 text-xs font-medium text-lav-700 hover:bg-lav-50">Manage saved replies</a>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

export default function Community() {
  const { channels, providers } = useStore()
  const { query } = useRoute()
  const toast = useToast()
  const [all, setAll] = useState(null)
  const [draft, setDraft] = useState({})
  const [busy, setBusy] = useState('')
  const [assigning, setAssigning] = useState(null)
  const status = query.status || 'open'
  const view = query.view || 'list'
  const contact = query.contact || ''
  const load = () => {
    const p = new URLSearchParams()
    if (query.channel) p.set('channel_id', query.channel)
    return get('/api/comments?' + p).then(setAll)
  }
  // refetch when the filter changes, and whenever something else seeds/changes comments (huhq:refresh)
  useEffect(() => { load() }, [query.channel, status])
  useEffect(() => { const on = () => load(); window.addEventListener('huhq:refresh', on); return () => window.removeEventListener('huhq:refresh', on) }, [query.channel])
  const q = (query.q || '').toLowerCase()
  const matches = (c) => ({ open: c.status === 'open', answered: c.status !== 'open', flagged: c.flagged, assigned: !!c.assignee, all: true }[status] ?? (c.status === status))
  const rows = useMemo(() => (all || []).filter((c) => matches(c) && (!q || (c.text + ' ' + c.author).toLowerCase().includes(q))), [all, status, q])
  const counts = useMemo(() => Object.fromEntries(FILTERS.map(([k]) => [k, (all || []).filter((c) => ({ open: c.status === 'open', answered: c.status !== 'open', flagged: c.flagged, assigned: !!c.assignee, all: true }[k])).length])), [all])
  const byAuthor = useMemo(() => (all || []).reduce((m, c) => ((m[c.author] ||= []).push(c), m), {}), [all])
  const update = (id, patchObj) => setAll((xs) => xs.map((c) => (c.id === id ? { ...c, ...patchObj } : c)))   // optimistic

  const [syncNotes, setSyncNotes] = useState(null)   // null until a sync this visit; then the server's per-channel notes
  const hidden = (syncNotes ?? channels.filter((c) => c.comments_note).map((c) => ({ channel_id: c.id, username: c.username, note: c.comments_note }))).filter((n) => !query.channel || n.channel_id === query.channel)
  const sync = async () => { setBusy('sync'); try { const r = await post('/api/comments/sync', query.channel ? { channel_id: query.channel } : {}); toast(`${r.new} new comment(s)`); setSyncNotes(r.notes || []); load() } catch (e) { toast(e.message, 'err') } finally { setBusy('') } }
  const reply = async (c) => { setBusy(c.id); try { await post(`/api/comments/${c.id}/reply`, { text: draft[c.id] }); setDraft({ ...draft, [c.id]: '' }); toast('Reply sent'); load() } catch (e) { toast(e.message, 'err') } finally { setBusy('') } }
  const change = async (c, body, msg) => {
    const before = { ...c }
    update(c.id, body)
    try { const r = await patch(`/api/comments/${c.id}`, body); update(c.id, r); msg && toast(msg) } catch (e) { update(c.id, before); toast(e.message, 'err') }
  }
  const noComments = (providers?.connectors || []).filter((c) => !c.capabilities.comments)
  const groups = view === 'post' ? Object.entries(rows.reduce((m, c) => ((m[c.media_caption || c.media_external_id || 'Post'] ||= []).push(c), m), {})) : null

  const Chip = ({ name }) => (
    <button onClick={() => setQuery({ contact: name })} data-testid="commenter-chip" title={`See everything from @${name}`}
      className={cx('flex cursor-pointer items-center gap-1.5 rounded-full border py-0.5 pl-0.5 pr-2.5 text-[13px] transition', contact === name ? 'border-lav-500 bg-lav-100' : 'border-lav-200 bg-surface hover:border-lav-400')}>
      <Initials name={name} size={22} /><b className="font-semibold">@{name}</b>
      <span className="rounded-full bg-lav-100 px-1.5 text-[10px] text-lav-700">{(byAuthor[name] || []).length}</span>
    </button>
  )

  const Row = ({ c }) => {
    const ch = channels.find((x) => x.id === c.channel_id)
    return (
      <motion.article layout className={cx('rounded-2xl border bg-surface p-4 shadow-soft', c.flagged ? 'border-amber-300' : 'border-lav-200/70')} data-testid="comment-card" data-status={c.status}>
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted" data-testid="comment">
          <Chip name={c.author} />
          <span className="flex items-center gap-1">on <ChannelAvatar ch={ch} size={16} />@{c.channel_username}</span>
          <span>· {when(c.at)}</span>
          <Badge tone={c.status === 'open' ? 'amber' : c.status === 'replied' ? 'green' : 'gray'}>{c.status === 'open' ? 'Unanswered' : c.status === 'resolved' ? 'Done' : 'Replied'}</Badge>
          {c.flagged && <Badge tone="amber" data-testid="comment-flagged"><Flag className="size-3" />Flagged</Badge>}
          {c.assignee && <Badge tone="blue" data-testid="comment-assignee">→ {c.assignee}</Badge>}
        </div>
        <p className="mt-2 text-[14px] leading-relaxed">{c.text}</p>
        {view === 'list' && c.media_caption && <p className="mt-1 truncate text-xs text-muted">on “{c.media_caption}”</p>}
        {c.my_replies?.map((r, i) => <p key={i} className="mt-2 rounded-xl bg-lav-50 px-3 py-2 text-[13px]"><b>You:</b> {r.text}</p>)}
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <Textarea rows={1} className="min-w-64 flex-1" placeholder={`Reply to @${c.author}…`} value={draft[c.id] || ''} onChange={(e) => setDraft({ ...draft, [c.id]: e.target.value })} data-testid="comment-reply-input" aria-label="Reply" />
          <Button variant="primary" size="sm" disabled={!draft[c.id]} loading={busy === c.id} onClick={() => reply(c)} data-testid="comment-reply"><MessageSquareReply className="size-3.5" />Reply</Button>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1">
          <SavedReplies onPick={(text) => setDraft({ ...draft, [c.id]: `@${c.author} ${text}` })} />
          <Button size="sm" variant="ghost" onClick={() => change(c, { flagged: !c.flagged }, c.flagged ? 'Unflagged' : 'Flagged for follow-up')} data-testid="comment-flag" aria-pressed={c.flagged}><Flag className={cx('size-3.5', c.flagged && 'fill-amber-400 text-amber-500')} />{c.flagged ? 'Unflag' : 'Flag'}</Button>
          {assigning === c.id ? (
            <Input autoFocus className="h-8 w-40" placeholder="Assign to…" defaultValue={c.assignee} data-testid="comment-assign-input"
              onKeyDown={(e) => { if (e.key === 'Enter') { const v = e.currentTarget.value.trim(); setAssigning(null); change(c, { assignee: v }, v ? `Assigned to ${v}` : 'Unassigned') } if (e.key === 'Escape') setAssigning(null) }}
              onBlur={() => setAssigning(null)} />
          ) : <Button size="sm" variant="ghost" onClick={() => setAssigning(c.id)} data-testid="comment-assign"><UserPlus className="size-3.5" />{c.assignee ? 'Reassign' : 'Assign'}</Button>}
          {c.status !== 'resolved'
            ? <Button size="sm" variant="ghost" onClick={() => change(c, { status: 'resolved' }, 'Marked done')} data-testid="comment-done"><CheckCheck className="size-3.5" />Mark done</Button>
            : <Button size="sm" variant="ghost" onClick={() => change(c, { status: 'open' }, 'Reopened')} data-testid="comment-reopen"><RotateCcw className="size-3.5" />Reopen</Button>}
          {c.media_permalink && <a href={c.media_permalink} target="_blank" rel="noreferrer" className="ml-auto inline-flex h-8 items-center gap-1 rounded-xl px-3 text-[13px] text-ink-2 hover:bg-lav-100" data-testid="comment-open-post"><ExternalLink className="size-3.5" />Open post</a>}
        </div>
      </motion.article>
    )
  }

  const history = (byAuthor[contact] || []).slice().sort((a, b) => (b.at || '').localeCompare(a.at || ''))
  const chans = [...new Set(history.map((c) => c.channel_id))]

  return (
    <div className="space-y-5" data-testid="page-community">
      <div className="flex flex-wrap items-center gap-3">
        <div><h1 className="text-2xl font-semibold tracking-tight">Community</h1><p className="text-sm text-muted">Every comment from every channel, organised by the people who wrote them.</p></div>
        <Button className="ml-auto" variant="outline" loading={busy === 'sync'} onClick={sync} data-testid="comments-sync"><RefreshCw className="size-4" />Sync now</Button>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <div className="inline-flex flex-wrap rounded-xl bg-lav-100 p-1" role="tablist" aria-label="Filter comments">
          {FILTERS.map(([k, l]) => (
            <button key={k} role="tab" aria-selected={status === k} onClick={() => setQuery({ status: k })} data-testid={`community-filter-${k}`}
              className={cx('flex cursor-pointer items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium transition', status === k ? 'bg-surface text-ink shadow-soft' : 'text-muted hover:text-ink')}>
              {l}<span className="rounded-full bg-lav-200 px-1.5 text-[10px]">{counts[k] ?? 0}</span>
            </button>
          ))}
        </div>
        <Tabs testid="comments-view" value={view} onChange={(v) => setQuery({ view: v })} items={[['list', 'List'], ['post', 'By post'], ['insights', 'Insights']]} />
        <div className="ml-auto flex gap-2">
          <Input placeholder="Search comments or people" value={query.q || ''} onChange={(e) => setQuery({ q: e.target.value })} className="w-56" data-testid="comments-search" aria-label="Search comments" />
          <Select value={query.channel || ''} onChange={(e) => setQuery({ channel: e.target.value })} options={[['', 'All channels'], ...channels.map((c) => [c.id, `@${c.username} · ${c.provider}`])]} data-testid="comments-channel" aria-label="Channel" />
        </div>
      </div>
      {hidden.map((n) => <p key={n.channel_id} className="flex items-start gap-2 rounded-xl bg-amber-50 p-3 text-xs text-amber-900" data-testid="comments-hidden-note"><Info className="mt-0.5 size-3.5 shrink-0" /><span><b>@{n.username}:</b> {n.note}</span></p>)}
      {noComments.length > 0 && <p className="flex items-start gap-2 rounded-xl bg-lav-50 p-3 text-xs text-ink-2"><Info className="mt-0.5 size-3.5 shrink-0" />{noComments.map((c) => `${c.name}: ${c.capabilities.comments_note}`).join(' ')}</p>}

      <div className={cx('grid gap-5', contact && 'lg:grid-cols-[minmax(0,1fr)_340px]')}>
        <div className="min-w-0">
          {view === 'insights' ? <CommentInsights channelId={query.channel || ''} /> : all === null ? <SkeletonList rows={3} /> : !rows.length
            ? <Empty icon={status === 'open' ? Sparkles : Inbox} title={status === 'open' ? 'Inbox zero' : 'Nothing here'}>{status === 'open' ? 'Every comment has an answer. Press “Sync now” to pull the latest.' : 'No comments match this filter.'}</Empty>
            : view === 'post' ? <div className="space-y-5">{groups.map(([k, list]) => (
              <section key={k} className="space-y-2"><h3 className="flex items-center gap-2 text-sm font-semibold"><MessagesSquare className="size-4 text-lav-600" />“{k}” <span className="font-normal text-muted">· {list.length}</span></h3>{list.map((c) => <div key={c.id}>{Row({ c })}</div>)}</section>
            ))}</div> : <div className="space-y-3">{rows.map((c) => <div key={c.id}>{Row({ c })}</div>)}</div>}
        </div>

        <AnimatePresence>
          {contact && (
            <motion.aside initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 16 }} className="rounded-2xl border border-lav-200 bg-surface p-5 shadow-soft lg:sticky lg:top-0 lg:self-start" data-testid="contact-panel" aria-label={`@${contact}`}>
              <div className="flex items-start gap-3">
                <Initials name={contact} size={48} />
                <div className="min-w-0 flex-1"><h2 className="truncate text-lg font-semibold">@{contact}</h2>
                  <p className="text-xs text-muted">{history.length} comment{history.length === 1 ? '' : 's'} · {chans.length} channel{chans.length === 1 ? '' : 's'} · first seen {history.length ? when(history[history.length - 1].at) : '—'}</p></div>
                <Button size="icon" variant="ghost" aria-label="Close" onClick={() => setQuery({ contact: '' })} data-testid="contact-close"><X className="size-4" /></Button>
              </div>
              <div className="mt-4 grid grid-cols-3 gap-2 text-center">
                {[['Unanswered', history.filter((c) => c.status === 'open').length], ['Answered', history.filter((c) => c.status !== 'open').length], ['Flagged', history.filter((c) => c.flagged).length]].map(([l, n]) => (
                  <div key={l} className="rounded-xl bg-lav-50 p-2"><div className="text-lg font-semibold tabular-nums">{n}</div><div className="text-[11px] text-muted">{l}</div></div>
                ))}
              </div>
              <h3 className="mb-2 mt-5 text-xs font-semibold uppercase tracking-wider text-muted">History across posts</h3>
              <ol className="relative space-y-3 border-l-2 border-lav-200 pl-4">
                {history.map((c) => {
                  const ch = channels.find((x) => x.id === c.channel_id)
                  return (
                    <li key={c.id} data-testid="contact-history-item" className="relative">
                      <span className={cx('absolute -left-[21px] top-1.5 size-2.5 rounded-full ring-2 ring-surface', c.status === 'open' ? 'bg-amber-400' : 'bg-emerald-500')} />
                      <div className="flex items-center gap-1.5 text-[11px] text-muted"><ChannelAvatar ch={ch} size={14} />@{c.channel_username} · {when(c.at)}</div>
                      <p className="text-[13px]">{c.text}</p>
                      {c.media_caption && <p className="truncate text-[11px] text-muted">on “{c.media_caption}”</p>}
                    </li>
                  )
                })}
              </ol>
            </motion.aside>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}
