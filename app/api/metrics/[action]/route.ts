import { BACKEND } from '@/lib/backend';

export const dynamic = 'force-dynamic';
export const maxDuration = 60;

/** Token-gated sub-endpoints of the Insights dashboard: day (hourly timeline), inspect (question drill-down), flagged (answers to review), ids + mark (audience management),
 *  whoami (token check). Only these names are forwarded; the query string is rebuilt from an allow-list. */
const GET_ACTIONS: Record<string, string[]> = { day: ['date', 'aud'], inspect: ['key', 'days', 'aud'], flagged: ['days', 'aud'], ids: [], whoami: [] };
const POST_ACTIONS = new Set(['mark']);

async function forward(req: Request, action: string, method: 'GET' | 'POST') {
  const token = req.headers.get('x-metrics-token') ?? '';
  const src = new URL(req.url).searchParams;
  const qs = new URLSearchParams();
  for (const k of GET_ACTIONS[action] ?? []) { const v = src.get(k); if (v != null) qs.set(k, v.slice(0, 200)); }
  try {
    const body = method === 'POST' ? await req.text() : undefined;
    if (body && body.length > 2_000) return Response.json({ detail: 'Too large' }, { status: 413 });
    const r = await fetch(`${BACKEND}/api/metrics/${action}${qs.size ? `?${qs}` : ''}`, {
      method,
      headers: { 'x-metrics-token': token, ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body,
      signal: AbortSignal.timeout(55_000),
      cache: 'no-store',
    });
    return new Response(await r.text(), { status: r.status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });
  } catch {
    return Response.json({ detail: 'Backend unavailable' }, { status: 502 });
  }
}

export async function GET(req: Request, { params }: { params: Promise<{ action: string }> }) {
  const { action } = await params;
  if (!(action in GET_ACTIONS)) return Response.json({ detail: 'Not found' }, { status: 404 });
  return forward(req, action, 'GET');
}

export async function POST(req: Request, { params }: { params: Promise<{ action: string }> }) {
  const { action } = await params;
  if (!POST_ACTIONS.has(action)) return Response.json({ detail: 'Not found' }, { status: 404 });
  return forward(req, action, 'POST');
}
