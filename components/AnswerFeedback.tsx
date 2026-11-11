'use client';

import { useEffect, useRef, useState } from 'react';
import { Check, ThumbsDown, ThumbsUp } from 'lucide-react';
import { track } from '@/lib/analytics';
import { MAX_COMMENT, REASONS, Reason, checkProps, detailProps, fallbackWhy, ratingProps } from '@/lib/feedback-logic';
import type { Message } from '@/lib/chat';

/**
 * Rating controls for one finished answer.
 *  - normal answer:   "Was this helpful?  Yes / No". Yes is one tap (an optional note can follow); No is recorded at once and then offers
 *                     optional reason chips + a short comment inline (no modal).
 *  - fallback reply:  "Did this answer your question?  Yes / Not really" instead; Not really opens the same chips with "Didn't answer my question" preselected.
 * All events carry the message id so a later reason/comment joins the original rating (see lib/feedback-logic.ts for the contract).
 */
export type FbPatch = Partial<Pick<Message, 'feedback' | 'fbReason' | 'fbDone' | 'check'>>;

const btn = 'tap-box h-9 gap-1.5 rounded-full border px-3.5 text-[13px] font-semibold transition focus-visible:outline-2';
const idle = 'border-[var(--line)] bg-white text-brand-800 hover:border-brand-300 hover:bg-brand-50';
const on = 'border-brand-900 bg-brand-900 text-white';

export function AnswerFeedback({ m, onPatch }: { m: Message; onPatch: (p: FbPatch) => void }) {
  const why = fallbackWhy(m);
  const mode: 'check' | 'rate' = why ? 'check' : 'rate';
  const [reason, setReason] = useState<Reason | undefined>(m.fbReason as Reason | undefined);
  const [comment, setComment] = useState('');
  const [note, setNote] = useState(false);          // thumbs-up: the optional one-line note is open
  const panelRef = useRef<HTMLDivElement>(null);
  const negative = m.feedback === 'down' || m.check === 'no';
  const positive = m.feedback === 'up' || m.check === 'yes';
  const src: 'thumb' | 'check' = m.check ? 'check' : 'thumb';
  const open = (negative || (positive && note)) && !m.fbDone;

  // bring a freshly opened panel into view above the composer (phones), without moving anything above it
  useEffect(() => { if (open) panelRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }, [open]);

  const rate = (v: 'up' | 'down') => {
    if (m.feedback === v) return;
    track('feedback', ratingProps(m, v));
    setReason(undefined); setComment(''); setNote(false);
    onPatch({ feedback: v, fbReason: undefined, fbDone: v === 'up' });
  };
  const answer = (v: 'yes' | 'no') => {
    if (m.check === v) return;
    track('answer_check', checkProps(m, v));
    setNote(false); setComment('');
    if (v === 'no') { setReason('not_answered'); track('feedback_detail', detailProps(m, { value: 'down', src: 'check', reason: 'not_answered' })); }
    else setReason(undefined);
    onPatch({ check: v, fbReason: v === 'no' ? 'not_answered' : undefined, fbDone: v === 'yes' });
  };
  const pick = (r: Reason) => {
    const next = reason === r ? undefined : r;
    setReason(next);
    onPatch({ fbReason: next });
    if (next) track('feedback_detail', detailProps(m, { value: 'down', src, reason: next }));
  };
  const send = () => {
    const text = comment.trim();
    if (negative) { if (text || reason) track('feedback_detail', detailProps(m, { value: 'down', src, reason, comment: text })); }
    else if (text) track('feedback_detail', detailProps(m, { value: 'up', src, comment: text }));
    onPatch({ fbDone: true });
  };
  const skip = () => onPatch({ fbDone: true });

  return (
    <div className="mt-1.5">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5" role="group" aria-label={mode === 'check' ? 'Did this answer your question?' : 'Rate this answer'}>
        <span className={`ink-2 text-[12px] font-medium ${mode === 'check' ? 'max-sm:basis-full' : ''}`}>{mode === 'check' ? 'Did this answer your question?' : 'Was this helpful?'}</span>
        {mode === 'check' ? (
          <>
            <button type="button" onClick={() => answer('yes')} aria-pressed={m.check === 'yes'} className={`${btn} ${m.check === 'yes' ? on : idle}`}><Check className="h-3.5 w-3.5" aria-hidden />Yes</button>
            <button type="button" onClick={() => answer('no')} aria-pressed={m.check === 'no'} className={`${btn} ${m.check === 'no' ? on : idle}`}><ThumbsDown className="h-3.5 w-3.5" aria-hidden />Not really</button>
          </>
        ) : (
          <>
            <button type="button" onClick={() => rate('up')} aria-pressed={m.feedback === 'up'} aria-label="Yes, helpful" className={`${btn} ${m.feedback === 'up' ? on : idle}`}><ThumbsUp className="h-3.5 w-3.5" aria-hidden />Yes</button>
            <button type="button" onClick={() => rate('down')} aria-pressed={m.feedback === 'down'} aria-label="No, not helpful" className={`${btn} ${m.feedback === 'down' ? on : idle}`}><ThumbsDown className="h-3.5 w-3.5" aria-hidden />No</button>
          </>
        )}
      </div>

      <div ref={panelRef} aria-live="polite" className="scroll-mb-24">
        {m.fbDone && (positive || negative) && (
          <p className="ink-2 mt-2 flex items-center gap-1.5 text-[12px]">
            <Check className="h-3.5 w-3.5 text-brand-500" aria-hidden />{negative ? 'Thanks, this helps us fix it.' : 'Thanks!'}
            {positive && !note && m.feedback === 'up' && <button type="button" onClick={() => { onPatch({ fbDone: false }); setNote(true); }} className="tap-h font-semibold text-brand-600 hover:underline">Add a note</button>}
          </p>
        )}
        {open && (
          <div role="group" aria-label={negative ? 'Tell us what went wrong' : 'Add a note'} className="surface mt-2 rounded-2xl p-3"
               onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); skip(); } }}>
            {negative ? (
              <>
                <p className="text-[13px] font-semibold text-brand-900">What went wrong? <span className="ink-2 font-normal">(optional)</span></p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {REASONS.map((r) => (
                    <button key={r.id} type="button" onClick={() => pick(r.id)} aria-pressed={reason === r.id}
                            className={`tap-box h-9 rounded-full border px-3 text-[13px] font-medium transition ${reason === r.id ? on : idle}`}>{r.label}</button>
                  ))}
                </div>
              </>
            ) : <p className="text-[13px] font-semibold text-brand-900">Anything to add? <span className="ink-2 font-normal">(optional)</span></p>}
            <textarea value={comment} onChange={(e) => setComment(e.target.value.slice(0, MAX_COMMENT))} maxLength={MAX_COMMENT} rows={negative ? 2 : 1}
                      aria-label={negative ? 'Optional comment about what went wrong' : 'Optional note'}
                      placeholder={negative ? 'Tell us more, e.g. what the right answer is' : 'One line is plenty'}
                      className="bare mt-2 block w-full resize-none rounded-xl border border-[var(--line)] bg-white px-3 py-2 text-base outline-none focus:border-brand-400 focus:ring-4 focus:ring-brand-500/10 sm:text-sm" />
            <div className="mt-2 flex items-center justify-end gap-2">
              <span className="ink-2 mr-auto text-[11px] tabular-nums" aria-hidden>{comment.length > 200 ? `${comment.length}/${MAX_COMMENT}` : ''}</span>
              <button type="button" onClick={skip} className="tap-box h-9 rounded-full px-3 text-[13px] font-semibold text-brand-700 hover:bg-brand-100">Skip</button>
              <button type="button" onClick={send} className="tap-box h-9 rounded-full bg-brand-900 px-4 text-[13px] font-semibold text-white hover:bg-brand-700">Send</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
