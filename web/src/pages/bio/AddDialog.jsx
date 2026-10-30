// "+ Add" dialog: paste a link or search, categories on the left, block tiles, then a list of
// blocks and brand buttons. A pasted URL becomes the matching brand button (or a plain link); YouTube / Spotify links
// can also be embedded as a player.
import { AnimatePresence, motion } from 'motion/react'
import { AtSign, ChevronRight, Ellipsis, Lightbulb, Link2, Mail, Play, Search, Type, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { cx } from '../../ui'
import { brandFor, hostOf, isEmbed, isUrl, META, withScheme } from './shared'

const CATS = [
  ['suggested', 'Suggested', Lightbulb], ['social', 'Social', AtSign], ['media', 'Media', Play],
  ['contact', 'Contact', Mail], ['text', 'Text', Type], ['all', 'View all', Ellipsis],
]
const CAT_BLOCKS = {
  suggested: ['link', 'embed', 'email', 'contact'], social: ['socials'], media: ['embed', 'image', 'posts'],
  contact: ['email', 'contact'], text: ['heading', 'text', 'spacer'],
  all: ['link', 'heading', 'embed', 'email', 'contact', 'image', 'posts', 'socials', 'text', 'spacer'],
}
const CAT_BRANDS = {
  suggested: ['instagram', 'tiktok', 'yt', 'spotify', 'x', 'linked'],
  social: ['instagram', 'tiktok', 'x', 'faceb', 'linked', 'threads', 'bluesky', 'mastodon', 'snapchat', 'pinterest', 'reddit', 'discord', 'github', 'tumb'],
  media: ['yt', 'spotify', 'apple-music', 'soundcloud', 'twitch', 'vimeo', 'apple-podcasts', 'amazon-music', 'bandcamp'],
  contact: ['whatsapp', 'telegram', 'signal', 'calendly', 'messenger'],
  text: [],
}
const TILES = ['link', 'heading', 'embed', 'email', 'contact', 'image', 'spacer']

export function AddDialog({ open, onClose, onAdd, brands }) {
  const [q, setQ] = useState('')
  const [cat, setCat] = useState('suggested')
  const input = useRef(null)
  useEffect(() => { if (open) { setQ(''); setCat('suggested'); setTimeout(() => input.current?.focus(), 50) } }, [open])
  useEffect(() => {
    if (!open) return
    const k = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [open, onClose])

  const pasted = isUrl(q) ? withScheme(q) : ''
  const brand = pasted ? brandFor(pasted, brands) : null
  const s = q.trim().toLowerCase()
  const rows = useMemo(() => {
    const blocks = (s && !pasted ? CAT_BLOCKS.all : CAT_BLOCKS[cat]).filter((k) => !s || pasted || `${META[k].label} ${META[k].desc}`.toLowerCase().includes(s))
    const br = s && !pasted
      ? brands.filter((b) => b.label.toLowerCase().includes(s) || b.key.includes(s) || b.aliases.some((a) => a.includes(s)))
      : (cat === 'all' ? brands : (CAT_BRANDS[cat] || []).map((k) => brands.find((b) => b.key === k)).filter(Boolean))
    return { blocks: pasted ? [] : blocks, brands: pasted ? [] : br }
  }, [s, pasted, cat, brands])

  const addPasted = (embed = false) => {
    if (embed) return onAdd({ type: 'embed', url: pasted })
    if (brand && brand.key !== 'default') return onAdd({ type: 'brand', brand: brand.key, label: brand.label, url: pasted })
    return onAdd({ type: 'link', label: hostOf(pasted), url: pasted })
  }
  const submit = (e) => {
    e.preventDefault()
    if (pasted) addPasted(false)
    else if (rows.brands.length + rows.blocks.length === 1) (rows.blocks[0] ? onAdd({ type: rows.blocks[0] }) : pickBrand(rows.brands[0]))
  }
  const pickBrand = (b) => onAdd({ type: 'brand', brand: b.key, label: b.label, url: '' })

  return (
    <AnimatePresence>
      {open && (
        <motion.div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-night/40 p-3 pt-[5vh] backdrop-blur-sm"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
          <motion.div role="dialog" aria-modal="true" aria-label="Add" data-testid="bio-add-dialog"
            initial={{ opacity: 0, y: 24, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 12, scale: 0.98 }}
            transition={{ type: 'spring', stiffness: 380, damping: 32 }}
            className="flex max-h-[88vh] w-full max-w-3xl flex-col overflow-hidden rounded-[2rem] border border-lav-200 bg-surface shadow-pop">
            <header className="relative flex items-center justify-center px-6 pb-2 pt-5">
              <h2 className="text-base font-semibold">Add</h2>
              <button type="button" onClick={onClose} aria-label="Close" data-testid="bio-add-close" className="absolute right-5 top-4 grid size-9 cursor-pointer place-items-center rounded-full hover:bg-lav-100"><X className="size-4" /></button>
            </header>
            <form onSubmit={submit} className="px-5 pb-3">
              <label className="flex h-12 items-center gap-3 rounded-full bg-lav-100 px-4 focus-within:ring-4 focus-within:ring-lav-200/70">
                <Search className="size-4 text-muted" />
                <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Paste a link or search…" aria-label="Paste a link or search"
                  data-testid="bio-add-search" className="h-full min-w-0 flex-1 bg-transparent text-[15px] outline-none placeholder:text-muted" />
              </label>
            </form>
            <div className="grid min-h-0 flex-1 gap-4 overflow-y-auto px-5 pb-5 sm:grid-cols-[180px_1fr]">
              <nav className="flex gap-1 overflow-x-auto sm:flex-col" aria-label="Categories">
                {CATS.map(([k, l, I]) => (
                  <button key={k} type="button" onClick={() => { setCat(k); setQ('') }} aria-pressed={cat === k && !s} data-testid={`bio-add-cat-${k}`}
                    className={cx('flex shrink-0 cursor-pointer items-center gap-2.5 rounded-xl px-3 py-2 text-sm transition', cat === k && !s ? 'bg-lav-100 font-semibold' : 'text-ink-2 hover:bg-lav-50')}>
                    <I className="size-4" />{l}
                  </button>
                ))}
              </nav>
              <div className="min-w-0 space-y-4">
                {pasted ? (
                  <div className="space-y-2" data-testid="bio-add-pasted">
                    <h3 className="px-1 text-xs font-semibold uppercase tracking-wider text-muted">Add this link</h3>
                    <Row onClick={() => addPasted(false)} testid="bio-add-paste"
                      icon={brand && brand.key !== 'default'
                        ? <span className="grid size-10 place-items-center rounded-full" style={{ background: brand.background }}><img src={brand.icon} alt="" className="size-5" /></span>
                        : <span className="grid size-10 place-items-center rounded-full bg-zinc-900 text-white"><Link2 className="size-4" /></span>}
                      title={brand && brand.key !== 'default' ? `${brand.label.replace(/^(Listen|Watch|Follow|Join|Subscribe) on /, '')} button` : `Link to ${hostOf(pasted)}`}
                      desc={pasted} />
                    {isEmbed(pasted) && <Row onClick={() => addPasted(true)} testid="bio-add-paste-embed" icon={<span className="grid size-10 place-items-center rounded-full bg-red-600 text-white"><Play className="size-4" /></span>}
                      title="Embed the player" desc="Visitors watch or listen without leaving your page" />}
                  </div>
                ) : (
                  <>
                    {!s && (cat === 'suggested' || cat === 'all') && (
                      <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
                        {TILES.map((k) => {
                          const M = META[k]
                          return (
                            <button key={k} type="button" onClick={() => onAdd({ type: k })} data-testid={`bio-add-${k}`}
                              className="flex h-20 cursor-pointer flex-col justify-between rounded-2xl bg-lav-100 p-3 text-left text-sm font-medium transition hover:bg-lav-200">
                              {k === 'heading' ? 'Header' : M.label.replace('YouTube / Spotify', 'Embed')}<M.icon className="size-5" />
                            </button>
                          )
                        })}
                      </div>
                    )}
                    <div className="space-y-1">
                      <h3 className="px-1 text-xs font-semibold uppercase tracking-wider text-muted">{s ? `Results for “${q.trim()}”` : CATS.find(([k]) => k === cat)[1]}</h3>
                      {rows.blocks.map((k) => {
                        const M = META[k]
                        return <Row key={k} onClick={() => onAdd({ type: k })} testid={`bio-add-row-${k}`} title={M.label} desc={M.desc}
                          icon={<span className={cx('grid size-10 place-items-center rounded-full', M.tone)}><M.icon className="size-4" /></span>} />
                      })}
                      {rows.brands.map((b) => (
                        <Row key={b.key} onClick={() => pickBrand(b)} testid={`bio-brand-${b.key}`} title={b.label} desc="Brand button with its colours and logo"
                          icon={<span className="grid size-10 place-items-center rounded-full" style={{ background: b.background, border: b.border || undefined }}><img src={b.icon} alt="" className="size-5" /></span>} />
                      ))}
                      {!rows.blocks.length && !rows.brands.length && <p className="px-1 py-6 text-sm text-muted">Nothing matches “{q}”. Paste a full link to add it as a button.</p>}
                    </div>
                  </>
                )}
                <p className="px-1 text-[11px] text-muted">Brand colours and icons from LittleLink (MIT). Logos are trademarks of their owners.</p>
              </div>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

function Row({ icon, title, desc, onClick, testid }) {
  return (
    <button type="button" onClick={onClick} data-testid={testid}
      className="group flex w-full cursor-pointer items-center gap-3 rounded-2xl px-2 py-2 text-left transition hover:bg-lav-50 focus-visible:bg-lav-50 focus-visible:outline-none">
      {icon}
      <span className="min-w-0 flex-1"><span className="block truncate text-[15px] font-medium">{title}</span><span className="block truncate text-xs text-muted">{desc}</span></span>
      <ChevronRight className="size-4 text-muted transition group-hover:translate-x-0.5 group-hover:text-ink" />
    </button>
  )
}
