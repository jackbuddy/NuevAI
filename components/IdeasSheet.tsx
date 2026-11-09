'use client';

import { useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';
import { IDEAS } from '@/lib/ideas';

/** "What can I ask?": a bottom sheet on phones, a centered panel on larger screens. Categories on top, real questions below. */
export function IdeasSheet({ open, onClose, onPick }: { open: boolean; onClose: () => void; onPick: (q: string, category: string) => void }) {
  const [cat, setCat] = useState(IDEAS[0].id);
  const panel = useRef<HTMLDivElement>(null);
  const closeBtn = useRef<HTMLButtonElement>(null);
  const group = IDEAS.find((g) => g.id === cat) ?? IDEAS[0];

  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement as HTMLElement | null;
    closeBtn.current?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); onClose(); return; }
      if (e.key !== 'Tab' || !panel.current) return;
      const f = [...panel.current.querySelectorAll<HTMLElement>('button')].filter((b) => !b.hasAttribute('disabled'));
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', key);
    return () => { document.removeEventListener('keydown', key); opener?.focus?.(); };
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center sm:items-center sm:p-6">
      <div className="absolute inset-0 bg-brand-950/30" onClick={onClose} aria-hidden />
      <div ref={panel} role="dialog" aria-modal="true" aria-labelledby="ideas-title"
           className="surface shadow-lift animate-rise relative flex max-h-[min(34rem,85dvh)] w-full max-w-lg flex-col rounded-t-3xl bg-white sm:rounded-3xl">
        <div className="flex items-center justify-between gap-3 px-5 pb-2 pt-4">
          <div>
            <h2 id="ideas-title" className="text-base font-bold text-brand-900">What can I ask?</h2>
            <p className="ink-2 text-xs">Tap one to ask it. Or type your own.</p>
          </div>
          <button ref={closeBtn} type="button" onClick={onClose} aria-label="Close ideas" className="tap-box h-11 w-11 shrink-0 rounded-full hover:bg-[var(--surface-2)]"><X className="h-4 w-4" aria-hidden /></button>
        </div>
        <div role="group" aria-label="Topics" className="no-scrollbar flex gap-1.5 overflow-x-auto px-5 pb-2">
          {IDEAS.map((g) => (
            <button key={g.id} type="button" aria-pressed={g.id === cat} onClick={() => setCat(g.id)}
                    className={`tap-h shrink-0 rounded-full px-3.5 py-1.5 text-[13px] font-semibold transition ${g.id === cat ? 'bg-brand-900 text-white' : 'surface-2 text-brand-800 hover:bg-brand-100'}`}>{g.label}</button>
          ))}
        </div>
        <ul aria-label={group.label} className="scroll-thin min-h-0 flex-1 overflow-y-auto px-3 pb-safe pt-1">
          {group.prompts.map((p) => (
            <li key={p}>
              <button type="button" onClick={() => onPick(p, group.label)}
                      className="tap-h wrap-any w-full rounded-2xl px-3 py-2.5 text-left text-[15px] font-medium text-brand-900 transition hover:bg-brand-50">{p}</button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
