import { BACKEND } from '@/lib/backend';

export const maxDuration = 60;
export const dynamic = 'force-dynamic';

/** Pipes the backend's server-sent events straight through so the first words appear immediately. */
export async function POST(req: Request) {
  try {
    const body = await req.text();
    const upstream = await fetch(`${BACKEND}/api/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      signal: AbortSignal.timeout(58_000),
      cache: 'no-store',
    });
    if (!upstream.ok || !upstream.body) {
      const detail = await upstream.json().catch(() => null);
      return Response.json({ error: detail?.detail ?? `Backend returned ${upstream.status}` }, { status: upstream.status || 502 });
    }
    return new Response(upstream.body, {
      headers: { 'Content-Type': 'text/event-stream; charset=utf-8', 'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no' },
    });
  } catch (e: any) {
    const timedOut = e?.name === 'TimeoutError' || e?.name === 'AbortError';
    return Response.json({ error: timedOut ? 'Backend timed out.' : e?.message ?? 'Internal error' }, { status: timedOut ? 504 : 502 });
  }
}
