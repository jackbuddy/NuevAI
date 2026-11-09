'use client';

import { Lightbulb, X } from 'lucide-react';
import { CAMPUSES, Campus } from '@/lib/chat';
import type { HintId } from '@/lib/ux-logic';

const COPY: Record<HintId, { text: string; cta?: string }> = {
  campus: { text: 'Want just your campus’s menu? It will show first on Home and in answers.' },
  games_calendar: { text: 'The Calendar has every game: add one to your calendar, or follow a team to see its next game on Home.', cta: 'Open calendar' },
  calendar_tab: { text: 'The Calendar tab has the full month, filters and search.', cta: 'Open calendar' },
  resources: { text: 'Forms, booking links and handbooks are collected in Resources.', cta: 'Open Resources' },
  ideas: { text: 'Not sure what else to ask? There’s a list of ideas.', cta: 'Browse ideas' },
};

/** One quiet, dismissible suggestion under the latest answer. The caller decides when (lib/ux-logic.ts chooseHint) and tracks it. */
export function Hint({ id, onAct, onCampus, onDismiss }: { id: HintId; onAct: () => void; onCampus: (c: Campus) => void; onDismiss: () => void }) {
  const c = COPY[id];
  return (
    <div role="note" className="surface-2 mt-3 flex items-start gap-2.5 rounded-2xl py-2 pl-3.5 pr-1 text-[13px] leading-snug text-brand-900 ml-[38px]">
      <Lightbulb className="mt-3 h-3.5 w-3.5 shrink-0 text-brand-500 max-sm:mt-3.5" aria-hidden />
      <div className="min-w-0 flex-1 py-2">
        <p>{c.text}</p>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {id === 'campus'
            ? CAMPUSES.map((k) => <button key={k.id} type="button" onClick={() => onCampus(k.id)} className="tap-box rounded-full bg-brand-900 px-3.5 py-1 text-[12px] font-semibold text-white hover:bg-brand-700">{k.label}</button>)
            : <button type="button" onClick={onAct} className="tap-box rounded-full bg-brand-900 px-3.5 py-1 text-[12px] font-semibold text-white hover:bg-brand-700">{c.cta}</button>}
        </div>
      </div>
      <button type="button" onClick={onDismiss} aria-label="Dismiss suggestion" className="tap-box h-11 w-11 shrink-0 rounded-full hover:bg-white/70"><X className="h-4 w-4" aria-hidden /></button>
    </div>
  );
}
