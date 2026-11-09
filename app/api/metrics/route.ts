import { BACKEND } from '@/lib/backend';

export const dynamic = 'force-dynamic';
export const maxDuration = 60;

const AUD = new Set(['users', 'users_internal', 'test_bots', 'all']);

/** Dashboard summary. The token is only ever forwarded, never logged. `days` is clamped by the backend (24h/7/30/90/all=0). */
export async function GET(req: Request) {
  const token = req.headers.get('x-metrics-token') ?? '';
  const u = new URL(req.url);
  const days = Number(u.searchParams.get('days') ?? '7');
  const aud = u.searchParams.get('aud') ?? 'users';
  const refresh = u.searchParams.get('refresh') === '1' ? '1' : '0';
  const qs = new URLSearchParams({ days: String(Number.isFinite(days) ? Math.trunc(days) : 7), aud: AUD.has(aud) ? aud : 'users', refresh });
  try {
    const r = await fetch(`${BACKEND}/api/metrics?${qs}`, {
      headers: { 'x-metrics-token': token },
      signal: AbortSignal.timeout(55_000),
      cache: 'no-store',
    });
    return new Response(await r.text(), { status: r.status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });
  } catch {
    return Response.json({ detail: 'Backend unavailable' }, { status: 502 });
  }
}
