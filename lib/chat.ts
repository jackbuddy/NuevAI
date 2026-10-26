import { currentIds } from './analytics';

export interface Source { chunk_id?: string; title?: string | null; url?: string | null; similarity?: number; type?: string }
export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  sources?: Source[];
  cached?: boolean;
  degraded?: boolean;   // the model was unavailable: sources and facts only
  stopped?: boolean;
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
  onDone?: (d: { cached: boolean; degraded?: boolean; timings: Record<string, number>; suggestions?: Suggestion[] }) => void;
}

export const uid = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);

/** Streams an answer. Throws on transport/server failure so the caller can show a retry. */
const GRADE_KEY = 'nuevai.grade';
/** Optional 9-12 preference. Lives only in this browser's localStorage; it is sent with a question, never saved or logged. */
export function loadGrade(): number | null {
  try { const g = Number(localStorage.getItem(GRADE_KEY)); return g >= 9 && g <= 12 ? g : null; } catch { return null; }
}
export function saveGrade(g: number | null) {
  try { if (g) localStorage.setItem(GRADE_KEY, String(g)); else localStorage.removeItem(GRADE_KEY); } catch {}
}

export type Role = 'student' | 'parent' | 'staff';
const ROLE_KEY = 'nuevai.role';
/** Optional "I'm a…" preference. Browser-only, like the grade: it only changes which starter questions are offered. */
export function loadRole(): Role | null {
  try { const r = localStorage.getItem(ROLE_KEY); return r === 'student' || r === 'parent' || r === 'staff' ? r : null; } catch { return null; }
}
export function saveRole(r: Role | null) {
  try { if (r) localStorage.setItem(ROLE_KEY, r); else localStorage.removeItem(ROLE_KEY); } catch {}
}

export type Campus = 'san_mateo' | 'hillsborough';
export const CAMPUSES: { id: Campus; label: string }[] = [{ id: 'san_mateo', label: 'San Mateo' }, { id: 'hillsborough', label: 'Hillsborough' }];
const CAMPUS_KEY = 'nuevai.campus';
/** Optional campus preference. Browser-only; it is sent only so lunch answers come from the right menu, and never stored with an identity. */
export function loadCampus(): Campus | null {
  try { const c = localStorage.getItem(CAMPUS_KEY); return c === 'san_mateo' || c === 'hillsborough' ? c : null; } catch { return null; }
}
export function saveCampus(c: Campus | null) {
  try { if (c) localStorage.setItem(CAMPUS_KEY, c); else localStorage.removeItem(CAMPUS_KEY); } catch {}
}

export async function streamChat(query: string, history: { role: string; content: string }[], h: StreamHandlers, signal?: AbortSignal, grade?: number | null, campus?: Campus | null) {
  const res = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, history: history.slice(-4).map((t) => ({ role: t.role, content: t.content.slice(0, 700) })), ...(grade ? { grade } : {}), ...(campus ? { campus } : {}), ...currentIds() }),
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
