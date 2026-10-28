// Contacts: everyone who comments on your posts, with segments, history, notes, tags and assignment.
// Registry: contact.list / contact.get / contact.update / contact.segments.
import { motion } from 'motion/react'
import { MessageSquareReply, Search, Users, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { get, patch, post } from '../api'
import { setQuery, useRoute } from '../router'
import { Badge, Button, cx, Empty, Input, NetIcon, SkeletonList, Textarea, useToast } from '../ui'

export default function Contacts() {
  const { query } = useRoute()
  const toast = useToast()
  const [rows, setRows] = useState(null)
  const [segs, setSegs] = useState([])
  const [c, setC] = useState(null)
  const [reply, setReply] = useState({})
  const load = () => {
    const p = new URLSearchParams()
    if (query.q) p.set('q', query.q)
    if (query.segment) p.set('segment', query.segment)
    get('/api/contacts?' + p).then(setRows).catch((e) => toast(e.message, 'err'))
    get('/api/contact-segments').then(setSegs).catch(() => {})
  }
  useEffect(load, [query.q, query.segment])
  useEffect(() => { if (query.contact) get(`/api/contacts/${encodeURIComponent(query.contact)}`).then(setC).catch(() => setC(null)); else setC(null) }, [query.contact])
  const save = async (body) => {
    try { await patch(`/api/contacts/${encodeURIComponent(c.id || c.handle)}`, body); const n = await get(`/api/contacts/${encodeURIComponent(c.handle)}`); setC(n); toast('Saved'); load() } catch (e) { toast(e.message, 'err') }
  }
  const send = async (h) => {
    try { await post(`/api/comments/${h.id}/reply`, { text: reply[h.id] }); setReply({ ...reply, [h.id]: '' }); toast('Reply sent'); setC(await get(`/api/contacts/${encodeURIComponent(c.handle)}`)) } catch (e) { toast(e.message, 'err') }
  }
  return (
    <div className="space-y-5" data-testid="page-contacts">
      <header><h1 className="text-2xl font-semibold tracking-tight">Contacts</h1><p className="text-sm text-muted">Everyone who comments on your posts, across every channel. Notes, tags and assignment stay private to you.</p></header>
      <div className="grid gap-5 lg:grid-cols-[220px_minmax(0,1fr)]">
        <aside className="space-y-1" data-testid="contacts-segments">
          <button onClick={() => setQuery({ segment: '' })} data-testid="contacts-segment-all" className={cx('flex w-full cursor-pointer items-center justify-between rounded-xl px-3 py-2 text-sm', !query.segment ? 'bg-lav-100 font-medium' : 'hover:bg-lav-50')}>Everyone<span className="text-xs text-muted">{rows?.length ?? ''}</span></button>
          {segs.map((s) => (
            <button key={s.id} title={s.rule} onClick={() => setQuery({ segment: s.id })} data-testid={`contacts-segment-${s.id}`}
              className={cx('flex w-full cursor-pointer items-center justify-between rounded-xl px-3 py-2 text-sm', query.segment === s.id ? 'bg-lav-100 font-medium' : 'hover:bg-lav-50')}>{s.label}<span className="text-xs text-muted">{s.count}</span></button>
          ))}
        </aside>
        <section className={cx('grid gap-5', c && 'xl:grid-cols-[minmax(0,1fr)_380px]')}>
          <div className="min-w-0 space-y-3">
            <div className="relative w-fit"><Search className="absolute left-3 top-2.5 size-4 text-muted" />
              <Input className="w-80 pl-9" placeholder="Search people, notes, tags" value={query.q || ''} onChange={(e) => setQuery({ q: e.target.value })} data-testid="contacts-search" aria-label="Search contacts" /></div>
            {rows === null ? <SkeletonList rows={4} /> : !rows.length ? <Empty icon={Users} title="No contacts yet" testid="contacts-empty">People appear here when they comment. Pull comments from Community › Sync now.</Empty> : (
              <div className="overflow-hidden rounded-2xl border border-lav-100 bg-surface">
                <table className="w-full text-sm"><thead className="bg-lav-50/60 text-left text-xs text-muted"><tr><th className="px-4 py-2">Person</th><th>Comments</th><th>Waiting</th><th>Last seen</th><th>Tags</th><th>Assignee</th></tr></thead>
                  <tbody>{rows.map((r) => (
                    <tr key={r.handle} onClick={() => setQuery({ contact: r.handle })} className={cx('cursor-pointer border-t border-lav-50 hover:bg-lav-50/50', query.contact === r.handle && 'bg-lav-50')} data-testid="contact-row">
                      <td className="px-4 py-2.5 font-medium">@{r.handle}</td><td>{r.comments}</td><td>{r.open ? <Badge tone="amber">{r.open}</Badge> : '–'}</td>
                      <td className="text-muted">{r.last_seen ? new Date(r.last_seen).toLocaleDateString() : ''}</td>
                      <td>{r.tags.map((t) => <Badge key={t} tone="gray" className="mr-1">{t}</Badge>)}</td><td>{r.assignee}</td></tr>))}</tbody></table>
              </div>
            )}
          </div>
          {c && (
            <motion.aside initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} className="h-fit rounded-2xl border border-lav-200 bg-surface p-5 shadow-soft xl:sticky xl:top-0" data-testid="contact-profile">
              <div className="flex items-start justify-between"><div><h2 className="text-lg font-semibold">@{c.handle}</h2>
                <p className="text-xs text-muted">{c.comments} comments · first seen {c.first_seen ? new Date(c.first_seen).toLocaleDateString() : ''}</p></div>
                <Button variant="ghost" size="icon" onClick={() => setQuery({ contact: '' })} aria-label="Close" data-testid="contact-profile-close"><X className="size-4" /></Button></div>
              <label className="mt-4 block text-sm">Notes<Textarea rows={3} defaultValue={c.notes} key={c.handle + 'n'} onBlur={(e) => e.target.value !== c.notes && save({ notes: e.target.value })} data-testid="contact-notes" /></label>
              <label className="mt-2 block text-sm">Tags<Input defaultValue={c.tags.join(', ')} key={c.handle + 't'} onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()} onBlur={(e) => save({ tags: e.target.value.split(',').map((x) => x.trim()).filter(Boolean) })} data-testid="contact-tags" /></label>
              <label className="mt-2 block text-sm">Assigned to<Input defaultValue={c.assignee} key={c.handle + 'a'} onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()} onBlur={(e) => e.target.value !== c.assignee && save({ assignee: e.target.value })} data-testid="contact-assignee" /></label>
              <h3 className="mt-5 text-sm font-semibold">Timeline</h3>
              <ol className="mt-2 space-y-3" data-testid="contact-timeline">{c.history.map((h) => (
                <li key={h.id} className="rounded-xl border border-lav-100 p-3 text-sm" data-testid="contact-timeline-item">
                  <div className="flex items-center gap-2 text-xs text-muted"><NetIcon provider={h.provider} size="sm" />{new Date(h.at).toLocaleString()}<Badge tone={h.status === 'open' ? 'amber' : 'green'}>{h.status}</Badge></div>
                  <p className="mt-1">{h.text}</p><p className="mt-1 truncate text-[11px] text-muted">on “{h.media_caption}”</p>
                  {h.status === 'open' && <div className="mt-2 flex gap-2"><Input className="h-8" placeholder={`Reply to @${c.handle}`} value={reply[h.id] || ''} onChange={(e) => setReply({ ...reply, [h.id]: e.target.value })} data-testid="contact-reply-input" />
                    <Button size="sm" variant="primary" disabled={!reply[h.id]} onClick={() => send(h)} data-testid="contact-reply-send"><MessageSquareReply className="size-3.5" />Reply</Button></div>}
                </li>))}</ol>
            </motion.aside>
          )}
        </section>
      </div>
    </div>
  )
}
