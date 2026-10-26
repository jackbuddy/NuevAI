import { BACKEND } from '@/lib/backend';

export const maxDuration = 30;

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  try {
    const r = await fetch(`${BACKEND}/lunch?${searchParams.toString()}`, { signal: AbortSignal.timeout(20_000) });
    const text = await r.text();
    return new Response(text, {
      status: r.status,
      headers: { 'Content-Type': 'application/json', 'Cache-Control': 'public, s-maxage=300, stale-while-revalidate=600' },
    });
  } catch {
    return Response.json({ error: 'Lunch unavailable' }, { status: 502 });
  }
}
