import { BACKEND } from '@/lib/backend';

export const dynamic = 'force-dynamic';
export const maxDuration = 60;

export async function GET(req: Request) {
  const token = req.headers.get('x-metrics-token') ?? '';
  const days = new URL(req.url).searchParams.get('days') ?? '14';
  try {
    const r = await fetch(`${BACKEND}/api/metrics?days=${encodeURIComponent(days)}`, {
      headers: { 'x-metrics-token': token },
      signal: AbortSignal.timeout(55_000),
      cache: 'no-store',
    });
    return new Response(await r.text(), { status: r.status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });
  } catch {
    return Response.json({ detail: 'Backend unavailable' }, { status: 502 });
  }
}
