// In-app image editor: crop to a network's ratio, zoom, rotate, flip, colours and text, then "Save as copy".
// The cropper is react-easy-crop (MIT); the pixels are produced on the server by POST /api/media/{id}/edit
// (action media.edit), so agents get exactly the same editor through REST / MCP / CLI.
import { FlipHorizontal2, FlipVertical2, Plus, RotateCcw, RotateCw, Trash2, Type, Undo2 } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import Cropper from 'react-easy-crop'
import { post } from '../api'
import { Button, cx, Dialog, Field, Input, Select, useToast } from '../ui'

const ASPECTS = [['original', 'Original'], ['1:1', '1:1 Square'], ['4:5', '4:5 Portrait'], ['9:16', '9:16 Story / Reel'], ['16:9', '16:9 Landscape'], ['1.91:1', '1.91:1 Link']]
const RATIO = { '1:1': 1, '4:5': 4 / 5, '9:16': 9 / 16, '16:9': 16 / 9, '1.91:1': 1.91 }
const POS = { top: 0.12, middle: 0.5, bottom: 0.88 }
const NEW_TEXT = { text: '', pos: 'bottom', size: 0.08, color: '#ffffff', background: false }
const FRESH = { aspect: 'original', zoom: 1, rotation: 0, flipH: false, flipV: false, brightness: 1, contrast: 1, saturation: 1, texts: [], width: '' }

function Slider({ label, value, min, max, step, onChange, testid, fmt = (v) => v }) {
  return (
    <label className="block text-xs text-ink-2">
      <span className="flex justify-between"><span>{label}</span><span className="tabular-nums">{fmt(value)}</span></span>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} className="w-full accent-lav-600" data-testid={testid} aria-label={label} />
    </label>
  )
}

export default function ImageEditor({ media, open, onClose, onSaved }) {
  const toast = useToast()
  const [s, setS] = useState(FRESH)
  const [crop, setCrop] = useState({ x: 0, y: 0 })
  const [pixels, setPixels] = useState(null)
  const [cropSize, setCropSize] = useState(null)
  const [alt, setAlt] = useState('')
  const [saving, setSaving] = useState(false)
  const [preview, setPreview] = useState(null)
  const set = (patch) => { setS((x) => ({ ...x, ...patch })); setPreview(null) }

  useEffect(() => { if (open) { setS(FRESH); setCrop({ x: 0, y: 0 }); setAlt(media?.alt_text || ''); setPreview(null) } }, [open, media?.id])

  // react-easy-crop always needs a ratio: "Original" is the (rotated) image's own ratio
  const quarter = Math.round(s.rotation / 90) % 2 !== 0
  const natural = media?.width && media?.height ? (quarter ? media.height / media.width : media.width / media.height) : 1
  const aspect = RATIO[s.aspect] || natural
  const onCropComplete = useCallback((_area, px) => setPixels(px), [])

  const ops = useMemo(() => {
    const o = { rotate: s.rotation, flip_h: s.flipH, flip_v: s.flipV, brightness: s.brightness, contrast: s.contrast, saturation: s.saturation }
    if (pixels) o.crop = pixels
    if (Number(s.width) > 0) o.resize = { width: Number(s.width) }
    o.texts = s.texts.filter((t) => t.text.trim()).map((t) => ({ text: t.text, x: 0.5, y: POS[t.pos], size: t.size, color: t.color, background: t.background ? '#000000a6' : '' }))
    return o
  }, [s, pixels])

  const doPreview = async () => {
    try { setPreview(await post(`/api/media/${media.id}/edit`, { ...ops, preview: true })) } catch (e) { toast(e.message, 'err') }
  }
  const save = async () => {
    setSaving(true)
    try {
      const r = await post(`/api/media/${media.id}/edit`, { ...ops, alt_text: alt })
      toast('Saved an edited copy to your Library')
      onSaved?.(r.media)
      onClose()
    } catch (e) { toast(e.message, 'err') } finally { setSaving(false) }
  }
  const setText = (i, patch) => set({ texts: s.texts.map((t, j) => (j === i ? { ...t, ...patch } : t)) })
  const filter = `brightness(${s.brightness}) contrast(${s.contrast}) saturate(${s.saturation})`
  const sx = s.flipH ? -1 : 1
  const sy = s.flipV ? -1 : 1

  if (!media) return null
  return (
    <Dialog open={open} onClose={onClose} title={`Edit ${media.filename}`} wide testid="image-editor"
      footer={<>
        <span className="mr-auto text-xs text-muted">Saves a new copy. The original and posts that use it stay as they are.</span>
        <Button variant="ghost" onClick={() => set(FRESH)} data-testid="image-editor-reset"><Undo2 className="size-4" />Reset</Button>
        <Button variant="outline" onClick={doPreview} data-testid="image-editor-preview">Preview</Button>
        <Button variant="primary" loading={saving} onClick={save} data-testid="image-editor-save">Save as copy</Button>
      </>}>
      <div className="grid gap-5 lg:grid-cols-[1fr_320px]">
        <div className="space-y-3">
          <div className="relative h-[52vh] min-h-72 overflow-hidden rounded-2xl bg-night" data-testid="image-editor-canvas">
            {preview ? <img src={preview.preview} alt="Edited preview" className="size-full object-contain" data-testid="image-editor-preview-img" /> : (
              <>
                <Cropper image={media.url} crop={crop} zoom={s.zoom} rotation={s.rotation} aspect={aspect}
                  onCropChange={setCrop} onZoomChange={(z) => set({ zoom: z })} onCropComplete={onCropComplete} onCropSizeChange={setCropSize}
                  transform={`translate(${crop.x}px, ${crop.y}px) rotate(${s.rotation}deg) scale(${s.zoom * sx}, ${s.zoom * sy})`}
                  style={{ mediaStyle: { filter } }} />
                {cropSize && s.texts.length > 0 && (
                  <div className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2" style={{ width: cropSize.width, height: cropSize.height }}>
                    {s.texts.filter((t) => t.text.trim()).map((t, i) => (
                      <span key={i} className={cx('absolute left-1/2 -translate-x-1/2 -translate-y-1/2 whitespace-pre text-center font-bold leading-tight', t.background && 'rounded-md bg-black/65 px-2')}
                        style={{ top: `${POS[t.pos] * 100}%`, fontSize: t.size * cropSize.height, color: t.color, textShadow: t.background ? 'none' : '0 0 3px #000, 0 0 3px #000' }}>{t.text}</span>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
          {preview && <p className="text-xs text-muted" data-testid="image-editor-size">Result: {preview.width} × {preview.height}px. Change anything to keep editing.</p>}
          {!preview && <Slider label="Zoom" value={s.zoom} min={1} max={4} step={0.01} onChange={(v) => set({ zoom: v })} testid="image-editor-zoom" fmt={(v) => `${v.toFixed(2)}×`} />}
        </div>

        <div className="space-y-4">
          <Field label="Crop">
            <div className="flex flex-wrap gap-1.5" data-testid="image-editor-aspects">
              {ASPECTS.map(([k, l]) => <Button key={k} size="sm" variant={s.aspect === k ? 'primary' : 'outline'} onClick={() => set({ aspect: k })} data-testid={`image-editor-aspect-${k}`}>{l}</Button>)}
            </div>
          </Field>
          <Field label="Rotate and flip">
            <div className="flex flex-wrap gap-1.5">
              <Button size="sm" variant="outline" onClick={() => set({ rotation: (s.rotation + 270) % 360 })} aria-label="Rotate left" data-testid="image-editor-rotate-left"><RotateCcw className="size-4" /></Button>
              <Button size="sm" variant="outline" onClick={() => set({ rotation: (s.rotation + 90) % 360 })} aria-label="Rotate right" data-testid="image-editor-rotate-right"><RotateCw className="size-4" /></Button>
              <Button size="sm" variant={s.flipH ? 'primary' : 'outline'} onClick={() => set({ flipH: !s.flipH })} aria-label="Flip horizontally" data-testid="image-editor-flip-h"><FlipHorizontal2 className="size-4" /></Button>
              <Button size="sm" variant={s.flipV ? 'primary' : 'outline'} onClick={() => set({ flipV: !s.flipV })} aria-label="Flip vertically" data-testid="image-editor-flip-v"><FlipVertical2 className="size-4" /></Button>
            </div>
            <Slider label="Straighten" value={((s.rotation + 180) % 360) - 180} min={-45} max={45} step={1} onChange={(v) => set({ rotation: (v + 360) % 360 })} testid="image-editor-straighten" fmt={(v) => `${v}°`} />
          </Field>
          <Field label="Adjust">
            <div className="space-y-2">
              <Slider label="Brightness" value={s.brightness} min={0.2} max={2} step={0.05} onChange={(v) => set({ brightness: v })} testid="image-editor-brightness" fmt={(v) => `${Math.round(v * 100)}%`} />
              <Slider label="Contrast" value={s.contrast} min={0.2} max={2} step={0.05} onChange={(v) => set({ contrast: v })} testid="image-editor-contrast" fmt={(v) => `${Math.round(v * 100)}%`} />
              <Slider label="Saturation" value={s.saturation} min={0} max={2} step={0.05} onChange={(v) => set({ saturation: v })} testid="image-editor-saturation" fmt={(v) => `${Math.round(v * 100)}%`} />
            </div>
          </Field>
          <Field label="Text">
            <div className="space-y-2" data-testid="image-editor-texts">
              {s.texts.map((t, i) => (
                <div key={i} className="space-y-1.5 rounded-xl border border-lav-100 p-2" data-testid="image-editor-text">
                  <div className="flex gap-1.5">
                    <Input value={t.text} placeholder="Your text" onChange={(e) => setText(i, { text: e.target.value })} data-testid="image-editor-text-input" aria-label="Overlay text" />
                    <Button size="icon" variant="ghost" onClick={() => set({ texts: s.texts.filter((_, j) => j !== i) })} aria-label="Remove text" data-testid="image-editor-text-remove"><Trash2 className="size-4" /></Button>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <Select value={t.pos} onChange={(e) => setText(i, { pos: e.target.value })} options={[['top', 'Top'], ['middle', 'Middle'], ['bottom', 'Bottom']]} data-testid="image-editor-text-pos" aria-label="Text position" />
                    <input type="color" value={t.color} onChange={(e) => setText(i, { color: e.target.value })} className="h-8 w-10 cursor-pointer rounded" data-testid="image-editor-text-color" aria-label="Text colour" />
                    <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={t.background} onChange={(e) => setText(i, { background: e.target.checked })} data-testid="image-editor-text-bg" aria-label="Dark box behind text" />Box</label>
                  </div>
                  <Slider label="Size" value={t.size} min={0.03} max={0.25} step={0.01} onChange={(v) => setText(i, { size: v })} testid="image-editor-text-size" fmt={(v) => `${Math.round(v * 100)}%`} />
                </div>
              ))}
              {s.texts.length < 5 && <Button size="sm" variant="outline" onClick={() => set({ texts: [...s.texts, { ...NEW_TEXT, pos: s.texts.length ? 'top' : 'bottom' }] })} data-testid="image-editor-add-text"><Type className="size-4" /><Plus className="size-3" />Add text</Button>}
            </div>
          </Field>
          <Field label="Resize width (px)" hint="Leave empty to keep the cropped size. Height follows the ratio.">
            <Input type="number" min={1} max={8192} value={s.width} onChange={(e) => set({ width: e.target.value })} placeholder={pixels ? String(pixels.width) : ''} data-testid="image-editor-width" aria-label="Resize width" />
          </Field>
          <Field label="Alt text">
            <Input value={alt} onChange={(e) => setAlt(e.target.value)} placeholder="Describe the image" data-testid="image-editor-alt" aria-label="Alt text" />
          </Field>
        </div>
      </div>
    </Dialog>
  )
}
