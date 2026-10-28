// Settings › AI model: bring your own provider and key (NVIDIA, Gemini, Groq, OpenRouter, Ollama, OpenAI, Anthropic or any
// OpenAI-compatible URL). The key is stored encrypted and never sent back to the browser.
import { useEffect, useState } from 'react'
import { CheckCircle2, ExternalLink, KeyRound, ListRestart, PlugZap, Sparkles, XCircle } from 'lucide-react'
import { get, put, post } from '../api'
import { Badge, Button, Card, Field, Input, Select, useToast } from '../ui'

export function AiSettings() {
  const toast = useToast()
  const [st, setSt] = useState(null)
  const [f, setF] = useState({ provider: '', base_url: '', model: '', max_tokens: 2048, api_key: '' })
  const [models, setModels] = useState(null)
  const [test, setTest] = useState(null)
  const [busy, setBusy] = useState('')
  const load = () => get('/api/ai/settings').then((r) => { setSt(r); setF({ provider: r.provider, base_url: r.base_url, model: r.model, max_tokens: r.max_tokens, api_key: '' }) })
  useEffect(() => { load() }, [])
  if (!st) return null
  const preset = st.providers[f.provider] || {}
  const keyless = ['ollama', ''].includes(f.provider)
  const save = async (extra = {}) => {
    setBusy('save')
    try {
      const body = { provider: f.provider, base_url: f.base_url.trim(), model: f.model.trim(), max_tokens: Number(f.max_tokens) || 2048, ...extra }
      if (f.api_key.trim()) body.api_key = f.api_key.trim()
      const r = await put('/api/ai/settings', body)
      setSt(r); setF((x) => ({ ...x, api_key: '' })); setTest(null); toast(extra.clear_key ? 'Key removed' : 'AI model saved')
      return r
    } catch (e) { toast(e.message, 'err') } finally { setBusy('') }
  }
  const runTest = async () => {
    if (f.api_key.trim() || f.provider !== st.provider || f.model !== st.model || f.base_url !== st.base_url) { if (!(await save())) return }
    setBusy('test'); setTest(null)
    try { setTest(await post('/api/ai/test')) } catch (e) { setTest({ ok: false, error: e.message }) } finally { setBusy('') }
  }
  const loadModels = async () => {
    setBusy('models')
    try { const r = await get('/api/ai/models'); setModels(r); if (r.error) toast(r.error, 'err') } catch (e) { toast(e.message, 'err') } finally { setBusy('') }
  }
  return (
    <Card className="space-y-5 p-6" data-testid="settings-ai">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="flex items-center gap-2 font-semibold"><Sparkles className="size-4 text-lav-600" />Your own AI model</h2>
          <p className="text-sm text-muted">Plug in any model you like. It powers the caption writer, post ideas, alt text, comment themes, reply suggestions and the command bar. No credits and no limits beyond your provider's.</p>
        </div>
        {st.configured ? <Badge tone="green" data-testid="ai-status">Connected · {st.model || preset.model}</Badge> : <Badge tone="gray" data-testid="ai-status">Not set up</Badge>}
      </div>
      {st.from_env && <p className="rounded-xl bg-lav-50 p-3 text-sm" data-testid="ai-from-env">Using <code>HUHQ_AI_*</code> from <code>.env</code>. Saving here overrides it.</p>}
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="Provider">
          <Select className="w-full" value={f.provider} data-testid="ai-provider"
            onChange={(e) => { setF({ ...f, provider: e.target.value, base_url: '', model: '' }); setModels(null); setTest(null) }}
            options={[['', 'None (AI off)'], ...Object.entries(st.providers).map(([k, p]) => [k, p.label])]} />
        </Field>
        <Field label="API key" hint={st.has_key && f.provider === st.provider ? 'A key is saved. Type a new one to replace it.' : keyless ? 'Not needed for local models.' : ''}>
          <div className="flex gap-2">
            <Input type="password" autoComplete="off" value={f.api_key} onChange={(e) => setF({ ...f, api_key: e.target.value })} disabled={!f.provider}
              placeholder={st.has_key && f.provider === st.provider ? '•••••••• saved' : f.provider === 'nim' ? 'nvapi-…' : f.provider === 'gemini' ? 'AIza…' : 'sk-…'} data-testid="ai-key" />
            {preset.key_url && <a href={preset.key_url} target="_blank" rel="noreferrer" data-testid="ai-key-link" className="inline-flex shrink-0 items-center gap-1 rounded-xl border border-lav-200 px-3 text-[13px] text-ink-2 hover:border-lav-400"><KeyRound className="size-3.5 text-lav-600" />Get a key<ExternalLink className="size-3 text-muted" /></a>}
          </div>
        </Field>
        <Field label="Model" hint={`Default: ${preset.model || '-'}. Any model id your provider serves.`}>
          <div className="flex gap-2">
            <Input list="ai-model-list" value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })} placeholder={preset.model || 'model id'} disabled={!f.provider} data-testid="ai-model" />
            <Button variant="outline" size="md" onClick={loadModels} loading={busy === 'models'} disabled={!st.configured || f.provider !== st.provider} title="Load the models your provider offers" data-testid="ai-models-load"><ListRestart className="size-4" />Models</Button>
          </div>
          <datalist id="ai-model-list">{(models?.models || []).map((m) => <option key={m} value={m} />)}</datalist>
          {models?.models?.length > 0 && <p className="mt-1 text-xs text-muted" data-testid="ai-models-count">{models.models.length} models available - start typing to pick one.</p>}
        </Field>
        <Field label="Base URL" hint="Leave empty for the provider's default. Use it for LM Studio, vLLM, a proxy or any OpenAI-compatible server.">
          <Input value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })} placeholder={preset.base_url || 'https://…/v1'} disabled={!f.provider} data-testid="ai-base-url" />
        </Field>
        <Field label="Max output tokens" hint="Raise it for thinking models (Nemotron, DeepSeek-R1, Qwen3), which reason before answering.">
          <Input type="number" min={64} max={32768} value={f.max_tokens} onChange={(e) => setF({ ...f, max_tokens: e.target.value })} disabled={!f.provider} data-testid="ai-max-tokens" />
        </Field>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" onClick={() => save()} loading={busy === 'save'} data-testid="ai-save">Save</Button>
        <Button variant="outline" onClick={runTest} loading={busy === 'test'} disabled={!f.provider} data-testid="ai-test"><PlugZap className="size-4" />Test connection</Button>
        {st.has_key && <Button variant="danger-ghost" onClick={() => save({ clear_key: true })} data-testid="ai-clear-key">Remove key</Button>}
      </div>
      {test && (test.ok
        ? <p className="flex items-start gap-2 rounded-xl bg-emerald-50 p-3 text-sm text-emerald-800" data-testid="ai-test-result"><CheckCircle2 className="mt-0.5 size-4 shrink-0" /><span><b>{test.model}</b> answered “{test.reply}” in {(test.ms / 1000).toFixed(1)} s.</span></p>
        : <p className="flex items-start gap-2 rounded-xl bg-rose-50 p-3 text-sm text-rose-800" data-testid="ai-test-result"><XCircle className="mt-0.5 size-4 shrink-0" /><span>{test.error}</span></p>)}
      <details className="rounded-xl bg-lav-50 p-4 text-sm" data-testid="ai-headless-help">
        <summary className="cursor-pointer font-medium">Set it up without the UI (server, Docker, CLI)</summary>
        <p className="mt-2 text-ink-2">Put these in <code>.env</code> (used when nothing is saved here):</p>
        <pre className="mt-2 overflow-auto rounded-lg bg-[#141020] p-3 text-[11px] text-[#e6d9fe]">{'HUHQ_AI_PROVIDER=nim\nHUHQ_AI_MODEL=nvidia/nemotron-3-ultra-550b-a55b\nHUHQ_AI_API_KEY=nvapi-...\nHUHQ_AI_MAX_TOKENS=8192'}</pre>
        <p className="mt-2 text-ink-2">Or from a terminal on this machine: <code>huhqueue ai set --provider gemini --model gemini-2.5-flash</code> (asks for the key), then <code>huhqueue ai test</code>. Agents can check the setup with the <code>ai_status</code> MCP tool; keys are never readable by agents.</p>
      </details>
    </Card>
  )
}
