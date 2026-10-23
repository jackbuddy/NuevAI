'use client';

import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Check, MessageSquarePlus, X } from 'lucide-react';
import { flush, track } from '@/lib/analytics';

const KINDS = ['Wrong answer', 'Missing info', 'Idea', 'Bug', 'Other'];

/** Alpha feedback: a short note, tied to the anonymous visitor id like every other event. */
export function FeedbackButton({ tab }: { tab: string }) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState(KINDS[0]);
  const [text, setText] = useState('');
  const [sent, setSent] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  useEffect(() => { if (open) { setSent(false); setTimeout(() => ref.current?.focus(), 50); } }, [open]);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    window.addEventListener('keydown', esc);
    return () => window.removeEventListener('keydown', esc);
  }, []);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (!t) return;
    track('feedback_text', { kind, text: t.slice(0, 600), tab });
    flush();
    setSent(true);
    setText('');
    setTimeout(() => setOpen(false), 1600);
  };

  return (
    <>
      <button onClick={() => { setOpen(true); track('feedback_open', { tab }); }}
              className="ink-2 inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold transition hover:bg-[var(--surface-2)] hover:text-[var(--ink)]">
        <MessageSquarePlus className="h-3.5 w-3.5" /><span className="hidden sm:inline">Feedback</span>
      </button>
      {open && mounted && createPortal(
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-brand-950/30 p-3 sm:items-center" onClick={() => setOpen(false)} role="dialog" aria-modal="true" aria-label="Send feedback">
          <form onSubmit={submit} onClick={(e) => e.stopPropagation()} className="surface shadow-lift animate-rise w-full max-w-md rounded-3xl bg-white p-5">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 className="text-lg font-bold tracking-tight">Help improve NuevAI</h3>
                <p className="ink-2 mt-0.5 text-[13px]">NuevAI is in alpha. Tell us what was wrong, missing or great.</p>
              </div>
              <button type="button" onClick={() => setOpen(false)} aria-label="Close" className="ink-2 rounded-full p-1 hover:bg-[var(--surface-2)]"><X className="h-4 w-4" /></button>
            </div>
            {sent ? (
              <p className="mt-6 flex items-center gap-2 py-6 text-sm font-semibold text-brand-700"><Check className="h-4 w-4" />Thank you, that was sent.</p>
            ) : (
              <>
                <div className="mt-4 flex flex-wrap gap-1.5">
                  {KINDS.map((k) => (
                    <button type="button" key={k} onClick={() => setKind(k)} aria-pressed={kind === k}
                            className={`rounded-full px-3 py-1 text-xs font-semibold transition ${kind === k ? 'bg-brand-900 text-white' : 'surface-2 ink-2 hover:text-[var(--ink)]'}`}>{k}</button>
                  ))}
                </div>
                <textarea ref={ref} value={text} onChange={(e) => setText(e.target.value)} maxLength={600} rows={4} required
                          placeholder="e.g. It said Block 3 is at 12:35 but my schedule shows…"
                          className="surface mt-3 w-full resize-none rounded-2xl p-3 text-sm outline-none focus:border-brand-400 focus:ring-4 focus:ring-brand-500/10" />
                <p className="ink-2 mt-2 text-[11px]">Please don’t include names or personal details. Feedback is anonymous.</p>
                <button type="submit" disabled={!text.trim()} className="mt-3 w-full rounded-full bg-brand-900 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-700 disabled:opacity-30">Send feedback</button>
              </>
            )}
          </form>
        </div>,
        document.body
      )}
    </>
  );
}
