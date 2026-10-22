import { NextResponse } from 'next/server';
import { BACKEND } from '@/lib/backend';

export const maxDuration = 60;
export const dynamic = 'force-dynamic';

/** Non-streaming fallback. The UI normally uses /api/chat/stream. */
export async function POST(req: Request) {
  try {
    const body = await req.json();
    const response = await fetch(`${BACKEND}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(58_000),
      cache: 'no-store',
    });
    if (!response.ok) {
      const errorData = await response.json().catch(() => null);
      return NextResponse.json({ error: errorData?.detail ?? `Backend returned ${response.status}` }, { status: response.status });
    }
    return NextResponse.json(await response.json());
  } catch (error: any) {
    const timedOut = error?.name === 'TimeoutError' || error?.name === 'AbortError';
    return NextResponse.json({ error: timedOut ? 'Backend timed out.' : error.message || 'Internal server error' }, { status: timedOut ? 504 : 500 });
  }
}
