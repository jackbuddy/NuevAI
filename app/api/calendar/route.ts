import { BACKEND } from '@/lib/backend';

export const maxDuration = 30;

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  try {
    const r = await fetch(`${BACKEND}/api/calendar?${searchParams.toString()}`, { signal: AbortSignal.timeout(28_000) });
    const text = await r.text();
    return new Response(text, {
      status: r.status,
      headers: { 'Content-Type': 'application/json', 'Cache-Control': 'public, s-maxage=300, stale-while-revalidate=600' },
    });
  } catch (e: any) {
    return Response.json({ error: e?.name === 'TimeoutError' ? 'Calendar timed out' : 'Calendar unavailable' }, { status: 502 });
  }
}
