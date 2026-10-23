import { currentIds } from './analytics';

export interface Source { chunk_id?: string; title?: string | null; url?: string | null; similarity?: number; type?: string }
export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  sources?: Source[];
  cached?: boolean;
  error?: boolean;
  streaming?: boolean;
  ms?: number;          // time to first words
  totalMs?: number;
  query?: string;       // for assistant messages: the question it answers
  feedback?: 'up' | 'down';
  suggestions?: Suggestion[];
}

export interface Suggestion { label: string; query?: string; action?: 'calendar' }

export interface StreamHandlers {
  onMeta?: (m: { sources: Source[]; cached: boolean }) => void;
  onDelta: (text: string) => void;
  onDone?: (d: { cached: boolean; timings: Record<string, number>; suggestions?: Suggestion[] }) => void;
}

export const uid = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);

/** Streams an answer. Throws on transport/server failure so the caller can show a retry. */
export async function streamChat(query: string, history: { role: string; content: string }[], h: StreamHandlers, signal?: AbortSignal) {
  const res = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, history: history.slice(-6), ...currentIds() }),
    signal,
  });
  if (!res.ok || !res.body) {
    const j = await res.json().catch(() => null);
    throw new Error(j?.error ?? `The assistant returned an error (${res.status}).`);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  let gotDone = false;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const frame = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const ev = /^event: (.*)$/m.exec(frame)?.[1];
      const data = /^data: (.*)$/m.exec(frame)?.[1];
      if (!ev || !data) continue;
      const payload = JSON.parse(data);
      if (ev === 'meta') h.onMeta?.(payload);
      else if (ev === 'delta') h.onDelta(payload.t);
      else if (ev === 'done') { gotDone = true; h.onDone?.(payload); }
      else if (ev === 'error') throw new Error(payload.message || 'The assistant ran into a problem.');
    }
  }
  if (!gotDone) throw new Error('The connection dropped before the answer finished.');
}
