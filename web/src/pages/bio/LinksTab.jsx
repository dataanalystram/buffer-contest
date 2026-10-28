// Links tab: profile header (avatar, title, bio, page socials), "Add header" + "+ Add", and the drag-and-drop block list.
import { AnimatePresence, motion, Reorder, useDragControls } from 'motion/react'
import {
  ArrowDown, ArrowUp, CalendarClock, ChevronDown, Copy, GripVertical, ImagePlus, Link as LinkIcon, MoreVertical, MousePointerClick,
  Plus, Trash2, Wrench, Zap,
} from 'lucide-react'
import { useRef, useState } from 'react'
import { Button, confirmAsync, cx, Input, Menu, promptAsync, Select, Switch, useToast } from '../../ui'
import { AddDialog } from './AddDialog'
import { BlockEditor, LinkPanel } from './BlockPanel'
import { blockTitle, hostOf, META, NEW, socialIcon, SOCIAL_NETS, uid, uploadSquare, useBrands } from './shared'

export function LinksTab({ p, setP, focus, setFocus, stats }) {
  const [adding, setAdding] = useState(false)
  const [open, setOpen] = useState('')
  const brands = useBrands()
  const set = (o) => setP((c) => ({ ...c, ...o }))
  const setBlocks = (fn) => setP((c) => ({ ...c, blocks: fn(c.blocks) }))
  const setBlock = (id, o) => setBlocks((bs) => bs.map((x) => (x.id === id ? { ...x, ...o } : x)))
  const expand = (id) => { const next = open === id ? '' : id; setOpen(next); setFocus(next) }
  const insert = (partial) => {
    const b = { id: uid(), hidden: false, ...(NEW[partial.type] || {}), ...partial }
    setBlocks((bs) => [b, ...bs])
    setOpen(b.id); setFocus(b.id); setAdding(false)
  }
  const move = (id, d) => setBlocks((bs) => {
    const i = bs.findIndex((x) => x.id === id)
    const j = i + d
    if (i < 0 || j < 0 || j >= bs.length) return bs
    const next = [...bs]; [next[i], next[j]] = [next[j], next[i]]; return next
  })
  const remove = async (b) => {
    if (!(await confirmAsync(`Delete “${blockTitle(b) || META[b.type]?.label}”? Its click history stays in Short links.`, { ok: 'Delete' }))) return
    setBlocks((bs) => bs.filter((x) => x.id !== b.id))
  }
  const duplicate = (b) => setBlocks((bs) => {
    const i = bs.findIndex((x) => x.id === b.id)
    const { clicks: _c, short_url: _s, link_id: _l, ...rest } = b
    return [...bs.slice(0, i + 1), { ...rest, id: uid() }, ...bs.slice(i + 1)]
  })
  const byId = Object.fromEntries(p.blocks.map((b) => [b.id, b]))
  return (
    <div className="space-y-4">
      <ProfileHeader p={p} set={set} />
      <div className="grid grid-cols-[1fr_1.4fr] gap-3">
        <Button size="lg" variant="soft" className="justify-center rounded-full" onClick={() => insert({ type: 'heading' })} data-testid="bio-add-header"><Plus className="size-4" />Add header</Button>
        <Button size="lg" variant="primary" className="justify-center rounded-full" onClick={() => setAdding(true)} data-testid="bio-add-open"><Plus className="size-4" />Add</Button>
      </div>
      {!p.blocks.length && (
        <div className="rounded-3xl border-2 border-dashed border-lav-200 p-10 text-center text-sm text-muted" data-testid="bio-blocks-empty">
          Nothing here yet. Paste your first link with <b>+ Add</b>.
        </div>
      )}
      <Reorder.Group axis="y" values={p.blocks.map((b) => b.id)} onReorder={(ids) => setBlocks(() => ids.map((id) => byId[id]))} className="space-y-3" data-testid="bio-blocks">
        {p.blocks.map((b, i) => (
          <BlockCard key={b.id} b={b} first={i === 0} last={i === p.blocks.length - 1} open={open === b.id} dim={!!focus && focus !== b.id}
            onExpand={() => expand(b.id)} onFocus={() => setFocus(b.id)} set={(o) => setBlock(b.id, o)} move={(d) => move(b.id, d)}
            onRemove={() => remove(b)} onDuplicate={() => duplicate(b)} brands={brands}
            stat={stats?.blocks?.find((x) => x.id === b.id)} />
        ))}
      </Reorder.Group>
      <AddDialog open={adding} onClose={() => setAdding(false)} onAdd={insert} brands={brands} />
    </div>
  )
}

function ProfileHeader({ p, set }) {
  const toast = useToast()
  const file = useRef(null)
  const [busy, setBusy] = useState(false)
  const st = p.settings || {}
  const socials = st.socials || []
  const setSocials = (items, extra = {}) => set({ settings: { ...st, socials: items, ...extra } })
  const pick = async (e) => {
    const f = e.target.files?.[0]
    e.target.value = ''
    if (!f) return
    setBusy(true)
    try { set({ avatar_url: await uploadSquare(f) }); toast('Profile image updated') } catch (err) { toast(err.message, 'err') } finally { setBusy(false) }
  }
  const avatarUrl = async () => { const v = await promptAsync('Image URL', { title: 'Use an image URL', value: p.avatar_url, placeholder: 'https://…', ok: 'Use image' }); if (v !== null) set({ avatar_url: v.trim() }) }
  const [draft, setDraftSocial] = useState(null)        // {network, url} while adding an icon
  const addSocial = (e) => {
    e.preventDefault()
    if (!draft.url.trim()) return
    setSocials([...socials, { network: draft.network, url: draft.url.trim() }])
    setDraftSocial(null)
  }
  const editSocial = async (i) => {
    const s = socials[i]
    const url = await promptAsync('Leave empty to remove the icon', { title: `Your ${s.network}`, value: s.url, ok: 'Save' })
    setSocials(url === null || !url.trim() ? socials.filter((_, j) => j !== i) : socials.map((x, j) => (j === i ? { ...x, url: url.trim() } : x)))
  }
  return (
    <section className="rounded-3xl border border-lav-200/70 bg-surface p-5 shadow-soft" data-testid="bio-profile">
      <div className="flex gap-4">
        <div className="shrink-0">
          <Menu label="Profile image" testid="bio-avatar-menu" variant="ghost" size="none" align="left"
            items={[['Upload image', () => file.current?.click(), { icon: ImagePlus, testid: 'bio-avatar-upload' }], ['Use image URL', avatarUrl, { icon: LinkIcon, testid: 'bio-avatar-url' }],
              p.avatar_url && ['Remove', () => set({ avatar_url: '' }), { icon: Trash2, danger: true, testid: 'bio-avatar-remove' }]]}>
            <span className={cx('relative grid size-[76px] place-items-center overflow-hidden rounded-full bg-gradient-to-br from-lav-200 to-lav-400 ring-4 ring-surface', busy && 'animate-pulse')}>
              <img src={p.avatar_url || '/static/mark.png'} alt="" className="size-full object-cover" data-testid="bio-avatar-img" />
              <span className="absolute inset-x-0 bottom-0 bg-black/45 py-0.5 text-[10px] font-semibold text-white">Edit</span>
            </span>
          </Menu>
          <input ref={file} type="file" accept="image/*" className="hidden" onChange={pick} aria-label="Profile image file" data-testid="bio-avatar-file" />
        </div>
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex items-baseline gap-2">
            <input value={p.title} maxLength={30} onChange={(e) => set({ title: e.target.value })} placeholder="Your name or brand" aria-label="Profile title" data-testid="bio-title"
              className="min-w-0 flex-1 rounded-lg bg-transparent px-1 text-lg font-semibold outline-none hover:bg-lav-50 focus:bg-lav-50" />
            <span className="text-[11px] tabular-nums text-muted">{(p.title || '').length}/30</span>
          </div>
          <div className="flex items-start gap-2">
            <textarea value={p.bio} rows={2} maxLength={160} onChange={(e) => set({ bio: e.target.value })} placeholder="Add a short bio" aria-label="Bio" data-testid="bio-bio"
              className="min-w-0 flex-1 resize-none rounded-lg bg-transparent px-1 text-sm text-ink-2 outline-none hover:bg-lav-50 focus:bg-lav-50" />
            <span className="text-[11px] tabular-nums text-muted">{(p.bio || '').length}/160</span>
          </div>
          <div className="flex flex-wrap items-center gap-1.5 pt-1" data-testid="bio-page-socials">
            {socials.map((s, i) => (
              <button key={`${s.network}-${i}`} type="button" onClick={() => editSocial(i)} aria-label={`Edit ${s.network}`} data-testid="bio-social-chip"
                className="grid size-8 cursor-pointer place-items-center rounded-full bg-zinc-900 ring-1 ring-lav-200 transition hover:ring-lav-400 dark:bg-black">
                <img src={socialIcon(s.network)} alt="" className="size-4" onError={(e) => { e.currentTarget.style.display = 'none' }} />
              </button>
            ))}
            <button type="button" onClick={() => setDraftSocial(draft ? null : { network: 'instagram', url: '' })} aria-label="Add social icon" aria-expanded={!!draft} data-testid="bio-social-add"
              className="grid size-8 cursor-pointer place-items-center rounded-full border border-dashed border-lav-300 text-muted transition hover:border-lav-500 hover:text-ink"><Plus className="size-4" /></button>
            {socials.length > 0 && (
              <div className="ml-auto inline-flex rounded-full bg-lav-100 p-0.5 text-[11px]" role="group" aria-label="Social icons position">
                {['top', 'bottom'].map((pos) => (
                  <button key={pos} type="button" onClick={() => setSocials(socials, { socials_position: pos })} data-testid={`bio-socials-${pos}`} aria-pressed={(st.socials_position || 'top') === pos}
                    className={cx('cursor-pointer rounded-full px-2.5 py-1 font-medium capitalize', (st.socials_position || 'top') === pos ? 'bg-surface shadow-soft' : 'text-muted')}>{pos}</button>
                ))}
              </div>
            )}
          </div>
          <AnimatePresence>
            {draft && (
              <motion.form onSubmit={addSocial} initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
                className="flex flex-wrap gap-2 overflow-hidden pt-2" data-testid="bio-social-form">
                <Select value={draft.network} onChange={(e) => setDraftSocial({ ...draft, network: e.target.value })} options={SOCIAL_NETS.map((n) => [n, n])}
                  aria-label="Network" data-testid="bio-social-network" className="h-9 rounded-full" />
                <Input autoFocus value={draft.url} onChange={(e) => setDraftSocial({ ...draft, url: e.target.value })} placeholder={draft.network === 'email' ? 'you@example.com' : 'https://'}
                  aria-label="Profile link" data-testid="bio-social-url" className="h-9 min-w-40 flex-1 rounded-full" />
                <Button size="sm" variant="primary" className="h-9 rounded-full" data-testid="bio-social-save">Add icon</Button>
              </motion.form>
            )}
          </AnimatePresence>
        </div>
      </div>
    </section>
  )
}

function BlockCard({ b, first, last, open, dim, onExpand, onFocus, set, move, onRemove, onDuplicate, brands, stat }) {
  const controls = useDragControls()
  const meta = META[b.type] || META.link
  const linky = b.type === 'link' || b.type === 'brand'
  const brand = b.type === 'brand' && brands.find((x) => x.key === b.brand)
  const now = new Date().toISOString()
  const sched = b.end_at && b.end_at < now ? 'Expired' : (b.start_at && b.start_at > now ? 'Scheduled' : ((b.start_at || b.end_at) ? 'Timed' : ''))
  const onKey = (e) => {
    if (e.key === 'ArrowUp') { e.preventDefault(); move(-1) }
    if (e.key === 'ArrowDown') { e.preventDefault(); move(1) }
  }
  return (
    <Reorder.Item value={b.id} dragListener={false} dragControls={controls} data-testid="bio-block" data-type={b.type} data-id={b.id}
      whileDrag={{ scale: 1.02, boxShadow: '0 24px 48px -16px rgb(76 45 160 / .45)' }}
      className={cx('relative rounded-3xl border bg-surface shadow-soft transition-[border-color,opacity]', open ? 'border-lav-400' : 'border-lav-200/70', b.hidden && 'opacity-60', dim && !open && 'opacity-80')}>
      <div className="flex items-stretch">
        <button type="button" onPointerDown={(e) => { e.preventDefault(); controls.start(e) }} onKeyDown={onKey} aria-label="Drag to reorder (or use the arrow keys)" data-testid="bio-block-grip"
          className="flex w-9 shrink-0 cursor-grab touch-none items-center justify-center rounded-l-3xl text-muted hover:text-ink focus-visible:outline-2 focus-visible:outline-lav-500 active:cursor-grabbing">
          <GripVertical className="size-4" />
        </button>
        <div className="min-w-0 flex-1 py-3 pr-1">
          {linky ? (
            <div className="flex items-start gap-3">
              {(b.thumb || brand) && <img src={b.thumb || brand.icon} alt="" className={cx('mt-0.5 size-10 shrink-0 rounded-xl object-cover', !b.thumb && 'p-2')} style={!b.thumb && brand ? { background: brand.background } : undefined} />}
              <div className="min-w-0 flex-1">
                <input value={b.label || ''} onChange={(e) => set({ label: e.target.value })} onFocus={onFocus} placeholder={brand?.label || hostOf(b.url) || 'Title'}
                  aria-label="Link title" data-testid="bio-block-label" className="w-full rounded-md bg-transparent px-1 text-[15px] font-semibold outline-none placeholder:text-muted hover:bg-lav-50 focus:bg-lav-50" />
                <input value={b.url || ''} onChange={(e) => set({ url: e.target.value })} onFocus={onFocus} placeholder="https://"
                  aria-label="Link URL" data-testid="bio-block-url" className="w-full rounded-md bg-transparent px-1 text-[13px] text-ink-2 outline-none placeholder:text-muted hover:bg-lav-50 focus:bg-lav-50" />
              </div>
            </div>
          ) : (
            <button type="button" onClick={onExpand} className="flex w-full cursor-pointer items-center gap-3 text-left" aria-expanded={open} data-testid="bio-block-summary">
              <span className={cx('grid size-10 shrink-0 place-items-center rounded-xl', meta.tone)}><meta.icon className="size-[18px]" /></span>
              <span className="min-w-0">
                <span className="block text-[11px] font-semibold uppercase tracking-wider text-muted">{meta.label}</span>
                <span className="block truncate text-[15px] font-semibold">{blockTitle(b) || <span className="text-muted">Empty</span>}</span>
              </span>
            </button>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-1.5 pl-1">
            {linky && !b.url && <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-700" data-testid="bio-block-setup"><Wrench className="size-3" />Setup</span>}
            {linky && (
              <button type="button" onClick={onExpand} className="inline-flex cursor-pointer items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium text-muted hover:bg-lav-100 hover:text-ink" aria-expanded={open} data-testid="bio-block-more">
                <ImagePlus className="size-3" /><span className="hidden sm:inline">Layout, power-ups, insights</span><span className="sm:hidden">More</span><ChevronDown className={cx('size-3 transition', open && 'rotate-180')} />
              </button>
            )}
            {b.spotlight && <span className="inline-flex items-center gap-1 rounded-full bg-lav-100 px-2 py-0.5 text-[11px] font-semibold text-lav-700"><Zap className="size-3" />Spotlight</span>}
            {sched && <span className="inline-flex items-center gap-1 rounded-full bg-sky-50 px-2 py-0.5 text-[11px] font-semibold text-sky-700" data-testid="bio-block-schedule"><CalendarClock className="size-3" />{sched}</span>}
            {b.clicks !== undefined && <span className="inline-flex items-center gap-1 text-[11px] text-muted" data-testid="bio-block-clicks"><MousePointerClick className="size-3" />{b.clicks} clicks</span>}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end justify-between gap-2 py-3 pr-3">
          <Switch checked={!b.hidden} onChange={(v) => set({ hidden: !v })} label={b.hidden ? 'Show on page' : 'Hide from page'} testid="bio-block-hide" />
          <Menu label="More actions" icon={MoreVertical} testid="bio-block-menu" size="icon-sm"
            items={[['Duplicate', onDuplicate, { icon: Copy, testid: 'bio-block-duplicate' }],
              !first && ['Move up', () => move(-1), { icon: ArrowUp, testid: 'bio-block-up' }],
              !last && ['Move down', () => move(1), { icon: ArrowDown, testid: 'bio-block-down' }],
              ['Delete', onRemove, { icon: Trash2, danger: true, testid: 'bio-block-remove' }]]} />
        </div>
      </div>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.2 }} className="overflow-hidden">
            <div className="border-t border-lav-100 p-4" onFocus={onFocus}>
              {linky ? <LinkPanel b={b} set={set} stat={stat} /> : <BlockEditor b={b} set={set} />}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </Reorder.Item>
  )
}
