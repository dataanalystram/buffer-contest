// Expanded editors: LinkPanel (Details | Power-ups | Insights) for link + brand buttons, BlockEditor for every other block.
import { CalendarClock, Copy, ImagePlus, MousePointerClick, Plus, Sparkles, Trash2, X, Zap } from 'lucide-react'
import { useRef, useState } from 'react'
import { Button, cx, Field, Input, Select, Switch, Tabs, Textarea, useToast } from '../../ui'
import { SOCIAL_NETS, uploadSquare, useCopy } from './shared'

const toLocal = (iso) => { if (!iso) return ''; const d = new Date(iso); return isNaN(d) ? '' : new Date(d - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16) }
const fromLocal = (v) => (v ? new Date(v).toISOString() : '')

function Toggle({ icon: Icon, title, desc, checked, onChange, testid }) {
  return (
    <div className="flex items-center gap-3 rounded-2xl border border-lav-200/70 p-3">
      <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-lav-100 text-lav-700"><Icon className="size-4" /></span>
      <span className="min-w-0 flex-1"><span className="block text-sm font-medium">{title}</span><span className="block text-xs text-muted">{desc}</span></span>
      <Switch checked={!!checked} onChange={onChange} label={title} testid={testid} />
    </div>
  )
}

export function LinkPanel({ b, set, stat }) {
  const [tab, setTab] = useState('details')
  return (
    <div className="space-y-4">
      <Tabs value={tab} onChange={setTab} testid="bio-link-tabs" className="w-full [&>button]:flex-1"
        items={[['details', 'Details'], ['powerups', 'Power-ups'], ['insights', 'Insights']]} />
      {tab === 'details' && <Details b={b} set={set} />}
      {tab === 'powerups' && (
        <div className="space-y-2">
          <Toggle icon={Sparkles} title="Highlight" desc="Fill the button with your accent colour" checked={b.highlight} onChange={(v) => set({ highlight: v })} testid="bio-block-highlight" />
          <Toggle icon={Zap} title="Spotlight" desc="A gentle wiggle every few seconds draws the eye" checked={b.spotlight} onChange={(v) => set({ spotlight: v })} testid="bio-block-spotlight" />
          <div className="rounded-2xl border border-lav-200/70 p-3">
            <div className="mb-2 flex items-center gap-3">
              <span className="grid size-9 place-items-center rounded-xl bg-lav-100 text-lav-700"><CalendarClock className="size-4" /></span>
              <span><span className="block text-sm font-medium">Schedule</span><span className="block text-xs text-muted">Show this link only between these times (your local time)</span></span>
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              <Field label="Show from"><Input type="datetime-local" value={toLocal(b.start_at)} onChange={(e) => set({ start_at: fromLocal(e.target.value) })} data-testid="bio-block-start" /></Field>
              <Field label="Hide after"><Input type="datetime-local" value={toLocal(b.end_at)} onChange={(e) => set({ end_at: fromLocal(e.target.value) })} data-testid="bio-block-end" /></Field>
            </div>
            {(b.start_at || b.end_at) && <Button size="sm" variant="ghost" className="mt-2" onClick={() => set({ start_at: '', end_at: '' })} data-testid="bio-block-schedule-clear"><X className="size-3.5" />Clear schedule</Button>}
          </div>
        </div>
      )}
      {tab === 'insights' && <Insights b={b} stat={stat} />}
    </div>
  )
}

function Details({ b, set }) {
  const toast = useToast()
  const file = useRef(null)
  const [busy, setBusy] = useState(false)
  const pick = async (e) => {
    const f = e.target.files?.[0]
    e.target.value = ''
    if (!f) return
    setBusy(true)
    try { set({ thumb: await uploadSquare(f, b.layout === 'featured' ? '' : 'square') }) } catch (err) { toast(err.message, 'err') } finally { setBusy(false) }
  }
  const layouts = [['classic', 'Classic', 'Efficient, direct and compact.'], ['featured', 'Featured', 'Make your link stand out with a larger image.']]
  return (
    <div className="space-y-4">
      <div>
        <h4 className="mb-2 text-sm font-semibold">Thumbnail</h4>
        <div className="flex items-center gap-3">
          <button type="button" onClick={() => file.current?.click()} aria-label="Upload thumbnail" data-testid="bio-thumb-upload"
            className={cx('grid size-16 cursor-pointer place-items-center overflow-hidden rounded-2xl border-2 border-dashed border-lav-300 text-muted transition hover:border-lav-500', busy && 'animate-pulse')}>
            {b.thumb ? <img src={b.thumb} alt="" className="size-full object-cover" /> : <ImagePlus className="size-5" />}
          </button>
          <input ref={file} type="file" accept="image/*" className="hidden" onChange={pick} aria-label="Thumbnail file" data-testid="bio-thumb-file" />
          <div className="text-xs text-muted">Square images work best. They are cropped to 400×400 (Featured keeps the full image).</div>
          {b.thumb && <Button size="icon-sm" variant="danger-ghost" aria-label="Remove thumbnail" onClick={() => set({ thumb: '' })} data-testid="bio-thumb-remove"><Trash2 className="size-3.5" /></Button>}
        </div>
      </div>
      <div>
        <h4 className="mb-2 text-sm font-semibold">Display</h4>
        <div className="grid grid-cols-2 gap-3" role="radiogroup" aria-label="Layout">
          {layouts.map(([k, l, d]) => {
            const on = (b.layout || 'classic') === k
            return (
              <button key={k} type="button" role="radio" aria-checked={on} onClick={() => set({ layout: k })} data-testid={`bio-layout-${k}`}
                className={cx('cursor-pointer rounded-2xl border-2 p-3 text-left transition', on ? 'border-ink' : 'border-lav-200 hover:border-lav-400')}>
                <div className="mb-2 grid h-20 place-items-center rounded-xl bg-lav-50">
                  {k === 'classic'
                    ? <span className="flex h-6 w-28 items-center gap-1.5 rounded-full border border-lav-300 bg-surface px-1"><span className="size-4 rounded-full bg-lav-300" /><span className="h-1.5 w-12 rounded bg-lav-300" /></span>
                    : <span className="w-28 overflow-hidden rounded-xl border border-lav-300 bg-surface"><span className="block h-10 bg-gradient-to-br from-lav-300 to-lav-500" /><span className="mx-auto my-1 block h-1.5 w-12 rounded bg-lav-300" /></span>}
                </div>
                <span className="block text-sm font-semibold">{l}</span><span className="block text-xs text-muted">{d}</span>
              </button>
            )
          })}
        </div>
        {b.layout === 'featured' && !b.thumb && <p className="mt-2 text-xs text-amber-700">Add a thumbnail: Featured shows it as a large image.</p>}
      </div>
    </div>
  )
}

function Insights({ b, stat }) {
  const copy = useCopy()
  return (
    <div className="space-y-3" data-testid="bio-link-insights">
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-2xl bg-lav-50 p-4"><div className="text-xs text-muted">All-time clicks</div><div className="text-2xl font-semibold tabular-nums">{b.clicks ?? 0}</div></div>
        <div className="rounded-2xl bg-lav-50 p-4"><div className="text-xs text-muted">Last 30 days</div><div className="text-2xl font-semibold tabular-nums">{stat?.clicks_in_range ?? 0}</div></div>
      </div>
      {b.short_url
        ? <div className="flex items-center gap-2 text-xs text-muted"><MousePointerClick className="size-3.5" />Tracked through <span className="truncate font-mono">{b.short_url}</span>
          <Button size="icon-sm" variant="ghost" aria-label="Copy tracked link" onClick={() => copy(b.short_url)} data-testid="bio-block-copy-short"><Copy className="size-3.5" /></Button></div>
        : <p className="text-xs text-muted">Add a URL to start counting clicks.</p>}
    </div>
  )
}

export function BlockEditor({ b, set: s }) {
  if (b.type === 'image') {
    return (
      <div className="grid gap-2 md:grid-cols-2">
        <Input placeholder="Image URL (https://…)" value={b.src} onChange={(e) => s({ src: e.target.value })} aria-label="Image URL" data-testid="bio-image-src" />
        <Input placeholder="Describe the image (alt text)" value={b.alt} onChange={(e) => s({ alt: e.target.value })} aria-label="Alt text" data-testid="bio-image-alt" />
        <Input placeholder="Link when tapped (optional)" value={b.url || ''} onChange={(e) => s({ url: e.target.value })} aria-label="Image link" data-testid="bio-image-url" />
        <Input placeholder="Caption (optional)" value={b.caption || ''} onChange={(e) => s({ caption: e.target.value })} aria-label="Caption" data-testid="bio-image-caption" />
      </div>
    )
  }
  if (b.type === 'embed') {
    return <Input placeholder="https://youtube.com/watch?v=… or https://open.spotify.com/…" value={b.url} onChange={(e) => s({ url: e.target.value })} aria-label="YouTube or Spotify link" data-testid="bio-embed-url" />
  }
  if (b.type === 'spacer') {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <div className="inline-flex rounded-full bg-lav-100 p-1" role="radiogroup" aria-label="Spacer size">
          {[['s', 'Small'], ['m', 'Medium'], ['l', 'Large']].map(([k, l]) => (
            <button key={k} type="button" role="radio" aria-checked={b.size === k} onClick={() => s({ size: k })} data-testid={`bio-spacer-${k}`}
              className={cx('cursor-pointer rounded-full px-3 py-1 text-[13px] font-medium', b.size === k ? 'bg-surface shadow-soft' : 'text-muted')}>{l}</button>
          ))}
        </div>
        <label className="flex items-center gap-2 text-sm"><Switch checked={!!b.divider} onChange={(v) => s({ divider: v })} label="Divider line" testid="bio-spacer-divider" />Divider line</label>
      </div>
    )
  }
  if (b.type === 'email') {
    return (
      <div className="grid gap-2 md:grid-cols-2">
        <Field label="Title"><Input value={b.title} onChange={(e) => s({ title: e.target.value })} data-testid="bio-email-title" /></Field>
        <Field label="Button"><Input value={b.button} onChange={(e) => s({ button: e.target.value })} data-testid="bio-email-button" /></Field>
        <Field label="What they get (optional)" className="md:col-span-2"><Input value={b.text || ''} onChange={(e) => s({ text: e.target.value })} data-testid="bio-email-text" /></Field>
        <Field label="Thank-you message"><Input value={b.success} onChange={(e) => s({ success: e.target.value })} data-testid="bio-email-success" /></Field>
        <label className="flex items-center gap-2 self-end pb-2 text-sm"><Switch checked={!!b.ask_name} onChange={(v) => s({ ask_name: v })} label="Ask for a name" testid="bio-email-ask-name" />Ask for a name too</label>
      </div>
    )
  }
  if (b.type === 'contact') {
    const f = [['name', 'Full name'], ['job_title', 'Job title'], ['org', 'Company'], ['email', 'Email'], ['phone', 'Phone'], ['website', 'Website (https://)'], ['button', 'Button text']]
    return (
      <div className="grid gap-2 md:grid-cols-2">
        {f.map(([k, l]) => <Field key={k} label={l}><Input value={b[k] || ''} onChange={(e) => s({ [k]: e.target.value })} data-testid={`bio-contact-${k}`} /></Field>)}
      </div>
    )
  }
  if (b.type === 'heading') return <Input value={b.text} onChange={(e) => s({ text: e.target.value })} aria-label="Header text" placeholder="Header text" data-testid="bio-heading-text" />
  if (b.type === 'text') return <Textarea rows={3} value={b.text} onChange={(e) => s({ text: e.target.value })} aria-label="Text" data-testid="bio-text-text" />
  if (b.type === 'posts') {
    return (
      <div className="grid gap-2 md:grid-cols-[1fr_120px]">
        <Field label="Section title"><Input value={b.title} onChange={(e) => s({ title: e.target.value })} data-testid="bio-posts-title" /></Field>
        <Field label="How many"><Input type="number" min={1} max={24} value={b.count} onChange={(e) => s({ count: Number(e.target.value) })} data-testid="bio-posts-count" /></Field>
      </div>
    )
  }
  if (b.type === 'socials') {
    return (
      <div className="space-y-2">
        <label className="flex items-center gap-2 text-sm"><Switch checked={!!b.auto} onChange={(v) => s({ auto: v })} label="Use connected channels" testid="bio-socials-auto" />Use my connected channels automatically</label>
        {!b.auto && (
          <>
            {(b.items || []).map((it, k) => (
              <div key={k} className="grid grid-cols-[140px_1fr_auto] gap-2">
                <Select value={it.network} onChange={(e) => s({ items: b.items.map((x, j) => (j === k ? { ...x, network: e.target.value } : x)) })}
                  options={SOCIAL_NETS.map((n) => [n, n])} aria-label="Network" data-testid="bio-socials-network" />
                <Input value={it.url} onChange={(e) => s({ items: b.items.map((x, j) => (j === k ? { ...x, url: e.target.value } : x)) })} placeholder="https://" aria-label="Profile URL" data-testid="bio-socials-url" />
                <Button size="icon-sm" variant="ghost" aria-label="Remove social link" onClick={() => s({ items: b.items.filter((_, j) => j !== k) })} data-testid="bio-socials-remove"><Trash2 className="size-3.5" /></Button>
              </div>
            ))}
            <Button data-testid="growth-social-link" size="sm" onClick={() => s({ items: [...(b.items || []), { network: 'instagram', url: 'https://' }] })}><Plus className="size-3.5" />Social link</Button>
          </>
        )}
      </div>
    )
  }
  return null
}
