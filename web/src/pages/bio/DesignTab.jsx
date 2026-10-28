// Design tab: theme gallery (each tile drawn in its own font and colours), then Custom: background, buttons, font,
// colours. Every change is a draft shown live in the preview; the bar above has Undo / Redo / Cancel / Save.
import { Check, ImagePlus, RotateCcw, Shuffle } from 'lucide-react'
import { useRef, useState } from 'react'
import { Button, cx, Input, SkeletonList, useToast } from '../../ui'
import { Panel, uploadSquare } from './shared'

const RADIUS = { pill: '999px', rounded: '12px', square: '4px' }
const SWATCHES = ['#121212', '#FFFFFF', '#C9ACFA', '#7C5CE0', '#2457F5', '#0F7B55', '#FFE14D', '#FF5A1F']
const STYLE_LABEL = { solid: 'Fill', outline: 'Outline', soft: 'Soft', shadow: 'Shadow', hard: 'Hard shadow' }

// Picking a preset resets the custom overrides and applies the preset's font, button shape and style.
export const presetPatch = (t) => ({ preset: t.key, font: '', buttons: t.buttons, style: t.style, accent: '#7C5CE0', bg_type: '', bg_color: '',
  bg_gradient: '', bg_image: '', text_color: '', button_color: '', button_text_color: '' })

export function sampleButton({ button, button_ink: ink, border, page_ink: pageInk }, style, shape) {
  const base = { background: button, color: ink, border: `1.5px solid ${border}`, borderRadius: RADIUS[shape] }
  if (style === 'outline') return { ...base, background: 'transparent', color: pageInk, borderColor: pageInk }
  if (style === 'soft') return { ...base, background: `${pageInk}14`, color: pageInk, borderColor: 'transparent' }
  if (style === 'shadow') return { ...base, borderColor: 'transparent', boxShadow: '0 8px 18px -8px rgba(0,0,0,.45)' }
  if (style === 'hard') return { ...base, border: `2px solid ${pageInk}`, boxShadow: `3px 3px 0 ${pageInk}` }
  return base
}

export function DesignTab({ theme, setDraft, catalog, shuffle }) {
  if (!catalog) return <SkeletonList rows={3} />
  const fonts = Object.fromEntries(catalog.fonts.map((f) => [f.key, f]))
  const preset = catalog.presets.find((t) => t.key === theme.preset) || catalog.presets[0]
  const eff = { button: theme.button_color || preset.button, button_ink: theme.button_text_color || (theme.button_color ? '#121212' : preset.button_ink),
    border: preset.border, page_ink: theme.text_color || preset.ink }
  const custom = theme.bg_type || theme.font || theme.text_color || theme.button_color || theme.button_text_color
  return (
    <div className="space-y-4" data-testid="bio-design">
      <Panel title="Theme" testid="bio-themes"
        action={<Button size="sm" variant="outline" className="rounded-full" onClick={shuffle} data-testid="bio-design-shuffle"><Shuffle className="size-3.5" />Shuffle</Button>}>
        <div className="grid grid-cols-3 gap-3 sm:grid-cols-4">
          {catalog.presets.map((t) => {
            const on = theme.preset === t.key && !custom
            const f = fonts[t.font]
            return (
              <button key={t.key} type="button" onClick={() => setDraft(presetPatch(t))} aria-pressed={on} data-testid={`bio-theme-${t.key}`} title={t.label}
                className="group cursor-pointer text-center">
                <span className={cx('relative flex aspect-[4/5] flex-col justify-between overflow-hidden rounded-2xl p-2.5 text-left ring-offset-2 ring-offset-surface transition group-hover:-translate-y-0.5',
                  on ? 'ring-[3px] ring-ink' : 'ring-1 ring-lav-200 group-hover:ring-lav-400')} style={{ background: t.bg }}>
                  <span className="text-2xl leading-none" style={{ fontFamily: f?.family, color: t.ink, fontWeight: 700 }}>Aa</span>
                  <span className="block h-5 w-full" style={sampleButton({ ...t, page_ink: t.ink }, t.style, t.buttons)} />
                  {on && <span className="absolute right-1.5 top-1.5 grid size-5 place-items-center rounded-full bg-ink text-surface"><Check className="size-3" /></span>}
                </span>
                <span className="mt-1.5 block text-xs font-medium">{t.label}</span>
              </button>
            )
          })}
        </div>
      </Panel>

      <Background theme={theme} setDraft={setDraft} catalog={catalog} />

      <Panel title="Buttons" testid="bio-buttons">
        <div className="grid grid-cols-[80px_repeat(3,1fr)] items-center gap-2 text-xs">
          <span />{['square', 'rounded', 'pill'].map((sh) => <span key={sh} className="text-center font-medium capitalize text-muted">{sh}</span>)}
          {catalog.styles.map((st) => [
            <span key={st} className="font-medium text-ink-2">{STYLE_LABEL[st] || st}</span>,
            ...['square', 'rounded', 'pill'].map((sh) => {
              const on = theme.style === st && theme.buttons === sh
              return (
                <button key={`${st}-${sh}`} type="button" onClick={() => setDraft({ style: st, buttons: sh })} aria-pressed={on} aria-label={`${STYLE_LABEL[st]} ${sh} buttons`}
                  data-testid={`bio-btn-${st}-${sh}`} className={cx('cursor-pointer rounded-xl p-2 transition', on ? 'bg-lav-100 ring-2 ring-ink' : 'hover:bg-lav-50')}
                  style={{ background: on ? undefined : 'transparent' }}>
                  <span className="mx-auto block h-7 w-full max-w-24" style={sampleButton(eff, st, sh)} />
                </button>
              )
            }),
          ])}
        </div>
      </Panel>

      <Panel title="Font" testid="bio-fonts">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {catalog.fonts.map((f) => {
            const on = (theme.font || preset.font) === f.key
            return (
              <button key={f.key} type="button" onClick={() => setDraft({ font: f.key })} aria-pressed={on} data-testid={`bio-font-${f.key}`}
                className={cx('cursor-pointer rounded-2xl border-2 px-3 py-3 text-left transition', on ? 'border-ink bg-lav-50' : 'border-lav-200 hover:border-lav-400')}>
                <span className="block text-2xl leading-tight" style={{ fontFamily: f.family, fontWeight: 700 }}>Aa</span>
                <span className="block truncate text-[13px]" style={{ fontFamily: f.family }}>{f.label}</span>
              </button>
            )
          })}
        </div>
      </Panel>

      <Panel title="Colours" testid="bio-colors">
        <div className="space-y-4">
          <ColorField label="Accent" hint="Highlighted links, signup buttons, avatar ring" value={theme.accent === '#7C5CE0' ? '' : theme.accent} fallback={preset.accent}
            onChange={(v) => setDraft({ accent: v || '#7C5CE0' })} testid="bio-accent" />
          <ColorField label="Page text" value={theme.text_color} fallback={preset.ink} onChange={(v) => setDraft({ text_color: v })} testid="bio-color-text" />
          <ColorField label="Button" value={theme.button_color} fallback={preset.button} onChange={(v) => setDraft({ button_color: v })} testid="bio-color-button" />
          <ColorField label="Button text" value={theme.button_text_color} fallback={preset.button_ink} onChange={(v) => setDraft({ button_text_color: v })} testid="bio-color-button-text" />
        </div>
      </Panel>
      <p className="px-1 text-[11px] text-muted">{catalog.credit}</p>
    </div>
  )
}

function Background({ theme, setDraft, catalog }) {
  const toast = useToast()
  const file = useRef(null)
  const [busy, setBusy] = useState(false)
  const types = [['', 'Theme'], ['solid', 'Solid'], ['gradient', 'Gradient'], ['image', 'Image']]
  const pickType = (k) => {
    if (k === 'solid') setDraft({ bg_type: k, bg_color: theme.bg_color || '#F6F1FF' })
    else if (k === 'gradient') setDraft({ bg_type: k, bg_gradient: theme.bg_gradient || catalog.gradients[0].key })
    else if (k === 'image' && !theme.bg_image) file.current?.click()
    else setDraft({ bg_type: k })
  }
  const pick = async (e) => {
    const f = e.target.files?.[0]
    e.target.value = ''
    if (!f) return
    setBusy(true)
    try { setDraft({ bg_type: 'image', bg_image: await uploadSquare(f, '') }) } catch (err) { toast(err.message, 'err') } finally { setBusy(false) }
  }
  return (
    <Panel title="Background" testid="bio-background">
      <div className="grid grid-cols-4 gap-2">
        {types.map(([k, l]) => (
          <button key={k || 'theme'} type="button" onClick={() => pickType(k)} aria-pressed={theme.bg_type === k} data-testid={`bio-bg-${k || 'theme'}`}
            className={cx('cursor-pointer rounded-2xl border-2 p-2 text-center text-xs font-medium transition', theme.bg_type === k ? 'border-ink' : 'border-lav-200 hover:border-lav-400')}>
            <span className={cx('mb-1.5 block h-12 rounded-xl', busy && k === 'image' && 'animate-pulse')} style={{
              background: k === 'solid' ? (theme.bg_color || '#F6F1FF') : k === 'gradient' ? (catalog.gradients.find((g) => g.key === theme.bg_gradient) || catalog.gradients[0]).css
                : k === 'image' ? (theme.bg_image ? `center/cover url("${theme.bg_image}")` : 'repeating-linear-gradient(45deg,#e6d9fe 0 6px,#f3ecff 6px 12px)')
                  : (catalog.presets.find((t) => t.key === theme.preset)?.bg || '#fff'),
            }} />{l}
          </button>
        ))}
      </div>
      <input ref={file} type="file" accept="image/*" className="hidden" onChange={pick} aria-label="Background image file" data-testid="bio-bg-file" />
      {theme.bg_type === 'solid' && <div className="mt-4"><ColorField label="Background colour" value={theme.bg_color} fallback="#F6F1FF" onChange={(v) => setDraft({ bg_color: v || '#F6F1FF' })} testid="bio-color-bg" /></div>}
      {theme.bg_type === 'gradient' && (
        <div className="mt-4 grid grid-cols-6 gap-2" data-testid="bio-gradients">
          {catalog.gradients.map((g) => (
            <button key={g.key} type="button" onClick={() => setDraft({ bg_gradient: g.key })} aria-pressed={theme.bg_gradient === g.key} aria-label={g.label} title={g.label}
              data-testid={`bio-gradient-${g.key}`} className={cx('aspect-square cursor-pointer rounded-full ring-offset-2 ring-offset-surface transition hover:scale-105', theme.bg_gradient === g.key && 'ring-[3px] ring-ink')}
              style={{ background: g.css }} />
          ))}
        </div>
      )}
      {theme.bg_type === 'image' && (
        <div className="mt-4 flex gap-2">
          <Input value={theme.bg_image} onChange={(e) => setDraft({ bg_image: e.target.value })} placeholder="https://…" aria-label="Background image URL" data-testid="bio-bg-image" />
          <Button size="md" onClick={() => file.current?.click()} data-testid="bio-bg-upload"><ImagePlus className="size-4" />Upload</Button>
        </div>
      )}
    </Panel>
  )
}

function ColorField({ label, hint, value, fallback, onChange, testid }) {
  const [text, setText] = useState(null)
  const shown = value || fallback || '#FFFFFF'
  const commit = (v) => { const x = v.trim().toUpperCase(); if (/^#[0-9A-F]{6}$/.test(x)) onChange(x); setText(null) }
  return (
    <div className="flex flex-wrap items-center gap-3">
      <div className="w-32"><div className="text-sm font-medium">{label}</div>{hint && <div className="text-[11px] leading-tight text-muted">{hint}</div>}</div>
      <label className="relative size-10 shrink-0 cursor-pointer overflow-hidden rounded-full ring-1 ring-lav-300" style={{ background: shown }}>
        <input type="color" value={shown.toLowerCase()} onChange={(e) => onChange(e.target.value.toUpperCase())} className="absolute inset-0 size-full cursor-pointer opacity-0" aria-label={`${label} colour`} data-testid={testid} />
      </label>
      <Input value={text ?? (value || '')} placeholder={fallback} onChange={(e) => setText(e.target.value)} onBlur={(e) => commit(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && commit(e.currentTarget.value)} className="h-9 w-28 font-mono text-xs uppercase" aria-label={`${label} hex`} data-testid={`${testid}-hex`} />
      <div className="flex gap-1.5">
        {SWATCHES.map((c) => <button key={c} type="button" onClick={() => onChange(c)} aria-label={`${label} ${c}`} data-testid={`${testid}-swatch`} className={cx('size-6 cursor-pointer rounded-full ring-1 ring-lav-300 transition hover:scale-110', value === c && 'ring-2 ring-ink ring-offset-1')} style={{ background: c }} />)}
      </div>
      {value && <Button size="sm" variant="ghost" onClick={() => onChange('')} data-testid={`${testid}-reset`}><RotateCcw className="size-3.5" />Theme</Button>}
    </div>
  )
}
