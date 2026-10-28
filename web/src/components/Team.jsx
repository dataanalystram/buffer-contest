// Settings › Team (members, roles, invites, the per-channel access matrix), Settings › Security (profile, password,
// two-factor, sessions) and the public invite-accept page. Everything here uses the same REST API as agents.
import { motion } from 'motion/react'
import { Check, Copy, KeyRound, Link2, Lock, Mail, MonitorSmartphone, RefreshCw, ShieldCheck, ShieldOff, Trash2, UserPlus, Users } from 'lucide-react'
import { useEffect, useState } from 'react'
import { del, get, patch, post } from '../api'
import { useStore } from '../store'
import { Badge, Button, Card, ChannelAvatar, cx, Dialog, Field, Input, Select, useToast } from '../ui'
import CircularText from './CircularText'

export const ROLE_TONE = { owner: 'ink', admin: 'lav', editor: 'blue', contributor: 'amber', client: 'green', viewer: 'gray' }
const ROLE_NAME = { owner: 'Owner', admin: 'Admin', editor: 'Editor', contributor: 'Contributor', client: 'Client', viewer: 'Viewer' }
const CELL_LABEL = { '': 'Role default', editor: 'Editor', contributor: 'Contributor (needs approval)', viewer: 'View only', none: 'No access' }
export const roleName = (r) => ROLE_NAME[r] || r

function Initials({ name, className }) {
  const s = (name || '?').split(/\s+/).map((x) => x[0]).join('').slice(0, 2).toUpperCase()
  return <span className={cx('grid size-9 shrink-0 place-items-center rounded-full bg-lav-100 text-xs font-semibold text-lav-800', className)}>{s}</span>
}

export function CopyField({ value, testid }) {
  const toast = useToast()
  const [ok, setOk] = useState(false)
  return (
    <div className="flex gap-2">
      <Input readOnly value={value} onFocus={(e) => e.target.select()} data-testid={testid} className="font-mono text-xs" />
      <Button variant="soft" data-testid={`${testid}-copy`} onClick={() => { navigator.clipboard?.writeText(value); setOk(true); toast('Copied'); setTimeout(() => setOk(false), 1500) }}>
        {ok ? <Check className="size-4" /> : <Copy className="size-4" />}Copy
      </Button>
    </div>
  )
}

function AccessEditor({ channels, value, onChange, role }) {
  if (['owner', 'admin'].includes(role)) return <p className="text-xs text-muted">Owners and admins always have full access to every channel.</p>
  return (
    <div className="space-y-2" data-testid="access-editor">
      {channels.map((c) => (
        <div key={c.id} className="flex items-center gap-3">
          <ChannelAvatar ch={c} size={26} /><span className="min-w-0 flex-1 truncate text-sm">@{c.username} <span className="text-muted">· {c.provider}</span></span>
          <Select className="h-8 text-xs" value={value[c.id] || ''} data-testid={`access-${c.id}`}
            onChange={(e) => { const next = { ...value }; if (e.target.value) next[c.id] = e.target.value; else delete next[c.id]; onChange(next) }}
            options={Object.entries(CELL_LABEL)} />
        </div>
      ))}
      {!channels.length && <p className="text-xs text-muted">No channels connected yet.</p>}
      <p className="text-[11px] text-muted">A channel setting can only lower the person's role there, never raise it.</p>
    </div>
  )
}

function InviteDialog({ open, onClose, team, onDone }) {
  const toast = useToast()
  const [f, setF] = useState({ name: '', email: '', role: 'contributor', channel_access: {} })
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(null)
  useEffect(() => { if (open) { setF({ name: '', email: '', role: 'contributor', channel_access: {} }); setDone(null) } }, [open])
  const mine = team.me.role
  const roles = team.roles.filter((r) => r.id !== 'owner' || mine === 'owner').filter((r) => mine === 'owner' || !['admin', 'owner'].includes(r.id))
  const submit = async () => {
    setBusy(true)
    try { const r = await post('/api/team/invites', f); setDone(r); onDone() } catch (e) { toast(e.message, 'err') } finally { setBusy(false) }
  }
  return (
    <Dialog open={open} onClose={onClose} title={done ? 'Invite ready' : 'Invite a teammate or client'} testid="invite-dialog"
      footer={done ? <Button variant="primary" onClick={onClose} data-testid="invite-done">Done</Button>
        : <><Button data-testid="team-cancel" variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} onClick={submit} data-testid="invite-submit"><Link2 className="size-4" />Create invite link</Button></>}>
      {done ? (
        <div className="space-y-4" data-testid="invite-result">
          <p className="text-sm text-ink-2">{done.emailed ? <>We emailed the invite to <b>{done.member.email}</b>. You can also share the link:</> : 'Send this link to them yourself. It works once and expires in 7 days.'}</p>
          <CopyField value={done.invite_url} testid="invite-link" />
          {done.email_error && <p className="text-xs text-rose-600">{done.email_error}</p>}
          <p className="text-xs text-muted">They join as <b>{roleName(done.member.role)}</b>.</p>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Name"><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Ana Silva" data-testid="invite-name" /></Field>
            <Field label="Email (optional)" hint={team.smtp ? 'We email the link when you add one.' : 'Optional - you share the link either way.'}>
              <Input type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} placeholder="ana@example.com" data-testid="invite-email" />
            </Field>
          </div>
          <div>
            <div className="mb-2 text-[13px] font-medium text-ink-2">Role</div>
            <div className="grid gap-2" role="radiogroup" aria-label="Role">
              {roles.map((r) => (
                <label key={r.id} className={cx('flex cursor-pointer items-start gap-3 rounded-xl border p-3', f.role === r.id ? 'border-lav-600 bg-lav-50' : 'border-lav-200 hover:bg-lav-50/50')}>
                  <input type="radio" name="role" className="mt-1 accent-lav-600" checked={f.role === r.id} onChange={() => setF({ ...f, role: r.id })} data-testid={`invite-role-${r.id}`} />
                  <span><b className="text-sm">{roleName(r.id)}</b><span className="block text-xs text-muted">{r.label}</span></span>
                </label>
              ))}
            </div>
          </div>
          <details className="rounded-xl border border-lav-200 p-3">
            <summary className="cursor-pointer text-sm font-medium" data-testid="invite-access-toggle">Channel access</summary>
            <div className="mt-3"><AccessEditor channels={team.channels} role={f.role} value={f.channel_access} onChange={(v) => setF({ ...f, channel_access: v })} /></div>
          </details>
        </div>
      )}
    </Dialog>
  )
}

function AccessDialog({ m, team, onClose, onDone }) {
  const toast = useToast()
  const [v, setV] = useState(m?.channel_access || {})
  useEffect(() => setV(m?.channel_access || {}), [m])
  return (
    <Dialog open={!!m} onClose={onClose} title={m ? `Channel access · ${m.name}` : ''} testid="access-dialog"
      footer={<Button variant="primary" data-testid="access-save" onClick={async () => {
        try { await patch(`/api/team/members/${m.id}`, { channel_access: v }); toast('Access saved'); onDone(); onClose() } catch (e) { toast(e.message, 'err') }
      }}>Save access</Button>}>
      {m && <AccessEditor channels={team.channels} role={m.role} value={v} onChange={setV} />}
    </Dialog>
  )
}

function Matrix({ team }) {
  const people = team.members.filter((m) => m.status !== 'disabled')
  const eff = (m, c) => {
    if (['owner', 'admin'].includes(m.role)) return roleName(m.role)
    const cell = m.channel_access?.[c.id]
    if (cell === 'none') return '—'
    const rank = { viewer: 1, client: 1, contributor: 2, editor: 3 }
    return cell && rank[cell] < rank[m.role] ? roleName(cell) : roleName(m.role)
  }
  if (!team.channels.length) return null
  return (
    <Card className="overflow-x-auto p-5" data-testid="team-matrix">
      <h3 className="mb-1 font-semibold">Who can do what, per channel</h3>
      <p className="mb-3 text-sm text-muted">Effective role on each channel. Change it from a person's “Channels” button.</p>
      <table className="w-full text-sm">
        <thead><tr><th className="py-2 pr-3 text-left font-medium text-muted">Person</th>{team.channels.map((c) => <th key={c.id} className="px-2 py-2 text-left font-medium"><span className="flex items-center gap-2"><ChannelAvatar ch={c} size={22} />@{c.username}</span></th>)}</tr></thead>
        <tbody>{people.map((m) => (
          <tr key={m.id} className="border-t border-lav-100" data-testid="matrix-row">
            <td className="py-2 pr-3 font-medium">{m.name}</td>
            {team.channels.map((c) => { const e = eff(m, c); return <td key={c.id} className={cx('px-2 py-2', e === '—' ? 'text-muted' : '')} data-testid="matrix-cell">{e}</td> })}
          </tr>
        ))}</tbody>
      </table>
    </Card>
  )
}

export function TeamSection() {
  const toast = useToast()
  const [team, setTeam] = useState(null)
  const [err, setErr] = useState('')
  const [inviting, setInviting] = useState(false)
  const [access, setAccess] = useState(null)
  const [link, setLink] = useState(null)
  const load = () => get('/api/team').then(setTeam).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  if (err) return <Card className="p-6 text-sm text-ink-2" data-testid="settings-team">{err}</Card>
  if (!team) return null
  const me = team.me
  const canTouch = (m) => me.role === 'owner' || !['owner', 'admin'].includes(m.role) || m.id === me.id
  const roleOpts = team.roles.filter((r) => me.role === 'owner' || !['owner', 'admin'].includes(r.id)).map((r) => [r.id, roleName(r.id)])
  const act = async (fn, msg) => { try { await fn(); msg && toast(msg); load() } catch (e) { toast(e.message, 'err') } }
  return (
    <div className="space-y-4" data-testid="settings-team">
      <Card className="divide-y divide-lav-100">
        <div className="flex flex-wrap items-center gap-3 p-5">
          <Users className="size-5 text-lav-600" />
          <div className="flex-1"><h3 className="font-semibold">Team members <span className="text-muted">({team.members.length})</span></h3>
            <p className="text-sm text-muted">Invite teammates and clients. Contributors' posts wait for an approval; clients can view and comment.</p></div>
          <Button variant="primary" onClick={() => setInviting(true)} data-testid="team-invite"><UserPlus className="size-4" />Invite</Button>
        </div>
        {team.members.map((m) => (
          <div key={m.id} className="flex flex-wrap items-center gap-3 p-4" data-testid="team-member" data-email={m.email} data-status={m.status}>
            <Initials name={m.name} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2 font-medium">{m.name}{m.id === me.id && <span className="text-xs text-muted">(you)</span>}
                {m.status === 'invited' && <Badge tone="amber" data-testid="member-status">Invited</Badge>}
                {m.status === 'disabled' && <Badge tone="red" data-testid="member-status">Disabled</Badge>}
                {m.totp_enabled && <Badge tone="green" title="Two-factor sign-in on"><ShieldCheck className="mr-0.5 inline size-3" />2FA</Badge>}
              </div>
              <div className="text-xs text-muted">{m.email || 'Invite link (no email)'}{m.handle ? ` · @${m.handle}` : ''}{m.last_login_at ? ` · last seen ${new Date(m.last_login_at).toLocaleDateString()}` : ''}</div>
            </div>
            {canTouch(m) && m.id !== me.id
              ? <Select className="h-8 text-xs" value={m.role} aria-label={`Role for ${m.name}`} data-testid="member-role" options={roleOpts.some(([r]) => r === m.role) ? roleOpts : [[m.role, roleName(m.role)], ...roleOpts]}
                onChange={(e) => act(() => patch(`/api/team/members/${m.id}`, { role: e.target.value }), `${m.name} is now ${roleName(e.target.value)}`)} />
              : <Badge tone={ROLE_TONE[m.role]} data-testid="member-role-badge">{roleName(m.role)}</Badge>}
            {canTouch(m) && !['owner', 'admin'].includes(m.role) && <Button size="sm" variant="ghost" onClick={() => setAccess(m)} data-testid="member-access">Channels</Button>}
            {canTouch(m) && m.status === 'invited' && <Button size="sm" variant="ghost" data-testid="member-resend" onClick={() => act(async () => setLink(await post(`/api/team/invites/${m.id}/resend`)))}><RefreshCw className="size-3.5" />New link</Button>}
            {canTouch(m) && m.id !== me.id && m.status !== 'invited' && <Button size="sm" variant="ghost" data-testid="member-disable"
              onClick={() => act(() => patch(`/api/team/members/${m.id}`, { disabled: m.status !== 'disabled' }), m.status === 'disabled' ? 'Re-enabled' : 'Disabled and signed out')}>{m.status === 'disabled' ? 'Enable' : 'Disable'}</Button>}
            {canTouch(m) && m.id !== me.id && m.totp_enabled && <Button size="sm" variant="ghost" title="Lost phone: turn off their two-factor" data-testid="member-reset-2fa"
              onClick={() => confirm(`Turn off two-factor for ${m.name}?`) && act(() => post(`/api/team/members/${m.id}/reset-2fa`), 'Two-factor turned off')}><ShieldOff className="size-3.5" /></Button>}
            {canTouch(m) && m.id !== me.id && <Button size="icon-sm" variant="danger-ghost" aria-label={`Remove ${m.name}`} data-testid="member-remove"
              onClick={() => confirm(`Remove ${m.name} from the team?`) && act(() => del(`/api/team/members/${m.id}`), 'Removed')}><Trash2 className="size-3.5" /></Button>}
          </div>
        ))}
      </Card>
      <Card className="grid gap-3 p-5 md:grid-cols-3" data-testid="team-roles">
        {team.roles.map((r) => <div key={r.id} className="rounded-xl bg-lav-50 p-3"><Badge tone={ROLE_TONE[r.id]}>{roleName(r.id)}</Badge><p className="mt-1 text-xs text-ink-2">{r.label}</p></div>)}
      </Card>
      <Matrix team={team} />
      {!team.smtp && <p className="text-xs text-muted" data-testid="team-smtp-hint"><Mail className="mr-1 inline size-3.5" />To email invites and notifications, set <code>HUHQ_SMTP_HOST</code>, <code>HUHQ_SMTP_USER</code>, <code>HUHQ_SMTP_PASSWORD</code> and <code>HUHQ_SMTP_FROM</code> in <code>.env</code>. Invite links work without it.</p>}
      <InviteDialog open={inviting} onClose={() => setInviting(false)} team={team} onDone={load} />
      <AccessDialog m={access} team={team} onClose={() => setAccess(null)} onDone={load} />
      <Dialog open={!!link} onClose={() => setLink(null)} title="New invite link" testid="resend-dialog">
        {link && <div className="space-y-3"><p className="text-sm text-ink-2">The old link no longer works.{link.emailed ? ' We emailed the new one too.' : ''}</p><CopyField value={link.invite_url} testid="resend-link" /></div>}
      </Dialog>
    </div>
  )
}

// --------------------------------------------------------------------------------------------- Settings › Security
function TwoFactor({ account, reload }) {
  const toast = useToast()
  const [setup, setSetup] = useState(null)
  const [code, setCode] = useState('')
  const [codes, setCodes] = useState(null)
  const [confirmPw, setConfirmPw] = useState('')
  const run = async (fn) => { try { await fn() } catch (e) { toast(e.message, 'err') } }
  return (
    <Card className="space-y-4 p-6" data-testid="security-2fa">
      <div className="flex items-center gap-3">
        <KeyRound className="size-5 text-lav-600" />
        <div className="flex-1"><h3 className="font-semibold">Two-factor sign-in</h3><p className="text-sm text-muted">A 6-digit code from an authenticator app (Google Authenticator, 1Password, Authy…) at every sign-in.</p></div>
        <Badge tone={account.totp_enabled ? 'green' : 'gray'} data-testid="2fa-status">{account.totp_enabled ? 'On' : 'Off'}</Badge>
      </div>
      {codes && (
        <div className="space-y-2 rounded-2xl bg-amber-50 p-4" data-testid="2fa-recovery-codes">
          <p className="text-sm font-medium text-amber-900">Save these recovery codes now. Each works once if you lose your phone; they are shown only once.</p>
          <pre className="grid grid-cols-2 gap-1 font-mono text-sm">{codes.map((c) => <span key={c}>{c}</span>)}</pre>
          <Button data-testid="team-copy-codes" size="sm" variant="soft" onClick={() => { navigator.clipboard?.writeText(codes.join('\n')); toast('Copied') }}><Copy className="size-3.5" />Copy codes</Button>
        </div>
      )}
      {!account.totp_enabled && !setup && <Button variant="primary" data-testid="2fa-setup" onClick={() => run(async () => setSetup(await post('/api/account/2fa/setup')))}><ShieldCheck className="size-4" />Set up two-factor</Button>}
      {!account.totp_enabled && setup && (
        <div className="flex flex-wrap items-start gap-6" data-testid="2fa-enrol">
          <img src={setup.qr} alt="QR code for your authenticator app" className="size-44 rounded-xl border border-lav-200 bg-white p-1" data-testid="2fa-qr" />
          <div className="min-w-64 flex-1 space-y-3">
            <p className="text-sm text-ink-2">Scan the QR code, or type this key: <code className="break-all" data-testid="2fa-secret">{setup.secret}</code></p>
            <Field label="6-digit code"><Input inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} placeholder="123456" data-testid="2fa-code" /></Field>
            <Button variant="primary" data-testid="2fa-enable" onClick={() => run(async () => { const r = await post('/api/account/2fa/enable', { code }); setCodes(r.recovery_codes); setSetup(null); setCode(''); toast('Two-factor is on'); reload() })}>Turn on</Button>
          </div>
        </div>
      )}
      {account.totp_enabled && (
        <div className="flex flex-wrap items-end gap-2">
          <p className="w-full text-xs text-muted">{account.recovery_codes_left} recovery codes left. Confirm with your password or a current code:</p>
          <Input type="password" className="max-w-xs" placeholder="Password or 6-digit code" value={confirmPw} onChange={(e) => setConfirmPw(e.target.value)} data-testid="2fa-confirm" />
          <Button variant="outline" data-testid="2fa-new-codes" onClick={() => run(async () => { const r = await post('/api/account/2fa/recovery-codes', /^\d{6}$/.test(confirmPw) ? { code: confirmPw } : { password: confirmPw }); setCodes(r.recovery_codes); setConfirmPw(''); reload() })}>New recovery codes</Button>
          <Button variant="danger-ghost" data-testid="2fa-disable" onClick={() => run(async () => { await post('/api/account/2fa/disable', /^\d{6}$/.test(confirmPw) ? { code: confirmPw } : { password: confirmPw }); setConfirmPw(''); setCodes(null); toast('Two-factor is off'); reload() })}>Turn off</Button>
        </div>
      )}
    </Card>
  )
}

function Sessions() {
  const toast = useToast()
  const [rows, setRows] = useState([])
  const load = () => get('/api/account/sessions').then(setRows).catch(() => {})
  useEffect(() => { load() }, [])
  return (
    <Card className="p-6" data-testid="security-sessions">
      <div className="mb-3 flex items-center gap-3"><MonitorSmartphone className="size-5 text-lav-600" /><h3 className="flex-1 font-semibold">Where you're signed in</h3>
        <Button size="sm" variant="outline" data-testid="sessions-revoke-others" onClick={async () => { const r = await post('/api/account/sessions/revoke-others'); toast(`Signed out ${r.revoked} other session(s)`); load() }}>Sign out everywhere else</Button></div>
      <ul className="divide-y divide-lav-100">{rows.map((r) => (
        <li key={r.id} className="flex items-center gap-3 py-2.5 text-sm" data-testid="session-row">
          <span className="min-w-0 flex-1 truncate">{(r.user_agent || 'Unknown browser').slice(0, 80)}<span className="block text-xs text-muted">{r.ip || '—'} · last active {r.last_seen_at ? new Date(r.last_seen_at).toLocaleString() : '—'}</span></span>
          {r.current ? <Badge tone="green">This browser</Badge> : <Button size="sm" variant="ghost" onClick={async () => { await del(`/api/account/sessions/${r.id}`); load() }} data-testid="session-revoke">Sign out</Button>}
        </li>))}</ul>
    </Card>
  )
}

export function SecuritySection() {
  const toast = useToast()
  const { reload: reloadStore } = useStore()
  const [account, setAccount] = useState(null)
  const [prof, setProf] = useState({ name: '', email: '' })
  const [cur, setCur] = useState('')
  const [nw, setNw] = useState('')
  const load = () => get('/api/account').then((a) => { setAccount(a); setProf({ name: a.name, email: a.email }) }).catch(() => setAccount(false))
  useEffect(() => { load() }, [])
  return (
    <div className="space-y-4" data-testid="settings-security">
      {account && (
        <Card className="space-y-3 p-6" data-testid="security-profile">
          <h3 className="font-semibold">Your profile</h3>
          <div className="grid gap-3 md:grid-cols-[1fr_1fr_auto] md:items-end">
            <Field label="Name"><Input value={prof.name} onChange={(e) => setProf({ ...prof, name: e.target.value })} data-testid="profile-name" /></Field>
            <Field label="Sign-in email"><Input type="email" value={prof.email} onChange={(e) => setProf({ ...prof, email: e.target.value })} data-testid="profile-email" /></Field>
            <Button variant="primary" data-testid="profile-save" onClick={async () => { try { await patch('/api/account', prof); toast('Profile saved'); load(); reloadStore() } catch (e) { toast(e.message, 'err') } }}>Save</Button>
          </div>
          <p className="text-xs text-muted">Role: <b>{roleName(account.role)}</b>{account.handle && <> · teammates mention you as <code>@{account.handle}</code></>}</p>
        </Card>
      )}
      <Card className="space-y-3 p-6">
        <h3 className="flex items-center gap-2 font-semibold"><Lock className="size-4 text-lav-600" />Password</h3>
        <div className="grid gap-3 md:grid-cols-3">
          <Input type="password" placeholder="Current password" value={cur} onChange={(e) => setCur(e.target.value)} data-testid="security-current" />
          <Input type="password" placeholder="New password (8+)" value={nw} onChange={(e) => setNw(e.target.value)} data-testid="security-new" />
          <Button variant="primary" onClick={async () => { try { await post('/api/auth/password', { current: cur, new: nw }); setCur(''); setNw(''); toast('Password changed - other sessions logged out') } catch (e) { toast(e.message, 'err') } }} data-testid="security-change">Change password</Button>
        </div>
      </Card>
      {account && <TwoFactor account={account} reload={load} />}
      {account && <Sessions />}
      <Card className="p-6">
        <ul className="list-disc space-y-1 pl-5 text-sm text-ink-2">
          <li>OAuth tokens are encrypted at rest (Fernet / AES) with a key in <code>.env</code> or <code>data/secret.key</code>.</li>
          <li>Connect flows use a one-time random <code>state</code> (CSRF) and PKCE for Google; callbacks older than 15 min are refused.</li>
          <li>Each person signs in with their own email and password (server-side sessions, CSRF tokens on every change). Agent tokens are hashed, scoped and capped by their owner's role.</li>
          <li>Logs redact access tokens, codes and secrets. Only the media/OAuth edge port is ever tunnelled.</li>
        </ul>
      </Card>
    </div>
  )
}

// --------------------------------------------------------------------------------------------- public invite page
export function InviteAccept({ token, onDone }) {
  const [info, setInfo] = useState(null)
  const [err, setErr] = useState('')
  const [f, setF] = useState({ name: '', email: '', password: '', password2: '' })
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    get(`/api/invites/${token}`).then((i) => { setInfo(i); setF((x) => ({ ...x, name: i.name === 'Invited teammate' ? '' : i.name || '' })) }).catch((e) => setErr(e.message))
  }, [token])
  const submit = async (e) => {
    e.preventDefault()
    setErr('')
    if (f.password !== f.password2) return setErr('Passwords do not match')
    setBusy(true)
    try {
      await post(`/api/invites/${token}/accept`, { name: f.name, email: info.needs_email ? f.email : undefined, password: f.password })
      window.location.hash = '#/queue'
      onDone()
    } catch (e2) { setErr(e2.message) } finally { setBusy(false) }
  }
  return (
    <div className="bg-grid grid min-h-full place-items-center p-6">
      <motion.form onSubmit={submit} initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} data-testid="invite-accept"
        className="w-full max-w-md rounded-3xl border border-lav-200 bg-surface p-8 shadow-pop" aria-label="Accept invite">
        <div className="mb-6 flex items-center gap-4">
          <CircularText size={84} text="HUHQUEUE • HUHBYTE • "><img src="/static/mark.png" alt="" className="size-9" /></CircularText>
          <div>
            <h1 className="text-xl font-semibold">Join the team</h1>
            {info && <p className="text-sm text-muted" data-testid="invite-info"><b>{info.invited_by || 'Your team'}</b> invited you as <Badge tone={ROLE_TONE[info.role]}>{roleName(info.role)}</Badge></p>}
          </div>
        </div>
        {info ? (
          <div className="space-y-3">
            {info.role_label && <p className="rounded-xl bg-lav-50 p-3 text-xs text-ink-2">{info.role_label}</p>}
            <Input placeholder="Your name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} data-testid="invite-accept-name" aria-label="Your name" />
            {info.needs_email ? <Input type="email" placeholder="Your email (you'll sign in with it)" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} data-testid="invite-accept-email" aria-label="Email" />
              : <Input value={info.email} readOnly aria-label="Email" className="text-muted" />}
            <Input type="password" placeholder="Choose a password (8+ characters)" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} data-testid="invite-accept-password" aria-label="Password" />
            <Input type="password" placeholder="Repeat password" value={f.password2} onChange={(e) => setF({ ...f, password2: e.target.value })} data-testid="invite-accept-password2" aria-label="Repeat password" />
            {err && <p className="text-sm text-rose-600" role="alert">{err}</p>}
            <Button variant="primary" className="w-full justify-center" loading={busy} data-testid="invite-accept-submit"><UserPlus className="size-4" />Join</Button>
          </div>
        ) : err ? <p className="text-sm text-rose-600" role="alert" data-testid="invite-error">{err}</p> : <p className="text-sm text-muted">Checking the invite…</p>}
        <a data-testid="team-already-have-an-account" href="#/home" onClick={() => setTimeout(() => window.location.reload(), 0)} className="mt-4 block text-center text-xs text-lav-700 hover:underline">Already have an account? Log in</a>
      </motion.form>
    </div>
  )
}
