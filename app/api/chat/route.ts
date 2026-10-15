import { NextResponse } from 'next/server';

const BACKEND_URL = process.env.CHAT_API_URL ?? 'https://nuevai-backend.onrender.com/api/chat';

export const maxDuration = 60;
export const dynamic = 'force-dynamic';

export async function POST(req: Request) {
  try {
    const { query, top_k = 5 } = await req.json();

    const response = await fetch(BACKEND_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query,
        top_k,
        model: 'openai/gpt-oss-120b',
      }),
      signal: AbortSignal.timeout(55_000),
      cache: 'no-store',
    });

    if (!response.ok) {
      // Host error pages (e.g. Render's 502) are HTML, not JSON.
      const errorData = await response.json().catch(() => null);
      return NextResponse.json(
        { error: errorData?.detail ?? `Backend returned ${response.status}` },
        { status: response.status }
      );
    }

    const data = await response.json();
    return NextResponse.json(data);
  } catch (error: any) {
    const timedOut = error?.name === 'TimeoutError' || error?.name === 'AbortError';
    return NextResponse.json(
      { error: timedOut ? 'Backend timed out.' : error.message || 'Internal server error' },
      { status: timedOut ? 504 : 500 }
    );
  }
}
