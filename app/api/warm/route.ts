import { BACKEND } from '@/lib/backend';

export const dynamic = 'force-dynamic';
export const maxDuration = 60;

/** The UI calls this on load so a sleeping backend starts waking while the visitor is still reading the page. */
export async function GET() {
  try {
    const r = await fetch(`${BACKEND}/health`, { signal: AbortSignal.timeout(55_000), cache: 'no-store' });
    return new Response(await r.text(), { status: r.status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });
  } catch {
    return Response.json({ status: 'waking' }, { status: 202 });
  }
}
