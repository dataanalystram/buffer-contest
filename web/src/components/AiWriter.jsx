// Composer › AI: write, rephrase, shorten, expand, hook, hashtags or fix a caption with your own model (Settings › AI model).
import { useState } from 'react'
import { Sparkles, Wand2 } from 'lucide-react'
import { post } from '../api'
import { Button, Dialog, Field, Select, Textarea, useToast } from '../ui'

const MODES = [['write', 'Write a post'], ['rephrase', 'Rephrase'], ['shorten', 'Shorten'], ['expand', 'Expand'], ['hook', 'Better hook'], ['hashtags', 'Add hashtags'], ['fix', 'Fix grammar']]
const TONES = [['friendly', 'Friendly'], ['professional', 'Professional'], ['playful', 'Playful'], ['bold', 'Bold'], ['informative', 'Informative']]

export function AiWriter({ caption, onUse, providers = [] }) {
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [f, setF] = useState({ mode: 'write', tone: 'friendly', text: '' })
  const [res, setRes] = useState(null)
  const [busy, setBusy] = useState(false)
  const show = () => { setF({ ...f, mode: caption.trim() ? 'rephrase' : 'write', text: caption.trim() ? caption : '' }); setRes(null); setOpen(true) }
  const run = async () => {
    setBusy(true)
    try { setRes(await post('/api/ai/write', { ...f, providers })) } catch (e) {
      toast(e.status === 400 && /Settings/.test(e.message) ? 'Add your AI model in Settings › AI model first' : e.message, 'err')
    } finally { setBusy(false) }
  }
  return (
    <>
      <Button size="sm" variant="ghost" onClick={show} data-testid="composer-ai-writer" title="Write with AI"><Wand2 className="size-4 text-lav-600" /><span className="hidden xl:inline">AI</span></Button>
      <Dialog open={open} onClose={() => setOpen(false)} title="AI writer" testid="ai-writer-dialog" wide
        footer={<Button variant="primary" onClick={run} loading={busy} disabled={!f.text.trim()} data-testid="ai-writer-run"><Sparkles className="size-4" />{res ? 'Try again' : 'Generate'}</Button>}>
        <div className="space-y-4">
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="What to do"><Select className="w-full" value={f.mode} onChange={(e) => setF({ ...f, mode: e.target.value })} options={MODES} data-testid="ai-writer-mode" /></Field>
            <Field label="Tone"><Select className="w-full" value={f.tone} onChange={(e) => setF({ ...f, tone: e.target.value })} options={TONES} data-testid="ai-writer-tone" /></Field>
          </div>
          <Field label={f.mode === 'write' ? 'What is the post about?' : 'Draft'}>
            <Textarea rows={4} value={f.text} onChange={(e) => setF({ ...f, text: e.target.value })} placeholder="e.g. we just shipped scheduled Shorts for YouTube" data-testid="ai-writer-text" />
          </Field>
          {res && <div className="space-y-2" data-testid="ai-writer-results">{res.options.map((o, i) => (
            <div key={i} className="rounded-2xl border border-lav-200 p-3" data-testid="ai-writer-option">
              <p className="whitespace-pre-wrap text-sm">{o}</p>
              <div className="mt-2 flex items-center gap-2"><span className="text-xs text-muted">{o.length}/{res.limit}</span>
                <Button size="sm" variant="soft" className="ml-auto" onClick={() => { onUse(o); setOpen(false); toast('Caption replaced') }} data-testid="ai-writer-use">Use this</Button></div>
            </div>
          ))}</div>}
        </div>
      </Dialog>
    </>
  )
}
