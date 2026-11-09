import { ImageResponse } from 'next/og';
import { brandIcon } from '@/lib/brand-icon';

// Icons referenced by app/manifest.ts: /pwa-icon/192, /pwa-icon/512, and ?maskable=1 for the full-bleed Android adaptive icon.
const SIZES = new Set([192, 512]);

export async function GET(req: Request, ctx: { params: Promise<{ size: string }> }) {
  const { size } = await ctx.params;
  const n = Number(size);
  if (!SIZES.has(n)) return new Response('Not found', { status: 404 });
  const maskable = new URL(req.url).searchParams.get('maskable') === '1';
  // maskable art must keep its mark inside the central 80% safe zone: smaller glyph, no rounding (the OS masks it)
  const res = new ImageResponse(brandIcon(n, maskable ? { glyph: 0.4 } : { radius: 0.22, glyph: 0.56 }), { width: n, height: n });
  res.headers.set('Cache-Control', 'public, max-age=86400, stale-while-revalidate=604800');
  return res;
}
