/**
 * Pure logic for answer feedback: reasons, fallback-reply detection and the event payloads.
 * No DOM, no storage, no imports (unit-tested with plain node: scripts/feedback-logic.test.mjs).
 *
 * Event contract (all go through lib/analytics.track, which stamps anon_id / session_id / audience):
 *   feedback         {mid, value:'up'|'down', src:'thumb', query, answer, cached, degraded, n_src, fbk?}      one tap; recorded immediately
 *   feedback_detail  {mid, value:'up'|'down', src:'thumb'|'check', reason?, comment?, query}               optional follow-up; the LATEST one per (anon_id, mid) wins
 *   answer_check     {mid, value:'yes'|'no', query, answer, cached, degraded, n_src, fbk}                   "Did this answer your question?" on fallback replies
 *   answer_fallback  {mid, query, answer, why, cached, degraded, n_src}                                     a fallback-style reply was shown (auto-flag; no user action)
 *   feedback_text    {kind, text, tab, mid?, lq?}                                                           header "Report a problem"; mid/lq name the answer on screen
 * `mid` is the assistant message id: it joins every later event to the original rating. The server's props limit is 2000 bytes of
 * ASCII-escaped JSON (analytics.MAX_PROPS_BYTES); fitProps keeps every event under FIT_BYTES by shortening the answer first.
 */

export type Reason = 'wrong' | 'outdated' | 'not_answered' | 'too_long' | 'other';
export const REASONS: { id: Reason; label: string }[] = [
  { id: 'wrong', label: 'Wrong' },
  { id: 'outdated', label: 'Outdated' },
  { id: 'not_answered', label: 'Didn’t answer my question' },
  { id: 'too_long', label: 'Too long' },
  { id: 'other', label: 'Other' },
];
export const isReason = (v: unknown): v is Reason => REASONS.some((r) => r.id === v);

export const MAX_COMMENT = 300;
export const MAX_ANSWER = 600;
export const MAX_QUERY = 200;
export const FIT_BYTES = 1800;   // server limit is 2000; leave room for the envelope fields the server adds (_aud, _why, _eid)

/** Truncate for storage: no NUL (Postgres jsonb rejects it), never end on half a surrogate pair (jsonb rejects a lone surrogate, which
 *  would make the whole analytics batch fail), collapsed whitespace. */
export function clip(s: unknown, n: number): string {
  let t = String(s ?? '').replace(/\u0000/g, '').replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  if (t.length > n) {
    t = t.slice(0, n);
    const last = t.charCodeAt(t.length - 1);
    if (last >= 0xd800 && last <= 0xdbff) t = t.slice(0, -1);
    t = t.trimEnd();
  }
  return t;
}

/** Size of the props as the server measures it: Python json.dumps with ensure_ascii (every non-ASCII char is a 6-byte \uXXXX escape). */
export function jsonBytes(v: unknown): number {
  const s = JSON.stringify(v) ?? '';
  let n = 0;
  for (let i = 0; i < s.length; i++) n += s.charCodeAt(i) > 127 ? 6 : 1;
  return n;
}

type P = Record<string, string | number | boolean | undefined>;
const dropUndef = (p: P): P => Object.fromEntries(Object.entries(p).filter(([, v]) => v !== undefined && v !== '')) as P;

/** Shorten the longest free-text fields until the event fits: answer first, then the question, never the ids or the reason. */
export function fitProps(props: P, limit = FIT_BYTES): P {
  const p = dropUndef(props);
  const shrink = (k: string, floor: number) => {
    while (jsonBytes(p) > limit && typeof p[k] === 'string' && (p[k] as string).length > floor) {
      p[k] = clip(p[k], Math.max(floor, Math.floor((p[k] as string).length * 0.8)));
    }
  };
  if (jsonBytes(p) <= limit) return p;
  shrink('answer', 120);
  shrink('query', 100);
  shrink('comment', 150);
  if (jsonBytes(p) > limit) delete p.answer;
  if (jsonBytes(p) > limit) delete p.comment;
  p.trunc = true;
  return p;
}

// ---------------------------------------------------------------- fallback-style replies

export interface AnswerLike {
  id: string; content?: string; query?: string; degraded?: boolean; cached?: boolean; error?: boolean; streaming?: boolean; stopped?: boolean;
  sources?: { type?: string }[]; ms?: number; totalMs?: number;
}
export type FallbackWhy = 'degraded' | 'no_sources' | 'hedge';

const HEDGE = [
  /\bI (?:don[’']t|do not) (?:have|know|see)\b/i,
  /\bI(?:[’']m| am) (?:not sure|not certain|unable to|not able to)\b/i,
  /\b(?:couldn[’']t|could not|can[’']t|cannot|can not|unable to) (?:find|locate|confirm|verify)\b/i,
  /\b(?:isn[’']t|is not|aren[’']t|are not|not) (?:in|available|listed|published|posted)\b/i,
  /\bnot on the school calendar\b/i,
  /\b(?:doesn[’']t|does not|don[’']t|do not) (?:say|list|mention|specify|include|show|have (?:that|any|a|an))\b/i,
  /\bno (?:information|details|record|data) (?:about|on|for|is)\b/i,
  /\bnothing (?:in|on) (?:the|my)\b/i,
];

/** Is this reply a hedge / redirect / degraded answer? Uses what the server already returns (degraded flag, sources, cached) plus the wording.
 *  Cached replies with no sources are canned (greetings, safety, privacy) and are not fallbacks. Good answers return null. */
export function fallbackWhy(m: AnswerLike): FallbackWhy | null {
  if (m.error || m.streaming || m.stopped || !m.content) return null;
  if (m.degraded) return 'degraded';
  if (!m.cached && (m.sources?.length ?? 0) === 0) return 'no_sources';
  const text = m.content;
  if (text.length <= 900 && HEDGE.some((r) => r.test(text))) return 'hedge';
  if (HEDGE.some((r) => r.test(text.slice(0, 260)))) return 'hedge';
  return null;
}

// ---------------------------------------------------------------- payloads

function base(m: AnswerLike, why?: FallbackWhy | null): P {
  return {
    mid: m.id, query: clip(m.query, MAX_QUERY), answer: clip(m.content, MAX_ANSWER), cached: !!m.cached, degraded: !!m.degraded,
    n_src: m.sources?.length ?? 0, ...(m.ms != null ? { first_ms: m.ms } : {}), ...(why ? { fbk: why } : {}),
  };
}

export const ratingProps = (m: AnswerLike, value: 'up' | 'down') => fitProps({ ...base(m, fallbackWhy(m)), value, src: 'thumb' });
export const checkProps = (m: AnswerLike, value: 'yes' | 'no') => fitProps({ ...base(m, fallbackWhy(m)), value });
export const fallbackProps = (m: AnswerLike, why: FallbackWhy) => fitProps({ ...base(m, why), why });
export const detailProps = (m: AnswerLike, d: { value: 'up' | 'down'; src: 'thumb' | 'check'; reason?: Reason; comment?: string }) =>
  fitProps({ mid: m.id, value: d.value, src: d.src, reason: d.reason, comment: clip(d.comment, MAX_COMMENT), query: clip(m.query, 120) });
