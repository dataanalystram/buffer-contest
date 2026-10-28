// A button that asks before doing something destructive (in-app dialog, not window.confirm).
import { useState } from 'react'
import { Button, Dialog } from '../ui'

export function ConfirmButton({ title, body, confirmLabel = 'Confirm', onConfirm, testid, danger = true, children, ...p }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const run = async () => {
    setBusy(true)
    try { await onConfirm() ; setOpen(false) } finally { setBusy(false) }
  }
  return (
    <>
      <Button aria-haspopup="dialog" data-testid={testid} onClick={() => setOpen(true)} {...p}>{children}</Button>
      <Dialog open={open} onClose={() => setOpen(false)} title={title} testid={`${testid}-dialog`}
        footer={<>
          <Button variant="ghost" onClick={() => setOpen(false)} data-testid={`${testid}-cancel`}>Cancel</Button>
          <Button variant={danger ? 'danger' : 'primary'} loading={busy} onClick={run} data-testid={`${testid}-confirm`}>{confirmLabel}</Button>
        </>}>
        <p className="text-sm text-ink-2">{body}</p>
      </Dialog>
    </>
  )
}
