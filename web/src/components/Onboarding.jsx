// Post-connect onboarding for every network: confirm the account -> weekly posting goal -> recommended times,
// saved as the channel's posting schedule (queue slots + calendar "+ New" cells). Every step is a registry action:
// onboarding.get / onboarding.recommend / onboarding.finish.
import { motion } from 'motion/react'
import { ArrowLeftRight, ArrowRight, Check, CircleHelp, Pencil, RefreshCw, Sparkles } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { get, post } from '../api'
import { go } from '../router'
import { useStore } from '../store'
import { Button, ChannelAvatar, cx, Input, NetIcon, NETWORKS, useToast } from '../ui'

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const TONES = { 1: 'bg-lav-100 text-lav-700', 3: 'bg-amber-100 text-amber-700', 5: 'bg-emerald-100 text-emerald-700' }
const ampm = (hhmm) => { const [h, m] = hhmm.split(':').map(Number); return `${((h + 11) % 12) + 1}:${String(m).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}` }

function Confetti() {
  const bits = useMemo(() => Array.from({ length: 26 }, (_, i) => ({ x: (i * 37) % 100, d: (i % 5) * 0.12, c: ['#C9ACFA', '#F472B6', '#34D399', '#FBBF24', '#60A5FA'][i % 5], r: (i * 47) % 360 })), [])
  return (
    <div className="pointer-events-none absolute inset-x-0 top-0 h-40 overflow-hidden" aria-hidden="true">
      {bits.map((b, i) => (
        <motion.span key={i} className="absolute top-0 h-1.5 w-3 rounded-full" style={{ left: `${b.x}%`, background: b.c, rotate: b.r }}
          initial={{ y: -20, opacity: 0 }} animate={{ y: [0, 90 + (i % 4) * 18], opacity: [0, 1, 0] }} transition={{ duration: 1.8, delay: b.d, ease: 'easeOut' }} />
      ))}
    </div>
  )
}

function Hero({ ch, check }) {
  return (
    <div className="relative mx-auto w-fit">
      <ChannelAvatar ch={ch} size={72} className="rounded-2xl shadow-soft" />
      {check && <span className="absolute -left-2 -top-2 grid size-7 place-items-center rounded-full bg-emerald-500 text-white ring-4 ring-surface"><Check className="size-4" /></span>}
      <span className="absolute -bottom-2 -right-2 rounded-lg ring-4 ring-surface"><NetIcon provider={ch.provider} size="md" /></span>
    </div>
  )
}

export default function Onboarding({ cid, onClose }) {
  const toast = useToast()
  const { reload } = useStore()
  const [data, setData] = useState(null)
  const [step, setStep] = useState('confirm')
  const [goal, setGoal] = useState(3)
  const [custom, setCustom] = useState('')
  const [help, setHelp] = useState(false)
  const [rec, setRec] = useState(null)
  const [off, setOff] = useState({})
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (!cid || cid === 'undefined') return; get(`/api/channels/${cid}/onboarding`).then((d) => { setData(d); if (d.goal) setGoal(d.goal) }).catch((e) => toast(e.message, 'err')) }, [cid])
  const ch = data?.channel
  const net = ch ? NETWORKS[ch.provider]?.name || ch.network : ''
  const goalValue = goal === 'custom' ? Math.max(1, Math.min(21, Number(custom) || 0)) : goal
  const next = async () => {
    setBusy(true)
    try { setRec(await post(`/api/channels/${cid}/onboarding/recommend`, { goal: goalValue })); setOff({}); setStep('times') } catch (e) { toast(e.message, 'err') } finally { setBusy(false) }
  }
  const finish = async (thenCustomize) => {
    setBusy(true)
    try {
      const times = rec.times.map((t, i) => ({ ...t, enabled: !off[i] }))
      const r = await post(`/api/channels/${cid}/onboarding/finish`, { goal: goalValue, times })
      toast(`Posting schedule saved: ${r.slots.length} times a week`)
      reload?.()
      onClose()
      go(`/channel/${cid}`, thenCustomize ? { tab: 'settings', section: 'schedule' } : { tab: 'publish' })
    } catch (e) { toast(e.message, 'err') } finally { setBusy(false) }
  }
  const switchAccount = async () => {
    try { const r = await post(`/api/connect/${ch.provider}`); if (r.url) window.location.href = r.url; else go('/channels') } catch (e) { toast(e.message, 'err') }
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center overflow-y-auto bg-night/45 p-4 pt-[7vh] backdrop-blur-sm" data-testid="onboarding">
      <motion.div role="dialog" aria-modal="true" aria-label="Set up your channel" initial={{ opacity: 0, y: 20, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }}
        className="relative w-full max-w-2xl overflow-hidden rounded-3xl border border-lav-200 bg-surface shadow-pop">
        <button className="absolute right-4 top-4 z-10 grid size-9 cursor-pointer place-items-center rounded-full border border-lav-200 text-ink-2 hover:bg-lav-50" aria-label="Close" data-testid="onboarding-close" onClick={onClose}>×</button>
        {!data ? <div className="p-16 text-center text-sm text-muted" data-testid="onboarding-loading">Loading…</div> : step === 'confirm' ? (
          <div data-testid="onboarding-step-confirm">
            <div className="flex items-center justify-between border-b border-lav-100 px-6 py-4">
              <button className="cursor-pointer text-sm text-ink-2 hover:text-ink" onClick={switchAccount} data-testid="onboarding-switch"><RefreshCw className="mr-1 inline size-3.5" />Switch account</button>
              <span className="flex items-center gap-2 pr-10" aria-label={`HuhQueue and ${net}`}><img src="/static/mark.png" alt="HuhQueue" className="size-7" /><ArrowLeftRight className="size-4 text-muted" /><NetIcon provider={ch.provider} size="md" /></span>
            </div>
            <div className="px-8 py-8">
              <h2 className="text-center text-2xl font-semibold">Confirm your account</h2>
              <p className="mt-1 text-center text-sm text-muted">This is the {net} {ch.type.toLowerCase()} HuhQueue will post to.</p>
              <div className="mx-auto mt-6 flex max-w-md items-center gap-4 rounded-2xl border-2 border-lav-400 bg-lav-50/60 p-4" role="radio" aria-checked="true" data-testid="onboarding-account-card">
                <ChannelAvatar ch={ch} size={52} />
                <div className="min-w-0 flex-1"><p className="truncate font-semibold">{ch.display_name}</p><p className="text-sm text-muted">{ch.type}{ch.username ? ` · @${ch.username}` : ''}{ch.followers_count != null ? ` · ${ch.followers_count} followers` : ''}</p></div>
                <span className="grid size-7 place-items-center rounded-full bg-emerald-500 text-white"><Check className="size-4" /></span>
              </div>
            </div>
            <div className="flex items-center justify-between border-t border-lav-100 px-6 py-4">
              <details className="text-sm text-ink-2" data-testid="onboarding-need-help"><summary className="cursor-pointer">Need help?</summary>
                <p className="mt-2 max-w-xs text-xs text-muted">Wrong account? Use Switch account. Connection or permission problems: see SETUP.md (redirect URIs and app keys) or Settings › App keys.</p></details>
              <Button variant="accent" onClick={() => setStep('goal')} data-testid="onboarding-finish-connection">Finish connection</Button>
            </div>
          </div>
        ) : step === 'goal' ? (
          <div data-testid="onboarding-step-goal">
            <Confetti />
            <div className="px-8 pb-6 pt-10">
              <Hero ch={ch} check />
              <h2 className="mt-6 text-center text-2xl font-semibold">How many times a week would you like to post?</h2>
              <p className="mt-1 text-center text-sm text-muted">Your posting goal tells us how many times to recommend each week.</p>
              <div className="mx-auto mt-6 max-w-lg space-y-2.5" role="radiogroup" aria-label="Posting goal">
                {data.goals.map((g) => (
                  <button key={g.value} role="radio" aria-checked={goal === g.value} onClick={() => setGoal(g.value)} data-testid={`onboarding-goal-${g.value}`}
                    className={cx('flex w-full cursor-pointer items-center gap-4 rounded-2xl border p-3 text-left transition', goal === g.value ? 'border-lav-500 bg-lav-50 ring-2 ring-lav-200' : 'border-lav-200 hover:border-lav-300')}>
                    <span className={cx('grid size-11 place-items-center rounded-xl text-lg font-semibold', TONES[g.value])}>{g.badge}</span>
                    <span className="flex-1 text-[15px]">{g.label} · <span className="text-muted">{g.value} time{g.value > 1 ? 's' : ''}/week</span></span>
                    <span className={cx('size-5 rounded-full border-2', goal === g.value ? 'border-lav-600 bg-lav-600 shadow-[inset_0_0_0_3px_var(--color-surface,#fff)]' : 'border-lav-300')} />
                  </button>
                ))}
                <div role="radio" aria-checked={goal === 'custom'} onClick={() => setGoal('custom')} data-testid="onboarding-goal-custom"
                  className={cx('flex w-full cursor-pointer items-center gap-4 rounded-2xl border p-3', goal === 'custom' ? 'border-lav-500 bg-lav-50 ring-2 ring-lav-200' : 'border-lav-200 hover:border-lav-300')}>
                  <span className="grid size-11 place-items-center rounded-xl bg-pink-100 text-pink-600"><Pencil className="size-4" /></span>
                  <span className="flex-1 text-[15px]">Choose your goal</span>
                  {goal === 'custom' && <Input type="number" min={1} max={21} value={custom} onChange={(e) => setCustom(e.target.value)} className="h-9 w-24" placeholder="per week" autoFocus data-testid="onboarding-goal-custom-input" aria-label="Posts per week" />}
                  <span className={cx('size-5 rounded-full border-2', goal === 'custom' ? 'border-lav-600 bg-lav-600' : 'border-lav-300')} />
                </div>
              </div>
              {help && <p className="mx-auto mt-4 max-w-lg rounded-xl bg-lav-50 p-3 text-xs text-ink-2" data-testid="onboarding-help-text">
                Recommended times come from your own posts' engagement once this channel has enough history (10+ sent posts). Until then we use Buffer's 2026 study of 52M+ posts for {net}. They become your posting slots: drop posts into the queue and they go out at these times. You can change them any time.</p>}
            </div>
            <div className="flex items-center justify-between border-t border-lav-100 px-6 py-4">
              <button className="flex cursor-pointer items-center gap-1.5 text-sm text-ink-2 hover:text-ink" onClick={() => setHelp(!help)} data-testid="onboarding-help"><CircleHelp className="size-4" />What's a recommended time?</button>
              <Button variant="accent" onClick={next} loading={busy} disabled={goal === 'custom' && !(Number(custom) > 0)} data-testid="onboarding-next">Next <ArrowRight className="size-4" /></Button>
            </div>
          </div>
        ) : (
          <div data-testid="onboarding-step-times">
            <div className="px-8 pb-6 pt-10">
              <Hero ch={ch} />
              <h2 className="mt-6 text-center text-2xl font-semibold">These are your recommended times to post</h2>
              <p className="mx-auto mt-1 max-w-md text-center text-sm text-muted">Customize them, block days and more from channel settings. Reach and engagement depend on more than timing.</p>
              <ul className="mx-auto mt-6 max-w-lg space-y-2.5">
                {rec.times.map((t, i) => (
                  <li key={i}>
                    <label className={cx('flex cursor-pointer items-center gap-3 rounded-2xl border px-4 py-3 transition', off[i] ? 'border-lav-100 text-muted' : 'border-lav-200 bg-surface')} data-testid="onboarding-time">
                      <input type="checkbox" className="size-4 accent-lav-600" checked={!off[i]} onChange={() => setOff({ ...off, [i]: !off[i] })} data-testid="onboarding-time-toggle" aria-label={`${DAYS[t.weekday]} ${t.start}`} />
                      <span>{DAYS[t.weekday]} from <b>{ampm(t.start)}</b> to <b>{ampm(t.end)}</b></span>
                      <span className="ml-auto text-xs text-muted">posts at {ampm(t.time)}</span>
                    </label>
                  </li>
                ))}
              </ul>
              <p className="mx-auto mt-4 flex max-w-lg items-center justify-center gap-1.5 text-center text-xs text-muted" data-testid="onboarding-basis">
                <Sparkles className="size-3.5 text-lav-600" />Based on {rec.basis} · times in {rec.timezone}
                {rec.source_url && <a data-testid="onboarding-source" className="underline" href={rec.source_url} target="_blank" rel="noreferrer">source</a>}
              </p>
            </div>
            <div className="flex items-center justify-between border-t border-lav-100 px-6 py-4">
              <button className="cursor-pointer text-sm text-ink-2 hover:text-ink" onClick={() => setStep('goal')} data-testid="onboarding-change-goal">Change goal</button>
              <div className="flex gap-2">
                <Button variant="ghost" onClick={() => finish(true)} disabled={busy} data-testid="onboarding-customize">Customize</Button>
                <Button variant="accent" onClick={() => finish(false)} loading={busy} disabled={Object.values(off).filter(Boolean).length === rec.times.length} data-testid="onboarding-got-it">Got it</Button>
              </div>
            </div>
          </div>
        )}
      </motion.div>
    </div>
  )
}
