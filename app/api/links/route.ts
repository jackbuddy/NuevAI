import { BACKEND } from '@/lib/backend';

export const maxDuration = 30;

export async function GET() {
  try {
    const r = await fetch(`${BACKEND}/api/links`, { signal: AbortSignal.timeout(25_000) });
    return new Response(await r.text(), {
      status: r.status,
      headers: { 'Content-Type': 'application/json', 'Cache-Control': 'public, s-maxage=3600, stale-while-revalidate=86400' },
    });
  } catch {
    return Response.json({ links: [] }, { status: 200 });
  }
}
