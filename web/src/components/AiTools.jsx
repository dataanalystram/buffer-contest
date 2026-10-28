// AI helpers that sit on existing pages: the Ideas generator, Community comment insights and the channel-group manager.
import { useEffect, useState } from 'react'
import { Layers, Lightbulb, MessageCircleQuestion, Plus, Sparkles, Trash2 } from 'lucide-react'
import { del, get, patch, post } from '../api'
import { setQuery } from '../router'
import { useStore } from '../store'
import { Badge, Button, Card, ChannelAvatar, cx, Dialog, Field, Input, Select, confirmAsync, useToast } from '../ui'

export function GenerateIdeasDialog({ open, onClose, onSaved, columns = [] }) {
  const toast = useToast()
  const [f, setF] = useState({ topic: '', audience: '', count: 5, column: 'unassigned' })
  const [res, setRes] = useState(null)
  const [pick, setPick] = useState([])
  const [busy, setBusy] = useState('')
  const gen = async () => {
    setBusy('gen')
    try { const r = await post('/api/ideas/generate', f); setRes(r); setPick(r.ideas.map((_, i) => i)) } catch (e) { toast(e.message, 'err') }
    setBusy('')
  }
  const save = async () => {
    setBusy('save')
    try {
      for (const i of pick) await post('/api/ideas', { ...res.ideas[i], column: f.column, tags: [] })
      toast(`${pick.length} idea(s) added`); setRes(null); onSaved(); onClose()
    } catch (e) { toast(e.message, 'err') }
    setBusy('')
  }
  return (
    <Dialog open={open} onClose={onClose} title="Generate ideas" testid="ideas-generate-dialog" wide
      footer={res ? <>
        <Button variant="ghost" onClick={gen} loading={busy === 'gen'} data-testid="ideas-generate-again"><Sparkles className="size-4" />Try again</Button>
        <Button variant="primary" disabled={!pick.length} loading={busy === 'save'} onClick={save} data-testid="ideas-generate-save">Add {pick.length} to board</Button>
      </> : <Button variant="primary" disabled={!f.topic.trim()} loading={busy === 'gen'} onClick={gen} data-testid="ideas-generate-run"><Sparkles className="size-4" />Generate</Button>}>
      <div className="space-y-4">
        <div className="grid gap-3 md:grid-cols-[2fr_2fr_90px_1fr]">
          <Field label="About"><Input autoFocus value={f.topic} onChange={(e) => setF({ ...f, topic: e.target.value })} onKeyDown={(e) => e.key === 'Enter' && f.topic.trim() && gen()} placeholder="e.g. AI agents for small teams" data-testid="ideas-generate-topic" /></Field>
          <Field label="For"><Input value={f.audience} onChange={(e) => setF({ ...f, audience: e.target.value })} placeholder="e.g. founders, students" data-testid="ideas-generate-audience" /></Field>
          <Field label="How many"><Input type="number" min={1} max={10} value={f.count} onChange={(e) => setF({ ...f, count: Number(e.target.value) })} data-testid="ideas-generate-count" /></Field>
          <Field label="Add to"><Select value={f.column} onChange={(e) => setF({ ...f, column: e.target.value })} data-testid="ideas-generate-column" options={columns.map((c) => [c.id, c.label])} /></Field>
        </div>
        {res?.note && <p className="text-xs text-muted" data-testid="ideas-generate-note">{res.note}</p>}
        {res && <div className="grid gap-2 md:grid-cols-2" data-testid="ideas-generate-results">{res.ideas.map((i, k) => {
          const on = pick.includes(k)
          return <button key={k} type="button" aria-pressed={on} data-testid="ideas-generate-item" onClick={() => setPick(on ? pick.filter((x) => x !== k) : [...pick, k])}
            className={cx('cursor-pointer rounded-2xl border p-3 text-left transition', on ? 'border-lav-500 bg-lav-50 shadow-soft' : 'border-lav-200 opacity-60')}>
            <div className="flex items-start gap-2"><Lightbulb className="mt-0.5 size-4 shrink-0 text-lav-600" /><b className="text-sm">{i.title}</b></div>
            {i.body && <p className="mt-1 pl-6 text-xs text-muted">{i.body}</p>}
          </button>
        })}</div>}
      </div>
    </Dialog>
  )
}

export function CommentInsights({ channelId }) {
  const [days, setDays] = useState('30')
  const [d, setD] = useState(null)
  useEffect(() => { setD(null); get(`/api/comments/insights?days=${days}${channelId ? `&channel_id=${channelId}` : ''}`).then(setD).catch(() => setD({ themes: [] })) }, [days, channelId])
  return (
    <Card className="space-y-4 p-5" data-testid="comment-insights">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="mr-auto flex items-center gap-2 font-semibold"><MessageCircleQuestion className="size-4 text-lav-600" />Comment insights {d?.source === 'llm' && <Badge tone="lav">AI</Badge>}</h2>
        <Select value={days} onChange={(e) => setDays(e.target.value)} data-testid="comment-insights-range" options={[['7', 'Last 7 days'], ['30', 'Last 30 days'], ['90', 'Last 90 days']]} />
      </div>
      {!d ? <p className="text-sm text-muted">Reading your comments…</p> : <>
        <div className="flex flex-wrap gap-2 text-sm"><Badge tone="lav">{d.comments ?? 0} comments</Badge><Badge tone="amber">{d.questions ?? 0} questions</Badge><Badge tone="red">{d.unanswered ?? 0} unanswered</Badge></div>
        {!d.themes?.length ? <p className="text-sm text-muted">No themes yet - they appear once people comment.</p> : (
          <div className="grid gap-3 md:grid-cols-2">{d.themes.map((t, i) => (
            <div key={i} className="rounded-2xl border border-lav-200 p-3" data-testid="comment-theme">
              <div className="flex items-center gap-2"><b className="capitalize">{t.label}</b>{t.count > 0 && <span className="text-xs text-muted">{t.count}</span>}</div>
              {t.summary && <p className="text-xs text-muted">{t.summary}</p>}
              {t.examples?.map((x, k) => <p key={k} className="mt-1 border-l-2 border-lav-300 pl-2 text-xs italic text-ink-2">“{x}”</p>)}
              {t.suggestion && <Button size="sm" variant="soft" className="mt-2" data-testid="comment-theme-post"
                onClick={() => setQuery({ compose: 'new', text: t.suggestion, channels: channelId || '' })}><Plus className="size-3.5" />{t.suggestion}</Button>}
            </div>
          ))}</div>
        )}
      </>}
    </Card>
  )
}

export function ChannelGroups() {
  const { channels } = useStore()
  const toast = useToast()
  const [groups, setGroups] = useState([])
  const [name, setName] = useState('')
  const load = () => get('/api/channel-groups').then(setGroups)
  useEffect(() => { load() }, [])
  const create = async () => { try { await post('/api/channel-groups', { name, channel_ids: [] }); setName(''); load() } catch (e) { toast(e.message, 'err') } }
  const toggle = async (g, cid) => {
    const ids = g.channel_ids.includes(cid) ? g.channel_ids.filter((x) => x !== cid) : [...g.channel_ids, cid]
    setGroups((x) => x.map((y) => (y.id === g.id ? { ...y, channel_ids: ids } : y)))
    try { await patch(`/api/channel-groups/${g.id}`, { channel_ids: ids }) } catch (e) { toast(e.message, 'err'); load() }
  }
  const remove = async (g) => { if (await confirmAsync(`Delete the group “${g.name}”? The channels stay connected.`, { ok: 'Delete' })) { await del(`/api/channel-groups/${g.id}`); load() } }
  return (
    <div className="space-y-3 p-5" data-testid="channel-groups">
      <div><h3 className="flex items-center gap-2 font-semibold"><Layers className="size-4 text-lav-600" />Channel groups</h3><p className="text-sm text-muted">Pick a whole group with one click in the composer.</p></div>
      {groups.map((g) => (
        <div key={g.id} className="flex flex-wrap items-center gap-2 rounded-2xl border border-lav-200 p-3" data-testid="channel-group">
          <b className="mr-2 text-sm">{g.name}</b>
          {channels.map((c) => {
            const on = g.channel_ids.includes(c.id)
            return <button key={c.id} type="button" aria-pressed={on} aria-label={`${on ? 'Remove' : 'Add'} @${c.username}`} data-testid="channel-group-toggle" onClick={() => toggle(g, c.id)}
              className={cx('cursor-pointer rounded-full border p-0.5', on ? 'border-lav-500' : 'border-transparent opacity-40 grayscale')}><ChannelAvatar ch={c} size={24} /></button>
          })}
          <Button size="icon-sm" variant="danger-ghost" className="ml-auto" aria-label={`Delete group ${g.name}`} onClick={() => remove(g)} data-testid="channel-group-delete"><Trash2 className="size-3.5" /></Button>
        </div>
      ))}
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (name.trim()) create() }}>
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="New group, e.g. Video channels" data-testid="channel-group-name" />
        <Button disabled={!name.trim()} data-testid="channel-group-create"><Plus className="size-4" />Add group</Button>
      </form>
    </div>
  )
}
