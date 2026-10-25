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
  const panel = useRef<HTMLFormElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const close = () => { clearTimeout(closeTimer.current); setOpen(false); };
  useEffect(() => {
    if (!open) return;
    setSent(false);
    const f = setTimeout(() => ref.current?.focus(), 50);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';            // the page behind must not scroll
    const btn = opener.current;
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); close(); return; }
      if (e.key !== 'Tab' || !panel.current) return;     // keep Tab inside the dialog
      const items = [...panel.current.querySelectorAll<HTMLElement>('button, textarea, [href], input, select')].filter((x) => !(x as HTMLButtonElement).disabled && x.offsetParent !== null);
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      else if (!panel.current.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
    };
    window.addEventListener('keydown', key);
    return () => { clearTimeout(f); window.removeEventListener('keydown', key); document.body.style.overflow = prevOverflow; btn?.focus(); };
  }, [open]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (!t) return;
    track('feedback_text', { kind, text: t.slice(0, 600), tab });
    flush();
    setSent(true);
    setText('');
    closeTimer.current = setTimeout(() => setOpen(false), 1600);
  };

  return (
    <>
      <button ref={opener} onClick={() => { setOpen(true); track('feedback_open', { tab }); }} aria-haspopup="dialog" aria-label="Send feedback"
              className="ink-2 tap-box gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold transition hover:bg-[var(--surface-2)] hover:text-[var(--ink)]">
        <MessageSquarePlus className="h-3.5 w-3.5" aria-hidden /><span className="hidden sm:inline">Feedback</span>
      </button>
      {open && mounted && createPortal(
        <div className="fixed inset-0 z-50 flex items-end justify-center overflow-y-auto bg-brand-950/30 p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:items-center" onClick={close}>
          <form ref={panel} role="dialog" aria-modal="true" aria-labelledby="fb-title" onSubmit={submit} onClick={(e) => e.stopPropagation()} className="surface shadow-lift animate-rise max-h-full w-full max-w-md overflow-y-auto rounded-3xl bg-white p-5">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 id="fb-title" className="text-lg font-bold tracking-tight">Help improve NuevAI</h3>
                <p className="ink-2 mt-0.5 text-[13px]">NuevAI is in alpha. Tell us what was wrong, missing or great.</p>
              </div>
              <button type="button" onClick={close} aria-label="Close" className="ink-2 tap-box -mr-2 -mt-2 h-10 w-10 shrink-0 rounded-full hover:bg-[var(--surface-2)]"><X className="h-4 w-4" aria-hidden /></button>
            </div>
            {sent ? (
              <p role="status" className="mt-6 flex items-center gap-2 py-6 text-sm font-semibold text-brand-700"><Check className="h-4 w-4" />Thank you, that was sent.</p>
            ) : (
              <>
                <div className="mt-4 flex flex-wrap gap-1.5">
                  {KINDS.map((k) => (
                    <button type="button" key={k} onClick={() => setKind(k)} aria-pressed={kind === k}
                            className={`tap-h rounded-full px-3.5 py-1 text-xs font-semibold transition ${kind === k ? 'bg-brand-900 text-white' : 'surface-2 ink-2 hover:text-[var(--ink)]'}`}>{k}</button>
                  ))}
                </div>
                <textarea ref={ref} aria-label="Your feedback" value={text} onChange={(e) => setText(e.target.value)} maxLength={600} rows={4} required
                          placeholder="e.g. It said Block 3 is at 12:35 but my schedule shows…"
                          className="bare surface mt-3 w-full resize-none rounded-2xl p-3 text-base outline-none focus:border-brand-400 sm:text-sm focus:ring-4 focus:ring-brand-500/10" />
                <p className="ink-2 mt-2 text-[11px]">Please don’t include names or personal details. Feedback is anonymous.</p>
                <button type="submit" disabled={!text.trim()} className="tap-box mt-3 w-full rounded-full bg-brand-900 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-700 disabled:opacity-30">Send feedback</button>
              </>
            )}
          </form>
        </div>,
        document.body
      )}
    </>
  );
}
