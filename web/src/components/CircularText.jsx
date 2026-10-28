// Rotating circular text badge (own implementation of the "circular text" idea popularised by
// reactbits.dev / panelui.dev). Pure SVG + CSS, respects prefers-reduced-motion.
export default function CircularText({ text = 'HUHBYTE • AI NEWS • DAILY • ', size = 120, className = '', children }) {
  const r = size / 2 - 12
  const id = 'ct-' + size
  return (
    <div className={`relative ${className}`} style={{ width: size, height: size }} aria-hidden="true">
      <svg viewBox={`0 0 ${size} ${size}`} className="spin-slow absolute inset-0">
        <defs>
          <path id={id} d={`M ${size / 2},${size / 2} m -${r},0 a ${r},${r} 0 1,1 ${r * 2},0 a ${r},${r} 0 1,1 -${r * 2},0`} />
        </defs>
        <text className="fill-lav-700" style={{ fontSize: size / 10.5, fontWeight: 700, letterSpacing: size / 40 }}>
          <textPath href={`#${id}`}>{text}</textPath>
        </text>
      </svg>
      <div className="absolute inset-0 grid place-items-center">{children}</div>
    </div>
  )
}
