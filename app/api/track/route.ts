import { BACKEND } from '@/lib/backend';

export const maxDuration = 15;

/** Forwards analytics batches to the backend. Always answers 202: analytics must never surface errors to users.
 *  The browser's own User-Agent is forwarded twice: as User-Agent (what the backend used before) and as x-client-ua, which it prefers
 *  because the Next.js server's own UA is just "node". sendBeacon requests carry the browser UA too, so unload flushes are covered. */
export async function POST(req: Request) {
  try {
    const body = await req.text();
    if (body.length > 20_000) return new Response(null, { status: 202 });
    const ua = req.headers.get('user-agent') ?? '';
    await fetch(`${BACKEND}/api/track`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'User-Agent': ua, 'x-client-ua': ua.slice(0, 200) },
      body,
      signal: AbortSignal.timeout(8_000),
    }).catch(() => {});
  } catch {}
  return new Response(null, { status: 202 });
}
