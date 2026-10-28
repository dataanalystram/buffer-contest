import { AnimatePresence, motion, Reorder } from 'motion/react'
import {
  CalendarClock, ChevronDown, Clock, FolderUp, Hash, ImagePlus, Layers, Megaphone, PenLine, Rocket, Save,
  Camera, Smile, Sparkles, Trash2, Wand2, X, Zap, FileArchive, Eye, EyeOff, SlidersHorizontal, Heart, MessageCircle, Send, Bookmark, Music2, Users, Bot, Image as ImageIcon, Film, CircleDashed, Check,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { get, patch, post, upload } from '../api'
import { go, parseHash, setQuery, useRoute } from '../router'
import { useStore } from '../store'
import ImageEditor from '../components/ImageEditor'
import UnsplashPicker from '../components/UnsplashPicker'
import { Badge, Button, ChannelAvatar, cx, Field, Input, Kbd, NETWORKS, Select, Sheet, Switch, Textarea, useOutside, useToast } from '../ui'
import { AiWriter } from '../components/AiWriter'
import ThreadEditor, { asParts } from '../components/ThreadEditor'

const EMOJI = '🚀 🔥 🤯 ✨ 🧠 🤖 ⚡ 📈 💡 🎯 👀 🙌 ✅ ❌ 🟣 💜 🧵 📌 🗞️ 🆕 🎉 🛠️ 💸 🌍'.split(' ')
const WHEN = [['queue', 'Next available', CalendarClock], ['prioritize', 'Prioritize', Zap], ['now', 'Publish now', Rocket], ['custom', 'Custom time', Clock]]
// composer-level options that fan out to every selected channel whose connector supports them
const AI_KEYS = { instagram: 'is_ai_generated', tiktok: 'is_aigc' }
const IG_TYPES = [['post', 'Post', ImageIcon], ['carousel', 'Carousel', Layers], ['reel', 'Reel', Film], ['story', 'Story', CircleDashed]]

const hashtagCount = (t) => (t.match(/(^|[^\w&])#\w+/gu) || []).length
const u16 = (t) => [...(t || '')].reduce((n, ch) => n + (ch.codePointAt(0) > 0xffff ? 2 : 1), 0)

function Popover({ label, icon: Icon, testid, children, placement = 'up' }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  useOutside(ref, () => setOpen(false))
  return (
    <div className="relative" ref={ref}>
      <Button type="button" size="sm" variant="ghost" aria-label={label} title={label} data-testid={testid} onClick={() => setOpen(!open)}><Icon className="size-4" /><span className="hidden xl:inline">{label}</span></Button>
      <AnimatePresence>
        {open && (
          <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
            className={cx('absolute z-30 w-80 rounded-2xl border border-lav-200 bg-popover p-3 shadow-pop', placement === 'down' ? 'right-0 top-10' : 'bottom-10 left-0')} onClick={(e) => e.target.closest('[data-close]') && setOpen(false)}>
            {children}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function OptionField({ f, value, onChange, dynamicChoices, testid }) {
  if (f.type === 'toggle') return (
    <div className="flex items-center justify-between gap-3 py-1">
      <span className="text-[13px] text-ink-2">{f.label}{f.help && <span className="block text-[11px] text-muted">{f.help}</span>}</span>
      <Switch checked={!!value} onChange={onChange} label={f.label} testid={testid} />
    </div>
  )
  const choices = dynamicChoices || f.choices
  return (
    <Field label={f.label} hint={f.help}>
      {(f.type === 'select' || f.type === 'media' || f.type === 'playlist') ? <Select className="w-full" value={value ?? ''} onChange={(e) => onChange(e.target.value)} options={choices || [['', f.type === 'media' ? 'None (YouTube picks a frame)' : 'None']]} data-testid={testid} aria-label={f.label} />
        : f.type === 'datetime' ? <Input type="datetime-local" value={(value || '').slice(0, 16)} onChange={(e) => onChange(e.target.value ? new Date(e.target.value).toISOString().replace('.000', '') : '')} data-testid={testid} aria-label={f.label} />
        : f.type === 'textarea' ? <Textarea rows={2} value={value ?? ''} onChange={(e) => onChange(e.target.value)} data-testid={testid} aria-label={f.label} />
          : <Input type={f.type === 'number' ? 'number' : 'text'} value={value ?? ''} onChange={(e) => onChange(f.type === 'number' ? (e.target.value === '' ? null : Number(e.target.value)) : e.target.value)} data-testid={testid} aria-label={f.label} />}
    </Field>
  )
}

function Preview({ ch, caption, media, options }) {
  const m = media[0]
  const yt = ch.provider === 'youtube'
  const vertical = yt ? options?.yt_format !== 'video' : (ch.provider !== 'instagram' || options?.ig_type === 'reel' || options?.ig_type === 'story' || m?.kind === 'video')
  const name = NETWORKS[ch.provider]?.name
  return (
    <div className="w-[240px] shrink-0" data-testid={`preview-${ch.provider}`}>
      <div className="mb-2 flex items-center gap-2 text-xs font-medium text-muted"><ChannelAvatar ch={ch} size={20} />{name} · @{ch.username}</div>
      <div className={cx('overflow-hidden rounded-2xl border border-lav-200 bg-surface shadow-soft', ch.provider !== 'instagram' && 'bg-zinc-950 text-white')}>
        <div className={cx('relative bg-lav-100', vertical ? 'aspect-[9/16]' : 'aspect-square')}>
          {m ? (m.kind === 'document' ? <div className="flex size-full flex-col items-center justify-center gap-1 bg-lav-50 text-[10px] font-semibold text-lav-700" data-testid="media-document"><span className="rounded bg-lav-600 px-1.5 py-0.5 text-white">PDF</span><span className="max-w-full truncate px-1">{m.filename}</span></div> : m.kind === 'video' ? <video src={m.url} className="size-full object-cover" muted /> : <img src={m.url} alt="" className="size-full object-cover" />)
            : <div className="grid size-full place-items-center text-xs text-muted">Add media to preview</div>}
          {media.length > 1 && <Badge tone="ink" className="absolute right-2 top-2"><Layers className="size-3" />1/{media.length}</Badge>}
          {ch.provider !== 'instagram' && !(yt && !vertical) && (
            <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/80 p-3 text-[11px] leading-snug text-white">
              {yt && <b className="mb-0.5 block text-[12px]" data-testid="preview-youtube-title">{options?.yt_title || 'Add a title'}</b>}
              <b>@{ch.username}</b> <span className="line-clamp-3 whitespace-pre-wrap">{caption}</span>
              {ch.provider === 'tiktok' && <div className="mt-1 flex items-center gap-1 opacity-80"><Music2 className="size-3" />original sound</div>}
            </div>
          )}
        </div>
        {yt && !vertical && (
          <div className="p-3 text-[12px]" data-testid="preview-youtube-video">
            <p className="line-clamp-2 font-semibold" data-testid="preview-youtube-title">{options?.yt_title || 'Add a title'}</p>
            <p className="mt-0.5 text-white/60">{ch.display_name || ch.username} · {({ public: 'Public', unlisted: 'Unlisted', private: 'Private' })[options?.yt_privacy || 'public']}{options?.yt_publish_at ? ' · premieres ' + new Date(options.yt_publish_at).toLocaleString() : ''}</p>
            <p className="mt-1 line-clamp-2 whitespace-pre-wrap text-white/70">{caption}</p>
          </div>
        )}
        {yt && !vertical && (
          <div className="p-3 text-[12px]" data-testid="preview-youtube-video">
            <p className="line-clamp-2 font-semibold" data-testid="preview-youtube-title">{options?.yt_title || 'Add a title'}</p>
            <p className="mt-0.5 text-white/60">{ch.display_name || ch.username} · {({ public: 'Public', unlisted: 'Unlisted', private: 'Private' })[options?.yt_privacy || 'public']}{options?.yt_publish_at ? ' · premieres ' + new Date(options.yt_publish_at).toLocaleString() : ''}</p>
            <p className="mt-1 line-clamp-2 whitespace-pre-wrap text-white/70">{caption}</p>
          </div>
        )}
        {ch.provider === 'instagram' && (
          <div className="p-3 text-[12px]">
            <div className="mb-2 flex gap-3 text-ink"><Heart className="size-4" /><MessageCircle className="size-4" /><Send className="size-4" /><Bookmark className="ml-auto size-4" /></div>
            <p className="line-clamp-4 whitespace-pre-wrap"><b>{ch.username}</b> {caption}</p>
          </div>
        )}
      </div>
      {asParts(options?.thread).filter((p) => p.text.trim() || p.media_ids.length).map((p, i) => (
        <div key={i} className="mt-2 flex gap-2 rounded-2xl border border-lav-200 bg-zinc-950 p-3 text-[11px] leading-snug text-white" data-testid="preview-thread-part">
          <ChannelAvatar ch={ch} size={20} />
          <div className="min-w-0"><b>@{ch.username}</b> <span className="text-white/50">· {i + 2}</span><p className="line-clamp-4 whitespace-pre-wrap">{p.text}</p>
            {p.media_ids.length > 0 && <div className="mt-1 flex gap-1">{p.media_ids.map((id) => <img key={id} src={`/files/${id}`} alt="" className="size-10 rounded object-cover" />)}</div>}
          </div>
        </div>
      ))}
    </div>
  )
}

export default function Composer() {
  const { channels, providers, library, settings, reload, can } = useStore()
  const route = useRoute()
  const toast = useToast()
  const editingId = route.query.compose !== 'new' ? route.query.compose : null
  const active = channels.filter((c) => c.status !== 'disconnected')
  // remember the last channels used (falls back to the first connected channel)
  const lastSel = (localStorage.getItem('huhq-last-channels') || '').split(',').filter((id) => active.some((c) => c.id === id))
  const initialSel = route.query.channels === 'all' ? active.map((c) => c.id) : route.query.channels ? route.query.channels.split(',') : lastSel.length ? lastSel : active.slice(0, 1).map((c) => c.id)
  const [sel, setSel] = useState(initialSel)
  const [groups, setGroups] = useState([])
  useEffect(() => { get('/api/channel-groups').then(setGroups).catch(() => {}) }, [])
  useEffect(() => { if (sel.length) localStorage.setItem('huhq-last-channels', sel.join(',')) }, [sel])
  const [caption, setCaption] = useState(route.query.text || '')
  const [media, setMedia] = useState([])
  const [editImage, setEditImage] = useState(null)
  const [unsplash, setUnsplash] = useState(false)
  const [per, setPer] = useState({})      // channel_id -> {caption?, options}
  const [customize, setCustomize] = useState(route.query.step === 'customize')
  const [when, setWhen] = useState(route.query.when || settings?.default_when || 'queue')
  const [at, setAt] = useState(route.query.at || '')
  const [override, setOverride] = useState(false)
  const [another, setAnother] = useState(false)
  const [tags, setTags] = useState([])
  const [issues, setIssues] = useState({})
  const [busy, setBusy] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [drag, setDrag] = useState(false)
  const [accountInfo, setAccountInfo] = useState({})
  const [showPreview, setShowPreview] = useState(localStorage.getItem('huhq-preview') !== 'off')
  const [topic, setTopic] = useState(route.query.topic || '')
  const [campaign, setCampaign] = useState(route.query.campaign || '')
  const [campaigns, setCampaigns] = useState([])
  useEffect(() => { get('/api/campaigns').then((r) => setCampaigns(r.map((c) => c.name))).catch(() => {}) }, [])
  const [suggest, setSuggest] = useState({})      // channel_id -> {key, format, reason, formats}
  const [pinnedFormat, setPinnedFormat] = useState({}) // channel_id -> true once the user picked by hand
  const fileRef = useRef(null)
  const folderRef = useRef(null)
  const textRef = useRef(null)
  const editing = useRef(null)

  const selected = active.filter((c) => sel.includes(c.id))
  const close = () => { const { path, query } = parseHash(); delete query.compose; delete query.channels; delete query.step; delete query.when; delete query.text; delete query.at; delete query.media; go(path, query) }

  // ?media=id1,id2 (Library › Use in a post) starts the post with those files
  useEffect(() => {
    if (editingId || !route.query.media) return
    const ids = route.query.media.split(',')
    get('/api/media?limit=500').then((all) => setMedia(ids.map((id) => all.find((m) => m.id === id)).filter(Boolean))).catch(() => {})
  }, [editingId, route.query.media])

  // load an existing post for editing
  useEffect(() => {
    if (!editingId) return
    get(`/api/targets/${editingId}`).then((t) => {
      editing.current = t
      setSel([t.channel_id]); setCaption(t.caption); setMedia(t.media); setTags(t.tags || []); setTopic(t.topic_key || ''); setCampaign(t.campaign || '')
      setPer({ [t.channel_id]: { options: t.options } })
      setWhen(t.status === 'draft' ? 'draft' : t.schedule_mode === 'queue' ? 'queue' : 'custom')
      if (t.scheduled_local_iso) setAt(t.scheduled_local_iso)
      setCustomize(true)
    }).catch((e) => toast(e.message, 'err'))
  }, [editingId])

  // creator info (TikTok privacy options etc.) for selected channels
  useEffect(() => {
    selected.forEach((c) => {
      if (accountInfo[c.id] || c.provider !== 'tiktok') return
      get(`/api/channels/${c.id}/account-info`).then((i) => setAccountInfo((x) => ({ ...x, [c.id]: i }))).catch(() => setAccountInfo((x) => ({ ...x, [c.id]: {} })))
    })
  }, [sel])

  const hasVideo = media.some((m) => m.kind === 'video')
  const [thumbImages, setThumbImages] = useState([])
  const [playlists, setPlaylists] = useState({})
  const ytChans = selected.filter((c) => c.provider === 'youtube')
  useEffect(() => { if (ytChans.length) { get('/api/media?kind=image&limit=200').then(setThumbImages).catch(() => {}); setCustomize(true) } }, [ytChans.length])
  useEffect(() => { ytChans.forEach((c) => playlists[c.id] || get(`/api/channels/${c.id}/playlists`).then((r) => setPlaylists((p) => ({ ...p, [c.id]: r }))).catch(() => setPlaylists((p) => ({ ...p, [c.id]: [] })))) }, [ytChans.map((c) => c.id).join()])
  const optionsFor = (c) => per[c.id]?.options || {}
  const captionFor = (c) => (per[c.id]?.caption ?? caption)

  // live validation (debounced) through the same API agents use
  useEffect(() => {
    if (!sel.length) return setIssues({})
    const t = setTimeout(() => {
      post('/api/validate', {
        caption, media_ids: media.map((m) => m.id), channel_ids: sel, topic_key: topic, target_id: editingId || undefined,
        channels: sel.map((id) => ({ channel_id: id, caption: per[id]?.caption, options: per[id]?.options })),
      }).then((r) => setIssues(r.issues)).catch(() => {})
    }, 350)
    return () => clearTimeout(t)
  }, [caption, media, sel, per, topic])

  // format suggestions: video → Reel/Short, several images or a text-heavy caption → carousel
  useEffect(() => {
    if (!sel.length) return
    const t = setTimeout(() => {
      post('/api/suggest-format', { caption, media_ids: media.map((m) => m.id), channel_ids: sel }).then((r) => {
        setSuggest(r)
        setPer((p) => {
          const n = { ...p }
          for (const [cid, sg] of Object.entries(r)) {
            if (pinnedFormat[cid] || editingId) continue
            n[cid] = { ...n[cid], options: { ...(n[cid]?.options || {}), [sg.key]: sg.format } }
          }
          return n
        })
      }).catch(() => {})
    }, 400)
    return () => clearTimeout(t)
  }, [caption, media, sel])
  const pickFormat = (cid, key, v) => { setPinnedFormat((x) => ({ ...x, [cid]: true })); setOpt(cid, key, v) }
  const applySuggestions = () => { setPinnedFormat({}); Object.entries(suggest).forEach(([cid, sg]) => setOpt(cid, sg.key, sg.format)) }

  const allIssues = Object.entries(issues).flatMap(([cid, list]) => list.map((i) => ({ ...i, ch: active.find((c) => c.id === cid) })))
  const errors = allIssues.filter((i) => i.level === 'error')
  const ytMissing = ytChans.length ? [...(hasVideo ? [] : ['a video']), ...(ytChans.every((c) => (optionsFor(c).yt_title || '').trim()) ? [] : ['a title'])] : []

  async function addFiles(files) {
    if (!files?.length) return
    setUploading(true)
    try {
      const r = await upload([...files])
      setMedia((m) => [...m, ...r.media])
      r.errors.forEach((e) => toast(e, 'err'))
      if (r.media.length) toast(`Added ${r.media.length} file${r.media.length > 1 ? 's' : ''}`)
    } catch (e) { toast(e.message, 'err') } finally { setUploading(false) }
  }

  function insert(text) {
    const el = textRef.current
    if (!el) return setCaption((c) => c + text)
    const s = el.selectionStart ?? caption.length, e = el.selectionEnd ?? caption.length
    const next = caption.slice(0, s) + text + caption.slice(e)
    setCaption(next)
    requestAnimationFrame(() => { el.focus(); el.selectionStart = el.selectionEnd = s + text.length })
  }
  const setOpt = (cid, k, v) => setPer((p) => ({ ...p, [cid]: { ...p[cid], options: { ...(p[cid]?.options || {}), [k]: v } } }))
  const setCap = (cid, v) => setPer((p) => ({ ...p, [cid]: { ...p[cid], caption: v } }))
  const fieldsOf = (c) => { const conn = providers.connectors.find((x) => x.id === c.provider); return [...(conn?.common_options || []), ...(conn?.option_schema || [])] }
  const supports = (c, key) => fieldsOf(c).some((f) => f.key === key)
  const fcChannels = selected.filter((c) => supports(c, 'first_comment'))
  const capsOf = (c) => providers.connectors.find((x) => x.id === c.provider)?.capabilities || {}
  const threadChannels = selected.filter((c) => capsOf(c).threads && supports(c, 'thread'))
  const igChannels = selected.filter((c) => c.provider === 'instagram')
  const aiChannels = selected.filter((c) => AI_KEYS[c.provider] && supports(c, AI_KEYS[c.provider]))
  const shared = (list, key, fallback = '') => (list.length ? (optionsFor(list[0])[key] ?? fallback) : fallback)
  const setAll = (list, key, v) => list.forEach((c) => setOpt(c.id, key, v))
  const igType = (() => { const v = shared(igChannels, 'ig_type', 'auto'); return ['reel', 'story', 'carousel'].includes(v) ? v : 'post' })()
  const setIgType = (v) => igChannels.forEach((c) => pickFormat(c.id, 'ig_type', v === 'post' ? (media.length > 1 ? 'carousel' : 'feed') : v))
  const igImages = media.filter((m) => m.kind === 'image')
  const [altBusy, setAltBusy] = useState('')
  const genAlt = async (m) => {
    setAltBusy(m.id)
    try { const r = await post(`/api/media/${m.id}/alt-text`, { save: true }); setMedia((x) => x.map((y) => (y.id === m.id ? { ...y, alt_text: r.alt_text } : y))); toast('Alt text written') } catch (e) { toast(e.message, 'err') }
    setAltBusy('')
  }
  const setAlt = async (m, v) => {
    if ((m.alt_text || '') === v) return
    try { await patch(`/api/media/${m.id}`, { alt_text: v }); setMedia((x) => x.map((y) => (y.id === m.id ? { ...y, alt_text: v } : y))) } catch (e) { toast(e.message, 'err') }
  }
  const aiOn = aiChannels.length > 0 && aiChannels.every((c) => !!optionsFor(c)[AI_KEYS[c.provider]])
  const togglePreview = () => { localStorage.setItem('huhq-preview', showPreview ? 'off' : 'on'); setShowPreview(!showPreview) }
  const ctaOn = selected.length > 0 && selected.every((c) => (optionsFor(c).follow_cta ?? settings?.cta_default ?? 'comment') !== 'off')
  const threadParts = asParts(shared(threadChannels, 'thread', []))
  const setThread = (parts) => setAll(threadChannels, 'thread', parts)
  // splitting rewrites post 1: the shared caption when only thread networks are picked, else their own caption
  const onSplit = (first, rest) => {
    if (threadChannels.length === selected.length) setCaption(first)
    else threadChannels.forEach((c) => setCap(c.id, first))
    setThread(rest)
  }
  const toggleCta = (on) => selected.forEach((c) => setOpt(c.id, 'follow_cta', on ? (settings?.cta_default === 'off' ? 'comment' : settings?.cta_default || 'comment') : 'off'))

  async function submit(mode) {
    const w = mode || when
    setBusy(true)
    try {
      if (editingId) {
        const c = selected[0]
        await patch(`/api/targets/${editingId}`, { caption: captionFor(c), media_ids: media.map((m) => m.id), options: optionsFor(c), tags, topic_key: topic, campaign,
          when: w, scheduled_at: w === 'custom' ? at : undefined, override_rate_limit: override })
        if (w === 'now') await post('/api/worker/tick')
        toast('Post updated')
      } else {
        const r = await post('/api/posts', {
          caption, media_ids: media.map((m) => m.id), channel_ids: sel, tags, topic_key: topic, campaign, when: w, scheduled_at: w === 'custom' ? at : undefined,
          override_rate_limit: override,
          channels: sel.map((id) => ({ channel_id: id, caption: per[id]?.caption, options: per[id]?.options })),
        })
        if (w === 'now') await post('/api/worker/tick')
        if (!can.needs_approval) r.notes?.forEach((n) => toast(n, 'err'))
        const held = r.targets.filter((x) => x.status === 'pending_approval').length
        toast(w === 'draft' ? 'Saved to drafts' : held ? `Sent ${held} post${held > 1 ? 's' : ''} for approval` : w === 'now' ? 'Sending now…' : `Scheduled ${r.targets.length} post${r.targets.length > 1 ? 's' : ''}`)
      }
      window.dispatchEvent(new Event('huhq:posts-changed'))
      reload()
      if (another && !editingId) { setCaption(''); setMedia([]); setPer({}); setTags([]); setTopic(''); setPinnedFormat({}) } else close()
    } catch (e) {
      if (e.detail?.issues) setIssues((x) => ({ ...x, ...e.detail.issues }))
      toast(e.message, 'err')
    } finally { setBusy(false) }
  }

  useEffect(() => {
    const k = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); submit() }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); submit('draft') }
    }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  })

  const counters = useMemo(() => selected.map((c) => {
    const lim = providers.connectors.find((x) => x.id === c.provider)?.caption_limit || 2200
    const n = u16(captionFor(c))
    return { c, n, lim, over: n > lim }
  }), [selected, caption, per])
  const tagsN = hashtagCount(caption)
  const tagLimit = settings?.hashtag_limit ?? 5

  const primaryLabel = can.needs_approval && when !== 'draft' ? (editingId ? 'Save & submit for approval' : 'Submit for approval') : editingId ? 'Save changes' : when === 'now' ? `Publish now${sel.length > 1 ? ` (${sel.length})` : ''}` : when === 'custom' ? 'Schedule' : when === 'prioritize' ? 'Add to top of queue' : 'Add to queue'

  return (
    <Sheet open onClose={close} title={editingId ? 'Edit post' : 'Create post'} testid="composer"
      headerExtra={<>
        <Popover label="Templates" icon={Wand2} testid="composer-templates" placement="down">
          <p className="mb-2 text-xs text-muted">Caption templates</p>
          {library.templates?.map((g) => <button key={g.id} data-close onClick={() => setCaption(g.text)} className="mb-1 block w-full cursor-pointer rounded-xl p-2 text-left text-sm hover:bg-lav-50" data-testid="template"><b>{g.name}</b><span className="line-clamp-2 block text-xs text-muted">{g.text}</span></button>)}
        </Popover>
        <Button size="sm" variant={showPreview ? 'soft' : 'ghost'} aria-pressed={showPreview} onClick={togglePreview} data-testid="composer-preview-toggle">{showPreview ? <Eye className="size-4" /> : <EyeOff className="size-4" />}Preview</Button>
      </>}
      footer={<>
        <span className="mr-auto flex flex-wrap items-center gap-4">
        <label className="flex cursor-pointer items-center gap-2 text-sm text-ink-2"><input type="checkbox" checked={another} onChange={(e) => setAnother(e.target.checked)} data-testid="composer-create-another" className="accent-lav-600" />Create another</label>
        <label className="flex cursor-pointer items-center gap-2 text-sm text-ink-2" title="Ignore the per-day limit for breaking news"><input type="checkbox" checked={override} onChange={(e) => setOverride(e.target.checked)} data-testid="composer-major-news" className="accent-rose-600" /><Megaphone className="size-4 text-rose-500" />Major news override</label>
        </span>
        {ytMissing.length > 0 && <span className="text-xs text-rose-600" role="alert" data-testid="composer-yt-missing">YouTube needs {ytMissing.join(' and ')}</span>}
        <Button variant="outline" onClick={() => submit('draft')} loading={busy && when === 'draft'} data-testid="composer-save-draft" disabled={!sel.length || ytMissing.length > 0}><Save className="size-4" />Save draft <Kbd>⌘S</Kbd></Button>
        <Button variant="primary" onClick={() => submit()} loading={busy} disabled={!sel.length || errors.length > 0 || ytMissing.length > 0 || (when === 'custom' && !at)} data-testid="composer-submit">
          <Send className="size-4" />{primaryLabel} <Kbd>⌘↵</Kbd>
        </Button>
      </>}>
      <div className={cx('grid min-h-full', showPreview && 'lg:grid-cols-[minmax(0,1fr)_440px]')}>
        <div className="min-w-0 space-y-4 p-5">
          {/* channels */}
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Channels">
            {active.map((c) => {
              const on = sel.includes(c.id)
              return (
                <motion.button key={c.id} whileTap={{ scale: 0.95 }} onClick={() => setSel((s) => (on ? s.filter((x) => x !== c.id) : [...s, c.id]))} disabled={!!editingId}
                  aria-pressed={on} aria-label={`${on ? 'Remove' : 'Add'} @${c.username} (${c.provider})`} data-testid="composer-channel" data-channel={c.id}
                  className={cx('flex cursor-pointer items-center gap-2 rounded-full border py-1 pl-1 pr-3 text-[13px] transition disabled:cursor-default',
                    on ? 'border-lav-500 bg-lav-100 text-ink shadow-soft' : 'border-lav-200 bg-surface text-muted opacity-70 hover:opacity-100')}>
                  <ChannelAvatar ch={c} size={28} className={on ? '' : 'grayscale'} />
                  <span className="max-w-32 truncate">{c.username}</span>
                  {on && <Check className="size-3.5 text-lav-700" />}
                </motion.button>
              )
            })}
            {!editingId && groups.map((g) => {
              const ids = g.channel_ids.filter((id) => active.some((c) => c.id === id))
              const on = ids.length > 0 && ids.every((id) => sel.includes(id))
              return <Button key={g.id} size="sm" variant={on ? 'lav' : 'outline'} data-testid="composer-channel-group" aria-pressed={on} disabled={!ids.length}
                onClick={() => setSel((s) => (on ? s.filter((x) => !ids.includes(x)) : [...new Set([...s, ...ids])]))}><Layers className="size-3.5" />{g.name}</Button>
            })}
            {!editingId && active.length > 1 && (
              <Button size="sm" variant="soft" data-testid="composer-all-channels" onClick={() => setSel(sel.length === active.length ? [] : active.map((c) => c.id))}>
                <Zap className="size-3.5" />{sel.length === active.length ? 'Clear' : 'All channels'}
              </Button>
            )}
            {!active.length && <a data-testid="composer-connect-a-channel-first" href="#/channels" className="text-sm font-medium text-lav-700 underline">Connect a channel first</a>}
            <span className="ml-auto text-xs text-muted">{sel.length} selected</span>
          </div>

          {/* editor */}
          <div className={cx('rounded-2xl border bg-surface transition', drag ? 'border-lav-500 ring-4 ring-lav-200' : 'border-lav-200')}
            onDragOver={(e) => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)}
            onDrop={(e) => { e.preventDefault(); setDrag(false); addFiles(e.dataTransfer.files) }}>
            <textarea ref={textRef} value={caption} onChange={(e) => setCaption(e.target.value)} rows={7} data-testid="composer-caption" aria-label="Caption"
              placeholder="What’s the AI news today? Drop images or videos anywhere here…" className="w-full resize-y rounded-t-2xl p-4 text-[15px] leading-relaxed outline-none" />
            {/* media strip, drag to reorder (carousel order) */}
            {(media.length > 0 || uploading) && (
              <Reorder.Group axis="x" values={media} onReorder={setMedia} className="flex gap-2 overflow-x-auto px-4 pb-3" data-testid="composer-media">
                {media.map((m, i) => (
                  <Reorder.Item key={m.id} value={m} className="group relative size-20 shrink-0 cursor-grab overflow-hidden rounded-xl border border-lav-200 active:cursor-grabbing" data-testid="composer-media-item">
                    {m.kind === 'document' ? <div className="flex size-full flex-col items-center justify-center gap-1 bg-lav-50 text-[10px] font-semibold text-lav-700" data-testid="media-document"><span className="rounded bg-lav-600 px-1.5 py-0.5 text-white">PDF</span><span className="max-w-full truncate px-1">{m.filename}</span></div> : m.kind === 'video' ? <video src={m.url} className="size-full object-cover" muted /> : <img src={m.url} alt={m.alt_text} className="size-full object-cover" draggable={false} />}
                    <span className="absolute left-1 top-1 rounded bg-black/60 px-1 text-[10px] text-white">{i + 1}</span>
                    <button onPointerDown={(e) => e.stopPropagation()} onClick={() => setMedia((x) => x.filter((y) => y.id !== m.id))} aria-label={`Remove ${m.filename}`} data-testid="composer-media-remove"
                      className="absolute right-1 top-1 grid size-5 cursor-pointer place-items-center rounded-full bg-black/60 text-white opacity-0 group-hover:opacity-100"><X className="size-3" /></button>
                    {m.kind === 'image' && <button onPointerDown={(e) => e.stopPropagation()} onClick={() => setEditImage(m)} aria-label={`Edit ${m.filename}`} data-testid="composer-media-edit"
                      className="absolute bottom-1 right-1 grid size-5 cursor-pointer place-items-center rounded-full bg-black/60 text-white opacity-0 group-hover:opacity-100 focus:opacity-100"><PenLine className="size-3" /></button>}
                  </Reorder.Item>
                ))}
                {uploading && <div className="grid size-20 shrink-0 place-items-center rounded-xl bg-lav-100 text-xs text-lav-700">Uploading…</div>}
              </Reorder.Group>
            )}
            <div className="flex flex-wrap items-center gap-1 border-t border-lav-100 px-2 py-1.5">
              <input ref={fileRef} type="file" multiple accept="image/*,video/*,.pdf,application/pdf,.zip" hidden onChange={(e) => addFiles(e.target.files)} data-testid="composer-file-input" />
              <input ref={folderRef} type="file" multiple hidden webkitdirectory="" onChange={(e) => addFiles(e.target.files)} data-testid="composer-folder-input" />
              <Button size="sm" variant="ghost" onClick={() => fileRef.current.click()} data-testid="composer-upload"><ImagePlus className="size-4" /><span className="hidden xl:inline">Media</span></Button>
              <Button size="sm" variant="ghost" onClick={() => folderRef.current.click()} data-testid="composer-upload-folder" title="Bulk upload a folder"><FolderUp className="size-4" /><span className="hidden xl:inline">Folder</span></Button>
              <Button size="sm" variant="ghost" onClick={() => fileRef.current.click()} title="Bulk upload a .zip" data-testid="composer-upload-zip"><FileArchive className="size-4" /><span className="hidden xl:inline">Zip</span></Button>
              <Button size="sm" variant="ghost" onClick={() => setUnsplash(true)} title="Free photos from Unsplash" data-testid="composer-unsplash"><Camera className="size-4" /><span className="hidden xl:inline">Unsplash</span></Button>
              <Popover label="Emoji" icon={Smile} testid="composer-emoji">
                <div className="grid grid-cols-8 gap-1">{EMOJI.map((e) => <button key={e} data-close onClick={() => insert(e)} className="cursor-pointer rounded-lg p-1 text-xl hover:bg-lav-100" data-testid="emoji">{e}</button>)}</div>
              </Popover>
              <AiWriter caption={caption} onUse={setCaption} providers={[...new Set(selected.map((c) => c.provider))]} />
              <Popover label="Hashtags" icon={Hash} testid="composer-hashtags">
                <p className="mb-2 text-xs text-muted">Saved hashtag groups (edit in Settings → Library)</p>
                {library.hashtag_groups?.map((g) => <button key={g.id} data-close onClick={() => insert((caption.endsWith('\n') || !caption ? '' : '\n\n') + g.text)} className="mb-1 block w-full cursor-pointer rounded-xl p-2 text-left text-sm hover:bg-lav-50" data-testid="hashtag-group"><b>{g.name}</b><span className="block truncate text-xs text-muted">{g.text}</span></button>)}
              </Popover>

              <div className="ml-auto flex flex-wrap items-center gap-1.5 pr-2" data-testid="composer-counters">
                <Badge tone={tagsN > tagLimit ? 'amber' : 'gray'} title="Hashtags"><Hash className="size-3" />{tagsN}/{tagLimit}</Badge>
                {counters.map(({ c, n, lim, over }) => <Badge key={c.id} tone={over ? 'red' : 'gray'}><span className="capitalize">{NETWORKS[c.provider]?.name}</span> {n}/{lim}</Badge>)}
              </div>
            </div>
          </div>

          {/* post format per channel - first-class, with suggestions */}
          {selected.some((c) => suggest[c.id] && c.provider !== 'instagram') && (
            <div className="rounded-2xl border border-lav-200 bg-surface p-4" data-testid="composer-formats" aria-label="Post format per channel">
              <div className="mb-3 flex items-center gap-2">
                <Layers className="size-4 text-lav-600" /><span className="text-sm font-semibold">Format</span>
                <Button size="sm" variant="soft" className="ml-auto" onClick={applySuggestions} data-testid="composer-suggest-format"><Sparkles className="size-3.5" />Use suggestions</Button>
              </div>
              <div className="space-y-2">
                {selected.filter((c) => suggest[c.id] && c.provider !== 'instagram').map((c) => {
                  const sg = suggest[c.id]
                  const cur = optionsFor(c)[sg.key] || sg.format
                  return (
                    <div key={c.id} className="flex flex-wrap items-center gap-2" data-testid={`format-row-${c.provider}`}>
                      <ChannelAvatar ch={c} size={26} />
                      <div className="flex flex-wrap gap-1" role="radiogroup" aria-label={`${NETWORKS[c.provider]?.name} format`}>
                        {sg.formats.map(([v, l]) => (
                          <button key={v} role="radio" aria-checked={cur === v} onClick={() => pickFormat(c.id, sg.key, v)} data-testid={`format-${c.provider}-${v}`}
                            className={cx('cursor-pointer rounded-lg border px-2.5 py-1 text-xs transition', cur === v ? 'border-lav-600 bg-lav-600 text-white' : 'border-lav-200 text-ink-2 hover:border-lav-400')}>
                            {l}{sg.format === v && <span className="ml-1 opacity-80">★</span>}
                          </button>
                        ))}
                      </div>
                      <span className="text-[11px] text-muted" data-testid={`format-reason-${c.provider}`}>★ {sg.reason}</span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Instagram: Post / Reel / Story + collaborators (Buffer-style, all free) */}
          {igChannels.length > 0 && (
            <section className="rounded-2xl border border-lav-200 bg-surface p-4" data-testid="composer-instagram" aria-label="Instagram options">
              <div className="flex flex-wrap items-center gap-3">
                <span className="flex items-center gap-2 text-sm font-semibold"><span className="inline-grid size-6 place-items-center rounded-full bg-gradient-to-br from-amber-400 via-pink-500 to-purple-600 text-white"><ImageIcon className="size-3.5" /></span>Instagram</span>
                <div className="inline-flex rounded-xl bg-lav-100 p-1" role="radiogroup" aria-label="Instagram post type">
                  {IG_TYPES.map(([v, l, I]) => (
                    <button key={v} role="radio" aria-checked={igType === v} onClick={() => setIgType(v)} data-testid={`composer-ig-type-${v}`}
                      className={cx('flex cursor-pointer items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium transition', igType === v ? 'bg-surface text-ink shadow-soft' : 'text-muted hover:text-ink')}><I className="size-3.5" />{l}</button>
                  ))}
                </div>
                {suggest[igChannels[0].id] && <span className="text-[11px] text-muted" data-testid="format-reason-instagram">★ {suggest[igChannels[0].id].reason}</span>}
              </div>
              {igType !== 'story' && (
                <Field label="Collaborators" hint="Up to 3 Instagram usernames, comma separated. They get an invite to co-author the post." className="mt-3">
                  <div className="relative"><Users className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted" />
                    <Input className="pl-9" placeholder="@username, @another" value={shared(igChannels, 'collaborators')} onChange={(e) => setAll(igChannels, 'collaborators', e.target.value)} data-testid="composer-collaborators" /></div>
                </Field>
              )}
              {igType !== 'story' && igChannels.some((c) => c.auth_mode !== 'fb_login') && (
                <p className="mt-1.5 rounded-lg bg-amber-50 px-2.5 py-1.5 text-[11px] text-amber-900" data-testid="composer-collaborators-fb-warning">
                  Collaborators and location only work for Instagram accounts connected through Facebook Login (a linked Facebook Page).
                  {' '}{igChannels.filter((c) => c.auth_mode !== 'fb_login').map((c) => '@' + c.username).join(', ')} {igChannels.filter((c) => c.auth_mode !== 'fb_login').length === 1 ? 'uses' : 'use'} Instagram Login, so they won’t be sent.</p>
              )}
              {igImages.length > 0 && igType !== 'story' && (
                <Field label="Alt text" hint="Describes each image for screen readers. Sent to Instagram with the post and saved on the file in your Library." className="mt-3">
                  <div className="space-y-2" data-testid="composer-alt-text">
                    {igImages.map((m) => (
                      <div key={m.id} className="flex items-center gap-2">
                        <img src={m.url} alt="" className="size-9 shrink-0 rounded-lg object-cover" />
                        <Input key={m.alt_text || ''} defaultValue={m.alt_text || ''} placeholder={`Describe ${m.filename}`} onBlur={(e) => setAlt(m, e.target.value)} data-testid="composer-alt-input" aria-label={`Alt text for ${m.filename}`} />
                        <Button size="icon-sm" variant="ghost" loading={altBusy === m.id} onClick={() => genAlt(m)} aria-label={`Write alt text for ${m.filename} with AI`} data-testid="composer-alt-generate"><Sparkles className="size-4 text-lav-600" /></Button>
                      </div>
                    ))}
                  </div>
                </Field>
              )}
            </section>
          )}

          {/* first comment + AI disclosure fan out to every selected channel that supports them */}
          {(fcChannels.length > 0 || aiChannels.length > 0 || threadChannels.length > 0) && (
            <section className="space-y-3 rounded-2xl border border-lav-200 bg-surface p-4" data-testid="composer-post-options" aria-label="Post options">
              {threadChannels.length > 0 && (
                <ThreadEditor parts={threadParts} setParts={setThread} provider={threadChannels[0].provider}
                  limit={Math.min(...threadChannels.map((c) => capsOf(c).thread_limit || 280))}
                  caption={threadChannels.length === selected.length ? caption : captionFor(threadChannels[0])} onSplit={onSplit}
                  networks={threadChannels.map((c) => NETWORKS[c.provider]?.name || c.provider).filter((v, i, a) => a.indexOf(v) === i).join(', ')} />
              )}
              {fcChannels.length > 0 && (
                <Field label={<span className="flex items-center gap-1.5"><MessageCircle className="size-3.5 text-lav-600" />First comment <span className="font-normal text-muted">· {fcChannels.map((c) => NETWORKS[c.provider]?.name).filter((v, i, a) => a.indexOf(v) === i).join(', ')}</span></span>}
                  hint="Posted as the first comment right after the post goes live. Great for hashtags and links.">
                  <Textarea rows={2} placeholder="Add a first comment…" value={shared(fcChannels, 'first_comment')} onChange={(e) => setAll(fcChannels, 'first_comment', e.target.value)} data-testid="composer-first-comment" />
                </Field>
              )}
              {aiChannels.length > 0 && (
                <div className="flex items-center justify-between gap-3">
                  <span className="flex items-center gap-2 text-[13px] text-ink-2"><Bot className="size-4 text-lav-600" /><span>AI-generated content label<span className="block text-[11px] text-muted">Adds the network’s “Made with AI” disclosure ({aiChannels.map((c) => NETWORKS[c.provider]?.name).filter((v, i, a) => a.indexOf(v) === i).join(', ')}).</span></span></span>
                  <Switch checked={aiOn} onChange={(v) => aiChannels.forEach((c) => setOpt(c.id, AI_KEYS[c.provider], v))} label="AI-generated content label" testid="composer-ai-disclosure" />
                </div>
              )}
            </section>
          )}

          {/* topic key: the same story never goes to a channel twice */}
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <label htmlFor="topic" className="text-ink-2">Topic key</label>
            <Input id="topic" className="h-9 max-w-xs" placeholder="e.g. 2026-10-22-gpt6-preview" value={topic} onChange={(e) => setTopic(e.target.value)} data-testid="composer-topic" />
            <Button size="sm" variant="ghost" data-testid="composer-topic-auto" onClick={() => setTopic(new Date().toISOString().slice(0, 10) + '-' + (caption.split('\n')[0] || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40))}>Auto</Button>
            <span className="text-xs text-muted">Same media or topic is never posted to a channel twice.</span>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <label htmlFor="campaign" className="text-ink-2">Campaign</label>
            <Input id="campaign" list="campaign-list" className="h-9 max-w-xs" placeholder="e.g. DevDay week" value={campaign} onChange={(e) => setCampaign(e.target.value)} data-testid="composer-campaign" />
            <datalist id="campaign-list">{campaigns.map((c) => <option key={c} value={c} />)}</datalist>
            <span className="text-xs text-muted">Group posts to filter the calendar and queue by campaign.</span>
          </div>

          {/* quick toggles */}
          <div className="flex flex-wrap items-center gap-4 rounded-2xl bg-lav-50 px-4 py-3">
            <div className="flex items-center gap-2 text-sm"><Switch checked={ctaOn} onChange={toggleCta} label="Auto Follow CTA" testid="composer-cta-toggle" /><span>Auto “{settings?.cta_handle || '@huhbyte'}” CTA {hasVideo ? '' : <span className="text-xs text-muted">(applies to video posts)</span>}</span></div>
            <div className="flex items-center gap-2 text-sm"><Switch checked={customize} onChange={setCustomize} label="Customize per network" testid="composer-customize" /><SlidersHorizontal className="size-4 text-lav-600" />Customize for each network</div>
            <div className="ml-auto flex flex-wrap gap-1">
              {library.tags?.map((t) => {
                const on = tags.includes(t.id)
                return <button key={t.id} onClick={() => setTags(on ? tags.filter((x) => x !== t.id) : [...tags, t.id])} aria-pressed={on} data-testid="composer-tag"
                  className={cx('cursor-pointer rounded-full border px-2.5 py-0.5 text-xs', on ? 'border-transparent text-white' : 'border-lav-200 text-ink-2')} style={on ? { background: t.color } : {}}>{t.name}</button>
              })}
            </div>
          </div>

          {/* per-network customisation */}
          <AnimatePresence>
            {customize && selected.map((c) => {
              const conn = providers.connectors.find((x) => x.id === c.provider)
              const opts = { ...Object.fromEntries([...(conn?.common_options || []), ...(conn?.option_schema || [])].map((f) => [f.key, f.default])), ...optionsFor(c) }
              const fields = [...(conn?.common_options || []), ...(conn?.option_schema || [])].filter((f) => f.type !== 'thread' &&
                (!f.show_if || Object.entries(f.show_if).every(([k, vals]) => vals.includes(opts[k]))) && (!f.show_if_media || (f.show_if_media === 'video' ? hasVideo : true)))
              const info = accountInfo[c.id]
              return (
                <motion.section key={c.id} initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }}
                  className="overflow-hidden rounded-2xl border border-lav-200 bg-surface" data-testid={`customize-${c.provider}`} aria-label={`${conn?.name} options`}>
                  <header className="flex items-center gap-3 border-b border-lav-100 px-4 py-3">
                    <ChannelAvatar ch={c} size={28} /><b className="text-sm">{conn?.name} · @{c.username}</b>
                    {info?.creator_nickname && <span className="text-xs text-muted">posting as {info.creator_nickname}</span>}
                    {per[c.id]?.caption !== undefined && <Button size="sm" variant="ghost" className="ml-auto" onClick={() => setPer((p) => ({ ...p, [c.id]: { ...p[c.id], caption: undefined } }))}>Use shared caption</Button>}
                  </header>
                  <div className="grid gap-4 p-4 md:grid-cols-2">
                    <Field label="Caption for this network" className="md:col-span-2">
                      <Textarea rows={3} value={captionFor(c)} onChange={(e) => setCap(c.id, e.target.value)} data-testid={`customize-caption-${c.provider}`} aria-label={`${conn?.name} caption`} />
                    </Field>
                    {c.provider === 'youtube' && <p className={cx('-mt-2 text-right text-[11px] md:col-span-2', new TextEncoder().encode(captionFor(c)).length > 5000 ? 'text-rose-600' : 'text-muted')} data-testid="customize-caption-count-youtube">
                      Description {new TextEncoder().encode(captionFor(c)).length}/5000</p>}
                    {c.provider === 'instagram' && c.auth_mode !== 'fb_login' && <p className="text-[11px] text-amber-800 md:col-span-2" data-testid="customize-ig-fb-warning">Location and collaborators need Facebook Login (a linked Facebook Page); this channel uses Instagram Login, so they won’t be sent.</p>}
                    {fields.map((f) => (
                      <OptionField key={f.key} f={f} value={opts[f.key]} onChange={(v) => setOpt(c.id, f.key, v)} testid={`opt-${c.provider}-${f.key}`}
                        dynamicChoices={f.type === 'media' ? [['', 'None (YouTube picks a frame)'], ...thumbImages.map((m) => [m.id, m.title || m.filename])]
                          : f.type === 'playlist' ? [['', 'No playlist'], ...(playlists[c.id] || []).map((p) => [p.id, p.title])]
                            : f.dynamic && info?.[f.dynamic] ? [['', 'Choose…'], ...info[f.dynamic]] : null} />
                    ))}
                  </div>
                </motion.section>
              )
            })}
          </AnimatePresence>

          {/* when */}
          <div className="rounded-2xl border border-lav-200 bg-surface p-4">
            <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="When to post">
              {WHEN.map(([v, l, I]) => (
                <button key={v} role="radio" aria-checked={when === v} onClick={() => setWhen(v)} data-testid={`when-${v}`}
                  className={cx('flex cursor-pointer items-center gap-2 rounded-xl border px-3 py-2 text-sm transition', when === v ? 'border-lav-600 bg-lav-100 font-medium text-ink' : 'border-lav-200 text-ink-2 hover:border-lav-400')}>
                  <I className="size-4" />{l}
                </button>
              ))}
              {editingId && <button role="radio" aria-checked={when === 'draft'} onClick={() => setWhen('draft')} data-testid="when-draft" className={cx('flex cursor-pointer items-center gap-2 rounded-xl border px-3 py-2 text-sm', when === 'draft' ? 'border-lav-600 bg-lav-100' : 'border-lav-200')}><PenLine className="size-4" />Draft</button>}
            </div>
            {when === 'custom' && (
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <Input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} className="w-auto" data-testid="composer-datetime" aria-label="Date and time" />
                <span className="text-xs text-muted">in each channel’s timezone ({[...new Set(selected.map((c) => c.timezone))].join(', ') || settings?.timezone})</span>
              </div>
            )}
            {when === 'queue' && <p className="mt-2 text-xs text-muted">Goes into the next free posting slot of each channel, respecting its daily limit.</p>}
            {when === 'prioritize' && <p className="mt-2 text-xs text-muted">Takes the very next slot; everything else in the queue shifts down one.</p>}
          </div>

          {allIssues.length > 0 && (
            <ul className="space-y-1.5" data-testid="composer-issues" aria-live="polite">
              {allIssues.map((i, k) => (
                <li key={k} data-testid={i.code?.startsWith('duplicate') ? 'composer-duplicate' : undefined} className={cx('flex items-start gap-2 rounded-xl px-3 py-2 text-[13px]', i.level === 'error' ? 'bg-rose-50 text-rose-800' : i.level === 'warning' ? 'bg-amber-50 text-amber-900' : 'bg-lav-50 text-ink-2')}>
                  {i.ch && <ChannelAvatar ch={i.ch} size={18} />}<span>{i.message}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* preview-first side pane (on by default, like Buffer) */}
        {showPreview && (
          <aside className="hidden border-l border-lav-200 bg-lav-50/60 lg:block" aria-label="Previews">
            <div className="sticky top-0 p-5">
              <div className="mb-3 flex items-center gap-2">
                <Eye className="size-4 text-lav-600" /><span className="text-sm font-semibold">Preview</span>
                <span className="text-xs text-muted">{selected.length ? `${selected.length} channel${selected.length > 1 ? 's' : ''}` : ''}</span>
              </div>
              <div className="flex flex-col items-center gap-6 pb-2" data-testid="composer-previews">
                {selected.length ? selected.map((c) => <Preview key={c.id} ch={c} caption={captionFor(c)} media={media} options={optionsFor(c)} />)
                  : <div className="grid h-72 w-full place-items-center rounded-2xl border border-dashed border-lav-300 text-sm text-muted"><span className="flex items-center gap-2"><Sparkles className="size-4" />Pick a channel to preview</span></div>}
              </div>
              {fcChannels.length > 0 && shared(fcChannels, 'first_comment') && <p className="mt-3 rounded-xl bg-surface p-3 text-xs text-ink-2 shadow-soft" data-testid="preview-first-comment"><b>First comment:</b> {shared(fcChannels, 'first_comment')}</p>}
            </div>
          </aside>
        )}
      </div>
      <UnsplashPicker open={unsplash} onClose={() => setUnsplash(false)} onPicked={(m) => setMedia((x) => [...x, m])} />
      <ImageEditor media={editImage} open={!!editImage} onClose={() => setEditImage(null)}
        onSaved={(n) => setMedia((x) => x.map((y) => (y.id === editImage.id ? n : y)))} />
    </Sheet>
  )
}
