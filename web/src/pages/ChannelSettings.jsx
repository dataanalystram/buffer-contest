// Channel page "Posting schedule" (custom times per day, days off that keep their times, goal, generate slots, copy
// from another channel) and "Settings" (notification default, link shortening, UTM, queue actions, token, disconnect).
import { CalendarOff, Copy, KeyRound, Link2, Minus, Plus, Shuffle, Sparkles, Trash2, Wand2, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { del, get, patch, post, put } from '../api'
import { ConfirmButton } from '../components/Confirm'
import { go } from '../router'
import { useStore } from '../store'
import { Badge, Button, Card, cx, Field, Input, Select, Switch, useToast } from '../ui'

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const TZS = ['Europe/Dublin', 'Europe/London', 'Europe/Berlin', 'Asia/Kolkata', 'America/New_York', 'America/Los_Angeles', 'Asia/Singapore', 'Australia/Sydney', 'UTC']

function useSettings(id) {
  const toast = useToast()
  const [st, setSt] = useState(null)
  useEffect(() => { get(`/api/channels/${id}/settings`).then(setSt).catch((e) => toast(e.message, 'err')) }, [id])
  const save = async (body, msg = 'Saved') => {
    try { const r = await patch(`/api/channels/${id}/settings`, body); setSt(r); toast(msg); return r } catch (e) { toast(e.message, 'err') }
  }
  return [st, save]
}

export function ScheduleTab({ ch, setCh }) {
  const toast = useToast()
  const { reload, channels } = useStore()
  const [st, save] = useSettings(ch.id)
  const [newSlot, setNewSlot] = useState({ weekday: 'all', time: '12:00' })
  const [copyFrom, setCopyFrom] = useState('')
  const id = ch.id
  const refresh = async () => { setCh(await get(`/api/channels/${id}`)); reload() }
  const saveSlots = async (slots) => { try { setCh(await put(`/api/channels/${id}/slots`, { slots })); reload() } catch (e) { toast(e.message, 'err') } }
  const upd = async (body) => { try { setCh(await patch(`/api/channels/${id}`, body)); reload(); toast('Saved') } catch (e) { toast(e.message, 'err') } }
  const addSlot = () => {
    const days = newSlot.weekday === 'all' ? [0, 1, 2, 3, 4, 5, 6] : newSlot.weekday === 'weekdays' ? [0, 1, 2, 3, 4] : newSlot.weekday === 'weekends' ? [5, 6] : [Number(newSlot.weekday)]
    const have = new Set(ch.slots.map((s) => `${s.weekday}-${s.time}`))
    saveSlots([...ch.slots, ...days.filter((d) => !have.has(`${d}-${newSlot.time}`)).map((d) => ({ weekday: d, time: newSlot.time }))])
  }
  const generate = async (mode, extra = {}) => {
    try {
      const r = await post(`/api/channels/${id}/slots/generate`, { mode, ...extra })
      toast(r.message || `${r.added} posting time${r.added === 1 ? '' : 's'} added`); await refresh()
    } catch (e) { toast(e.message, 'err') }
  }
  const off = new Set(st?.disabled_days || [])
  const toggleDay = (d, on) => {
    const next = new Set(off)
    if (on) next.delete(d); else next.add(d)
    save({ disabled_days: [...next] }, on ? `${DAYS[d]} back on` : `${DAYS[d]} is a day off - its times are kept`)
    if (on && !ch.slots.some((s) => s.weekday === d)) saveSlots([...ch.slots, { weekday: d, time: '12:00' }])
  }
  const goal = st?.posting_goal || 0
  const others = channels.filter((c) => c.id !== id && (c.slots?.length ?? 1))
  return (
    <Card className="space-y-6 p-6" data-testid="channel-schedule">
      <div className="grid gap-4 md:grid-cols-4">
        <Field label="Timezone"><Select className="w-full" value={ch.timezone} onChange={(e) => upd({ timezone: e.target.value })} options={[...new Set([ch.timezone, ...TZS])].map((z) => [z, z])} data-testid="channel-timezone" /></Field>
        <Field label="Posting goal (posts a week)" hint={`${ch.slots.filter((s) => !off.has(s.weekday)).length} active posting times`}>
          <div className="flex items-center gap-2">
            <Button size="icon" variant="outline" aria-label="Lower goal" disabled={!st || goal <= 1} onClick={() => save({ posting_goal: goal - 1 })} data-testid="channel-goal-minus"><Minus className="size-4" /></Button>
            <span className="w-10 text-center text-lg font-semibold tabular-nums" data-testid="channel-goal">{goal || '–'}</span>
            <Button size="icon" variant="outline" aria-label="Raise goal" disabled={!st || goal >= 70} onClick={() => save({ posting_goal: goal + 1 })} data-testid="channel-goal-plus"><Plus className="size-4" /></Button>
          </div>
        </Field>
        <Field label="Daily limit (posts per day)" hint="The rate guard. 'Major news override' skips it.">
          <div className="flex items-center gap-2">
            <Button size="icon" variant="outline" aria-label="Decrease limit" onClick={() => upd({ daily_limit: Math.max(0, ch.daily_limit - 1) })} data-testid="channel-limit-minus"><Minus className="size-4" /></Button>
            <span className="w-10 text-center text-lg font-semibold tabular-nums" data-testid="channel-limit">{ch.daily_limit || '∞'}</span>
            <Button size="icon" variant="outline" aria-label="Increase limit" onClick={() => upd({ daily_limit: ch.daily_limit + 1 })} data-testid="channel-limit-plus"><Plus className="size-4" /></Button>
          </div>
        </Field>
        <Field label="Presets">
          <div className="flex flex-wrap gap-2">
            {[['huhbyte', '2× daily'], ['weekdays-3', '3× weekdays'], ['daily-1', '1× daily']].map(([p, l]) => <Button key={p} size="sm" variant="soft" onClick={async () => { setCh(await post(`/api/channels/${id}/slots/preset`, { preset: p })); toast('Slots updated') }} data-testid={`channel-preset-${p}`}><Wand2 className="size-3.5" />{l}</Button>)}
          </div>
        </Field>
      </div>
      <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-lav-200 p-4" data-testid="channel-generate">
        <Sparkles className="size-4 text-lav-600" /><span className="text-sm font-medium">Generate posting times</span>
        <Button size="sm" variant="soft" onClick={() => generate('goal')} data-testid="generate-goal">Add times to meet my goal</Button>
        <Button size="sm" variant="soft" onClick={() => generate('recommended')} data-testid="generate-recommended">Replace with recommended</Button>
        {others.length > 0 && <>
          <Select className="h-9" value={copyFrom} onChange={(e) => setCopyFrom(e.target.value)} aria-label="Copy from channel" data-testid="generate-copy-source"
            options={[['', 'Copy from channel…'], ...others.map((c) => [c.id, `@${c.username} (${c.provider})`])]} />
          <Button size="sm" variant="ghost" disabled={!copyFrom} onClick={() => generate('copy', { source_channel_id: copyFrom })} data-testid="generate-copy"><Copy className="size-3.5" />Copy times</Button>
        </>}
      </div>
      <div className="flex flex-wrap items-end gap-2 rounded-2xl bg-lav-50 p-4">
        <Field label="Add posting time"><Select value={newSlot.weekday} onChange={(e) => setNewSlot({ ...newSlot, weekday: e.target.value })} options={[['all', 'Every day'], ['weekdays', 'Weekdays'], ['weekends', 'Weekends'], ...DAYS.map((d, i) => [String(i), d])]} data-testid="slot-day" /></Field>
        <Input type="time" className="w-32" value={newSlot.time} onChange={(e) => setNewSlot({ ...newSlot, time: e.target.value })} data-testid="slot-time" aria-label="Time" />
        <Button variant="primary" onClick={addSlot} data-testid="slot-add"><Plus className="size-4" />Add time</Button>
        <ConfirmButton variant="danger-ghost" className="ml-auto" testid="slot-clear" title="Remove every posting time?" confirmLabel="Remove all"
          body="Queued posts keep their place but wait for new posting times." onConfirm={() => saveSlots([])}><Trash2 className="size-4" />Clear all</ConfirmButton>
      </div>
      <div className="grid gap-3 sm:grid-cols-4 lg:grid-cols-7" data-testid="slot-grid">
        {DAYS.map((d, i) => {
          const isOff = off.has(i)
          const times = ch.slots.filter((s) => s.weekday === i)
          return (
            <div key={d} className={cx('rounded-2xl border p-3', isOff ? 'border-dashed border-lav-200 bg-lav-50/50' : 'border-lav-200')} data-testid="slot-day-col" data-day={i} data-off={isOff ? '1' : '0'}>
              <div className="mb-2 flex items-center justify-between text-sm font-semibold">{d}
                <Switch checked={!isOff && times.length > 0} label={`${d} on/off`} testid={`slot-day-toggle-${i}`} onChange={(on) => toggleDay(i, on)} />
              </div>
              {isOff && <Badge tone="gray" className="mb-1" data-testid="slot-day-off"><CalendarOff className="size-3" />Day off</Badge>}
              <ul className={cx('space-y-1', isOff && 'opacity-50')}>
                {times.map((s) => (
                  <li key={s.time} className="group flex items-center justify-between rounded-lg bg-lav-100 px-2 py-1 text-sm tabular-nums" data-testid="slot">
                    {s.time}<button aria-label={`Remove ${d} ${s.time}`} className="cursor-pointer opacity-0 group-hover:opacity-100 focus:opacity-100" onClick={() => saveSlots(ch.slots.filter((x) => !(x.weekday === i && x.time === s.time)))} data-testid="slot-remove"><X className="size-3.5" /></button>
                  </li>
                ))}
                {!times.length && <li className="text-xs text-muted">No posts</li>}
              </ul>
            </div>
          )
        })}
      </div>
    </Card>
  )
}

export function SettingsTab({ ch, setCh, net }) {
  const toast = useToast()
  const { reload } = useStore()
  const [st, save] = useSettings(ch.id)
  const [utm, setUtm] = useState(null)
  useEffect(() => { if (st) setUtm({ utm_source: st.utm_source, utm_medium: st.utm_medium, utm_campaign: st.utm_campaign }) }, [st?.utm_source, st?.utm_medium, st?.utm_campaign])
  const upd = async (body) => { try { setCh(await patch(`/api/channels/${ch.id}`, body)); reload(); toast('Saved') } catch (e) { toast(e.message, 'err') } }
  const qa = (action, label) => async () => {
    const r = await post(`/api/channels/${ch.id}/queue/${action}`)
    toast(`${label}: ${r.count} post${r.count === 1 ? '' : 's'}`); window.dispatchEvent(new Event('huhq:posts-changed')); setCh(await get(`/api/channels/${ch.id}`))
  }
  const row = 'flex flex-wrap items-center justify-between gap-4 border-t border-lav-100 pt-5'
  return (
    <Card className="space-y-5 p-6" data-testid="channel-general">
      <div className="flex items-center justify-between gap-4">
        <div><div className="font-medium">Pause queue</div><p className="text-sm text-muted">Nothing is published from this channel while paused. Posts stay in order.</p></div>
        <Switch checked={ch.paused} onChange={(v) => upd({ paused: v })} label="Pause queue" testid="channel-pause" />
      </div>
      <div className={row}>
        <div><div className="font-medium">Notify me instead of auto-publishing (default)</div><p className="text-sm text-muted">New posts for @{ch.username} start in “Notify me” mode: you get a reminder and finish in the app.</p></div>
        <Switch checked={!!st?.notify_default} disabled={!st} onChange={(v) => save({ notify_default: v })} label="Notification publishing default" testid="channel-notify-default" />
      </div>
      <div className={row}>
        <div><div className="flex items-center gap-1.5 font-medium"><Link2 className="size-4 text-lav-600" />Link shortening</div><p className="text-sm text-muted">Shorten links in captions when the post goes out (uses your links service when one is set up).</p></div>
        <Switch checked={!!st?.link_shortening} disabled={!st} onChange={(v) => save({ link_shortening: v })} label="Link shortening" testid="channel-link-shortening" />
      </div>
      <div className={cx(row, 'items-start')}>
        <div className="min-w-64 flex-1"><div className="font-medium">UTM tracking</div><p className="text-sm text-muted">Adds utm_source / utm_medium / utm_campaign to every link at publish time. Links you tagged by hand are left alone.</p>
          {st?.utm_enabled && utm && (
            <div className="mt-3 grid gap-2 sm:grid-cols-3" data-testid="channel-utm-fields">
              {['utm_source', 'utm_medium', 'utm_campaign'].map((k) => (
                <Field key={k} label={k}><Input value={utm[k]} onChange={(e) => setUtm({ ...utm, [k]: e.target.value })} onBlur={() => utm[k] !== st[k] && save({ [k]: utm[k] })} data-testid={`channel-${k}`} /></Field>
              ))}
            </div>
          )}
        </div>
        <Switch checked={!!st?.utm_enabled} disabled={!st} onChange={(v) => save({ utm_enabled: v })} label="UTM tracking" testid="channel-utm" />
      </div>
      <div className={row} data-testid="channel-queue-actions">
        <div><div className="font-medium">Queue actions</div><p className="text-sm text-muted">Tidy up @{ch.username}'s queue.</p></div>
        <div className="flex flex-wrap gap-2">
          <ConfirmButton size="sm" variant="soft" danger={false} testid="queue-shuffle" title="Shuffle the queue?" confirmLabel="Shuffle" body="Queued posts swap places randomly; the posting times stay the same." onConfirm={qa('shuffle', 'Shuffled')}><Shuffle className="size-3.5" />Shuffle</ConfirmButton>
          <ConfirmButton size="sm" variant="outline" testid="queue-clean-failed" title="Remove failed posts?" confirmLabel="Remove" body="Deletes every failed post on this channel from HuhQueue." onConfirm={qa('clean-failed', 'Removed')}>Clean failed</ConfirmButton>
          <ConfirmButton size="sm" variant="outline" testid="queue-delete-drafts" title="Delete all drafts?" confirmLabel="Delete drafts" body="Deletes every draft on this channel. This can't be undone." onConfirm={qa('delete-drafts', 'Deleted')}>Delete drafts</ConfirmButton>
          <ConfirmButton size="sm" variant="danger-ghost" testid="queue-empty" title="Empty the queue?" confirmLabel="Empty queue" body="Deletes every scheduled post on this channel. This can't be undone." onConfirm={qa('empty', 'Emptied')}><Trash2 className="size-3.5" />Empty queue</ConfirmButton>
        </div>
      </div>
      <div className={row}>
        <div><div className="font-medium">Access token</div><p className="text-sm text-muted">Encrypted on this computer. Expires {ch.token_expires_at ? new Date(ch.token_expires_at).toLocaleString() : 'unknown'}; refreshed automatically.</p><p className="text-xs text-muted">Scopes: {ch.scopes || '—'}</p></div>
        <Button variant="outline" onClick={async () => { try { setCh(await post(`/api/channels/${ch.id}/refresh-token`)); toast('Token refreshed') } catch (e) { toast(e.message, 'err') } }} data-testid="channel-refresh-token"><KeyRound className="size-4" />Refresh now</Button>
      </div>
      <div className={row}>
        <div><div className="font-medium text-rose-700">Disconnect</div><p className="text-sm text-muted">Revokes access and deletes this channel’s posts, slots and comments from HuhQueue (not from {net}).</p></div>
        <ConfirmButton variant="danger" testid="channel-disconnect" title={`Disconnect @${ch.username}?`} confirmLabel="Disconnect"
          body={`Revokes access and deletes this channel’s posts, slots and comments from HuhQueue (not from ${net}).`}
          onConfirm={async () => { await del(`/api/channels/${ch.id}`); await reload(); toast('Disconnected'); go('/channels') }}><Trash2 className="size-4" />Disconnect</ConfirmButton>
      </div>
    </Card>
  )
}
