// Sticky live phone preview + the Share popover (copy, open, QR code, custom domain).
import { AnimatePresence, motion } from 'motion/react'
import { Copy, Download, ExternalLink, Globe, QrCode, Share2, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { get } from '../../api'
import { Button, cx, Dialog, SkeletonList, useOutside, useToast } from '../../ui'
import { fetchFile, useCopy } from './shared'

export function PhonePreview({ page, html, focus, open, onClose }) {
  const frame = useRef(null)
  const [qr, setQr] = useState(false)
  // Write the draft HTML into the same document (no navigation flash), keep the scroll position, and bring the
  // block being edited into view. The preview HTML has no scripts; the iframe is same-origin (about:blank).
  useEffect(() => {
    const doc = frame.current?.contentDocument
    if (!doc || !html) return
    const y = doc.scrollingElement?.scrollTop || 0
    doc.open(); doc.write(html); doc.close()
    if (doc.scrollingElement) doc.scrollingElement.scrollTop = y
    const el = focus && doc.querySelector(`[data-id="${CSS.escape(focus)}"]`)
    if (el) el.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [html])
  return (
    <aside data-testid="bio-preview-pane"
      className={cx('lg:sticky lg:top-4 lg:block', open ? 'fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-night/60 p-4 backdrop-blur-sm' : 'hidden')}>
      {open && <Button variant="primary" size="icon" aria-label="Close preview" onClick={onClose} data-testid="bio-preview-close" className="absolute right-4 top-4 rounded-full lg:hidden"><X className="size-4" /></Button>}
      <div className="relative mx-auto w-fit">
        <div className="relative h-[640px] w-[316px] overflow-hidden rounded-[2.9rem] border-[10px] border-[#141020] bg-[#141020] shadow-pop ring-1 ring-lav-300/40">
          <span className="absolute left-1/2 top-2 z-10 h-[22px] w-[92px] -translate-x-1/2 rounded-full bg-[#141020]" aria-hidden="true" />
          <iframe ref={frame} title="Bio page preview" src="about:blank" className="size-full rounded-[2.2rem] bg-white" data-testid="bio-preview" />
        </div>
        <div className="mt-3 flex items-center justify-center gap-2">
          <span className="text-xs text-muted">Live preview{page.published ? '' : ' · not published'}</span>
          <Button size="icon-sm" variant="outline" className="rounded-full" aria-label="QR code" onClick={() => setQr(true)} data-testid="bio-qr-open-side"><QrCode className="size-3.5" /></Button>
          <Button size="icon-sm" variant="outline" className="rounded-full" aria-label="Open the public page" onClick={() => window.open(page.domain_url || page.public_url, '_blank')} data-testid="bio-open-side"><ExternalLink className="size-3.5" /></Button>
        </div>
      </div>
      <QrDialog open={qr} onClose={() => setQr(false)} page={page} />
    </aside>
  )
}

export function SharePopover({ page }) {
  const copy = useCopy()
  const [open, setOpen] = useState(false)
  const [qr, setQr] = useState(false)
  const ref = useRef(null)
  useOutside(ref, () => setOpen(false))
  const url = page.domain_url || page.public_url
  return (
    <div className="relative" ref={ref}>
      <Button size="sm" variant="outline" className="rounded-full" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(!open)} data-testid="bio-share"><Share2 className="size-3.5" />Share</Button>
      <AnimatePresence>
        {open && (
          <motion.div role="dialog" aria-label="Share your page" data-testid="bio-share-popover" initial={{ opacity: 0, y: -6, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -4 }}
            className="absolute right-0 top-11 z-40 w-[320px] space-y-3 rounded-3xl border border-lav-200 bg-popover p-4 shadow-pop">
            <div>
              <h4 className="text-sm font-semibold">Share your page</h4>
              <p className="text-xs text-muted">{page.published ? 'Your page is live. Put this link in your bios.' : 'Publish to make this link work for visitors.'}</p>
            </div>
            <div className="flex items-center gap-2 rounded-2xl bg-lav-100 p-1.5 pl-3">
              <span className="min-w-0 flex-1 truncate text-[13px] font-medium" data-testid="bio-share-url">{url.replace(/^https?:\/\//, '')}</span>
              <Button size="sm" variant="primary" className="rounded-xl" onClick={() => copy(url)} data-testid="bio-copy"><Copy className="size-3.5" />Copy</Button>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Button size="sm" onClick={() => { setOpen(false); setQr(true) }} data-testid="bio-qr-open"><QrCode className="size-3.5" />QR code</Button>
              <Button size="sm" onClick={() => window.open(url, '_blank')} data-testid="bio-open"><ExternalLink className="size-3.5" />Open</Button>
            </div>
            {page.domain_url
              ? <a href={page.domain_url} target="_blank" rel="noreferrer" className="flex items-center gap-1.5 text-xs text-lav-700 hover:underline" data-testid="bio-domain-url"><Globe className="size-3.5" />{page.domain_url}</a>
              : <p className="flex items-center gap-1.5 text-xs text-muted"><Globe className="size-3.5" />Use your own domain in Settings.</p>}
          </motion.div>
        )}
      </AnimatePresence>
      <QrDialog open={qr} onClose={() => setQr(false)} page={page} />
    </div>
  )
}

export function QrDialog({ open, onClose, page }) {
  const toast = useToast()
  const [qr, setQr] = useState(null)
  useEffect(() => { if (open) get(`/api/bio/${page.id}/qr`).then(setQr).catch((e) => toast(e.message, 'err')) }, [open, page.id, page.domain])
  return (
    <Dialog open={open} onClose={onClose} title="QR code" testid="bio-qr">
      {!qr ? <SkeletonList rows={1} /> : (
        <div className="space-y-4 text-center">
          <img src={qr.png_data_uri} alt={`QR code for ${qr.url}`} className="mx-auto size-56 rounded-3xl border border-lav-200 bg-white p-3 shadow-soft" data-testid="bio-qr-image" />
          <p className="break-all text-sm text-muted" data-testid="bio-qr-url">{qr.url}</p>
          <div className="flex justify-center gap-2">
            <Button size="sm" variant="primary" onClick={() => fetchFile(`/api/bio/${page.id}/qr?format=png&scale=16&download=true`, `bio-${page.slug}-qr.png`)} data-testid="bio-qr-png"><Download className="size-3.5" />PNG</Button>
            <Button size="sm" onClick={() => fetchFile(`/api/bio/${page.id}/qr?format=svg&download=true`, `bio-${page.slug}-qr.svg`)} data-testid="bio-qr-svg"><Download className="size-3.5" />SVG</Button>
          </div>
          <p className="text-xs text-muted">Print it on stickers, slides or your video end card. It follows your custom domain when you set one.</p>
        </div>
      )}
    </Dialog>
  )
}
