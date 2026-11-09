'use client';

import { useEffect, useId, useState } from 'react';
import { track } from '@/lib/analytics';
import { WHATS_NEW } from '@/lib/whatsnew';
import { readVisit, updateUx, ux } from '@/lib/visit';
import { whatsNewActive } from '@/lib/ux-logic';

/**
 * One quiet line for people who used NuevAI before this release ("New · lunch menus and followable teams").
 * Opens into a short list, closes for good with "Got it". First-time visitors never see it: nothing is new to them.
 */
export function WhatsNew() {
  const [show, setShow] = useState(false);
  const [open, setOpen] = useState(false);
  const panel = useId();

  useEffect(() => {
    try {
      const v = readVisit();
      const u = ux();
      if (v.firstRun) { if (u.whatsNew.seenId !== WHATS_NEW.id) updateUx((x) => { x.whatsNew.seenId = WHATS_NEW.id; }); return; }
      const active = whatsNewActive({ id: WHATS_NEW.id, releasedAt: Date.parse(WHATS_NEW.releasedAt), seenId: u.whatsNew.seenId, shownCount: u.whatsNew.shownCount, returning: v.returning, now: Date.now() });
      if (!active) return;
      setShow(true);
      // count this page load once per page lifetime
      if (u.whatsNew.countedId !== `${WHATS_NEW.id}:${Math.floor(performance.timeOrigin)}`) updateUx((x) => { x.whatsNew.shownCount += 1; x.whatsNew.countedId = `${WHATS_NEW.id}:${Math.floor(performance.timeOrigin)}`; });
    } catch {}
  }, []);

  if (!show) return null;
  const close = () => { updateUx((x) => { x.whatsNew.seenId = WHATS_NEW.id; }); setShow(false); };

  return (
    <div className="animate-rise mt-7 text-center" style={{ animationDelay: '170ms' }}>
      <button type="button" aria-expanded={open} aria-controls={open ? panel : undefined}
              onClick={() => { if (!open) track('whatsnew_open', { id: WHATS_NEW.id }); setOpen((o) => !o); }}
              className="tap-h inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-[12px] font-medium text-brand-700 transition hover:bg-brand-100">
        <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-sky-500" />
        <span><b className="font-semibold text-brand-800">New</b> · {WHATS_NEW.line}</span>
      </button>
      {open && (
        <div id={panel} className="surface shadow-soft mx-auto mt-1 max-w-sm rounded-2xl p-4 text-left">
          <ul className="space-y-1.5 text-[13px] leading-snug text-brand-900">
            {WHATS_NEW.items.map((t) => <li key={t} className="flex gap-2"><span aria-hidden className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-brand-400" />{t}</li>)}
          </ul>
          <button type="button" onClick={close} className="tap-h mt-2 text-[12px] font-semibold text-brand-700 hover:underline">Got it</button>
        </div>
      )}
    </div>
  );
}
