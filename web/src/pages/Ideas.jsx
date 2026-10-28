import { motion } from 'motion/react'
import { LayoutGrid, Columns3, Plus, Send, Trash2, Lightbulb, Sparkles } from 'lucide-react'
import { GenerateIdeasDialog } from '../components/AiTools'
import { useEffect, useState } from 'react'
import { del, get, patch, post, put } from '../api'
import { setQuery, useRoute } from '../router'
import { useStore } from '../store'
import { Badge, Button, Dialog, Empty, Field, Input, Select, Tabs, Textarea, useToast, promptAsync } from '../ui'

export default function Ideas() {
  const { library } = useStore()
  const { query } = useRoute()
  const toast = useToast()
  const [data, setData] = useState({ columns: [], ideas: [] })
  const [edit, setEdit] = useState(null)
  const [gen, setGen] = useState(false)
  const view = query.view || 'board'
  const load = () => get('/api/ideas').then(setData)
  useEffect(() => { load() }, [])
  useEffect(() => { if (query.new === '1') { setEdit({ title: '', body: '', column: 'unassigned', tags: [] }); setQuery({ new: '' }) } }, [query.new])

  const save = async () => {
    try {
      if (edit.id) await patch(`/api/ideas/${edit.id}`, edit)
      else await post('/api/ideas', edit)
      setEdit(null); load(); toast('Idea saved')
    } catch (e) { toast(e.message, 'err') }
  }
  const toPost = (i) => setQuery({ compose: 'new', text: [i.title, i.body].filter(Boolean).join('\n\n') })
  const drop = (e, col) => { const id = e.dataTransfer.getData('text/idea'); if (id) moveIdea(id, col) }
  const addColumn = async () => { const label = await promptAsync('Column name'); if (label) { await put('/api/idea-columns', { columns: [...data.columns, { label }] }); load() } }
  const [renaming, setRenaming] = useState(null)   // column id being renamed in place
  const rename = async (c, label) => {
    setRenaming(null)
    label = (label || '').trim()
    if (!label || label === c.label) return
    const columns = data.columns.map((x) => (x.id === c.id ? { ...x, label } : x))
    setData((d) => ({ ...d, columns }))  // optimistic
    try { await put('/api/idea-columns', { columns }); toast('Group renamed') } catch (e) { toast(e.message, 'err'); load() }
  }
  const moveIdea = async (id, col) => {
    setData((d) => ({ ...d, ideas: d.ideas.map((i) => (i.id === id ? { ...i, column: col } : i)) }))  // optimistic
    try { await patch(`/api/ideas/${id}`, { column: col }) } catch (e) { toast(e.message, 'err'); load() }
  }
  const shown = data.ideas.filter((i) => !query.tag || i.tags?.includes(query.tag))

  const Card = ({ i }) => (
    <motion.div layout draggable onDragStart={(e) => e.dataTransfer.setData('text/idea', i.id)} data-testid="idea-card"
      className="group cursor-grab rounded-xl border border-lav-200 bg-surface p-3 shadow-sm hover:border-lav-400 active:cursor-grabbing">
      <button className="block w-full cursor-pointer text-left text-sm font-medium" onClick={() => setEdit(i)} data-testid="idea-open">{i.title || 'Untitled idea'}</button>
      {i.body && <p className="mt-1 line-clamp-3 text-xs text-muted">{i.body}</p>}
      <div className="mt-2 flex items-center gap-1">
        {i.tags?.map((t) => <Badge key={t} tone="gray">#{t}</Badge>)}
        <Button size="sm" variant="ghost" className="ml-auto opacity-0 group-hover:opacity-100" onClick={() => toPost(i)} data-testid="idea-to-post"><Send className="size-3.5" />Create post</Button>
      </div>
    </motion.div>
  )

  return (
    <div className="space-y-5" data-testid="page-ideas">
      <GenerateIdeasDialog open={gen} onClose={() => setGen(false)} onSaved={load} columns={data.columns} />
      <div className="flex flex-wrap items-center gap-3">
        <div><h1 className="text-2xl font-semibold tracking-tight">Create</h1><p className="text-sm text-muted">Capture ideas, move them across the board, turn them into posts.</p></div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Select value={query.tag || ''} onChange={(e) => setQuery({ tag: e.target.value })} options={[['', 'All tags'], ...(library.tags || []).map((t) => [t.id, t.name])]} data-testid="ideas-tag-filter" aria-label="Tag filter" />
          <Tabs testid="ideas-view" value={view} onChange={(v) => setQuery({ view: v })} items={[['board', <span key="b" className="flex items-center gap-1"><Columns3 className="size-3.5" />Board</span>], ['gallery', <span key="g" className="flex items-center gap-1"><LayoutGrid className="size-3.5" />Gallery</span>]]} />
          <Button onClick={() => setGen(true)} data-testid="ideas-generate"><Sparkles className="size-4 text-lav-600" />Generate ideas</Button>
          <Button variant="primary" onClick={() => setEdit({ title: '', body: '', column: 'unassigned', tags: [] })} data-testid="idea-new"><Plus className="size-4" />New idea</Button>
        </div>
      </div>
      {view === 'board' ? (
        <div className="flex gap-4 overflow-x-auto pb-4" data-testid="ideas-board">
          {data.columns.map((c) => (
            <section key={c.id} onDragOver={(e) => e.preventDefault()} onDrop={(e) => drop(e, c.id)} data-testid="idea-column" aria-label={c.label}
              className="w-72 shrink-0 rounded-2xl border border-lav-200/60 bg-lav-100/70 p-3">
              <header className="mb-3 flex items-center justify-between">
                {renaming === c.id ? (
                  <input autoFocus defaultValue={c.label} aria-label={`Rename ${c.label}`} data-testid="idea-column-rename-input"
                    onBlur={(e) => rename(c, e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); if (e.key === 'Escape') setRenaming(null) }}
                    className="h-7 w-40 rounded-lg border border-lav-400 bg-surface px-2 text-sm font-semibold outline-none ring-4 ring-lav-200/60" />
                ) : (
                  <button onClick={() => setRenaming(c.id)} className="cursor-text rounded-lg px-1 text-sm font-semibold hover:bg-surface/70" title="Click to rename" data-testid="idea-column-rename">{c.label}</button>
                )}
                <span className="flex items-center gap-1"><Badge tone="gray">{shown.filter((i) => i.column === c.id).length}</Badge>
                  <button aria-label={`Add idea to ${c.label}`} className="cursor-pointer rounded-md p-1 hover:bg-surface" onClick={() => setEdit({ title: '', body: '', column: c.id, tags: [] })} data-testid="idea-column-add"><Plus className="size-3.5" /></button></span>
              </header>
              <div className="min-h-24 space-y-2">{shown.filter((i) => i.column === c.id).map((i) => <div key={i.id}>{Card({ i })}</div>)}
                {!shown.some((i) => i.column === c.id) && <button data-testid="ideas-drop-an-idea-here" onClick={() => setEdit({ title: '', body: '', column: c.id, tags: [] })} className="grid h-24 w-full cursor-pointer place-items-center rounded-xl border border-dashed border-lav-300 text-xs text-muted hover:border-lav-500 hover:text-lav-700">Drop an idea here or add one</button>}</div>
            </section>
          ))}
          <button onClick={addColumn} className="h-12 w-56 shrink-0 cursor-pointer rounded-2xl border border-dashed border-lav-300 text-sm text-lav-700 hover:bg-lav-50" data-testid="idea-add-column">+ New group</button>
        </div>
      ) : shown.length ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" data-testid="ideas-gallery">{shown.map((i) => <div key={i.id}>{Card({ i })}</div>)}</div>
      ) : <Empty icon={Lightbulb} title="No ideas yet" action={<Button data-testid="ideas-new-idea" variant="primary" onClick={() => setEdit({ title: '', body: '', column: 'unassigned', tags: [] })}>New idea</Button>} />}

      <Dialog open={!!edit} onClose={() => setEdit(null)} title={edit?.id ? 'Edit idea' : 'New idea'} testid="idea-dialog"
        footer={<>
          {edit?.id && <Button variant="danger-ghost" className="mr-auto" onClick={async () => { await del(`/api/ideas/${edit.id}`); setEdit(null); load() }} data-testid="idea-delete"><Trash2 className="size-4" />Delete</Button>}
          {edit?.id && <Button variant="outline" onClick={() => { toPost(edit); setEdit(null) }} data-testid="idea-dialog-to-post"><Send className="size-4" />Create post</Button>}
          <Button variant="primary" onClick={save} data-testid="idea-save">Save idea</Button>
        </>}>
        {edit && <div className="space-y-3">
          <Field label="Title"><Input value={edit.title} onChange={(e) => setEdit({ ...edit, title: e.target.value })} autoFocus data-testid="idea-title" /></Field>
          <Field label="Notes"><Textarea rows={5} value={edit.body} onChange={(e) => setEdit({ ...edit, body: e.target.value })} data-testid="idea-body" /></Field>
          <Field label="Group"><Select className="w-full" value={edit.column} onChange={(e) => setEdit({ ...edit, column: e.target.value })} options={data.columns.map((c) => [c.id, c.label])} data-testid="idea-column-select" /></Field>
          <div className="flex flex-wrap gap-1">{(library.tags || []).map((t) => {
            const on = edit.tags?.includes(t.id)
            return <button key={t.id} className="cursor-pointer rounded-full border px-2.5 py-0.5 text-xs" style={on ? { background: t.color, color: 'white', borderColor: 'transparent' } : {}} onClick={() => setEdit({ ...edit, tags: on ? edit.tags.filter((x) => x !== t.id) : [...(edit.tags || []), t.id] })} data-testid="idea-tag">{t.name}</button>
          })}</div>
        </div>}
      </Dialog>
    </div>
  )
}
