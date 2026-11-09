/** The NuevAI mark (navy tile, white four-point sparkle: same as components/Logo.tsx) as JSX for next/og ImageResponse. No binary assets. */
export function brandIcon(size: number, opts: { radius?: number; glyph?: number } = {}) {
  const glyph = Math.round(size * (opts.glyph ?? 0.56));
  return (
    <div style={{ width: size, height: size, display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#0b2145', borderRadius: Math.round(size * (opts.radius ?? 0)) }}>
      <svg viewBox="0 0 24 24" width={glyph} height={glyph} fill="#ffffff">
        <path d="M12 2.5l2 6.2 6.2 2-6.2 2-2 6.3-2-6.3-6.2-2 6.2-2 2-6.2z" />
      </svg>
    </div>
  );
}
