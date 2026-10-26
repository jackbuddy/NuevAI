'use client';

import { useEffect, useMemo, useState } from 'react';
import { CalendarX2, Sparkles, Trophy, UtensilsCrossed } from 'lucide-react';
import { CalEvent, iso, nowLA, parseISO, timeLabel } from '@/lib/calendar';
import { CAMPUSES, Campus } from '@/lib/chat';
import { track } from '@/lib/analytics';

interface Lunch { items?: string[]; available?: boolean; month_available?: boolean; month_label?: string; closed_reason?: string | null }

const addDays = (d: string, n: number) => { const x = parseISO(d); x.setDate(x.getDate() + n); return iso(x); };
const dayLabel = (d: string, today: string) =>
  d === today ? 'Today' : d === addDays(today, 1) ? 'Tomorrow' : parseISO(d).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
const byTime = (a: CalEvent, b: CalEvent) => a.event_date.localeCompare(b.event_date) || (a.start_time ?? '').localeCompare(b.start_time ?? '');

/** What is going on that people might not already know about: the next games, programming and today's lunch. */
export function Happening({ campus, onCampus, onOpenCalendar, onAsk }: {
  campus: Campus | null; onCampus: (c: Campus) => void; onOpenCalendar: () => void; onAsk: (q: string) => void;
}) {
  const [today] = useState(() => nowLA().date);
  const [events, setEvents] = useState<CalEvent[] | null>(null);
  const [lunch, setLunch] = useState<Lunch | null | undefined>(undefined);   // undefined: loading, null: unavailable

  useEffect(() => {
    const ac = new AbortController();
    fetch(`/api/calendar?from=${today}&to=${addDays(today, 7)}&limit=300`, { signal: ac.signal }).then((r) => (r.ok ? r.json() : null)).then((j) => setEvents(j?.events ?? [])).catch(() => {});
    return () => ac.abort();
  }, [today]);

  useEffect(() => {
    if (!campus) { setLunch(undefined); return; }
    const ac = new AbortController();
    setLunch(undefined);
    fetch(`/api/lunch?campus=${campus}&date=${today}`, { signal: ac.signal }).then((r) => (r.ok ? r.json() : null)).then((j) => setLunch(j && !j.error ? j : null)).catch(() => {});
    return () => ac.abort();
  }, [campus, today]);

  const { noSchool, games, programs } = useMemo(() => {
    const ev = (events ?? []).filter((e) => e.event_date <= addDays(today, 7));
    const upcoming = (list: CalEvent[]) => {
      const sorted = [...list].sort(byTime);
      const first = sorted[0]?.event_date;   // today's items if there are any, otherwise the next day that has some
      return first ? sorted.filter((e) => e.event_date === first).slice(0, 2) : [];
    };
    return {
      noSchool: ev.find((e) => e.category === 'no_school' && e.event_date <= today && e.end_date >= today) ?? null,
      games: upcoming(ev.filter((e) => e.kind === 'game')),
      programs: upcoming(ev.filter((e) => e.kind === 'event' && e.category !== 'athletics' && e.category !== 'no_school')),
    };
  }, [events, today]);

  const campusName = CAMPUSES.find((c) => c.id === campus)?.label;
  const card = 'surface shadow-soft group flex min-w-[15.5rem] flex-1 snap-start flex-col gap-2 rounded-2xl p-3.5 text-left transition hover:-translate-y-0.5 hover:shadow-lift sm:min-w-0';
  const chip = (cls: string) => `grid h-8 w-8 shrink-0 place-items-center rounded-full ${cls}`;
  const label = 'text-[11px] font-bold uppercase tracking-wider';

  const lunchBody = (() => {
    if (!campus) {
      return (
        <div className="space-y-2">
          <p className="ink-2 text-xs">Which campus?</p>
          <div className="flex flex-wrap gap-1.5">
            {CAMPUSES.map((c) => (
              <button key={c.id} type="button" onClick={() => { track('happening_campus', { campus: c.id }); onCampus(c.id); }}
                      className="tap-h rounded-full bg-amber-100 px-3 py-1.5 text-xs font-semibold text-amber-900 transition hover:bg-amber-200">{c.label}</button>
            ))}
          </div>
        </div>
      );
    }
    if (lunch === undefined) return <div className="space-y-1.5" aria-busy><div className="shimmer h-3 w-3/4 rounded-full" /><div className="shimmer h-3 w-1/2 rounded-full" /></div>;
    if (lunch === null) return <p className="ink-2 text-xs">Menu unavailable right now.</p>;
    if (lunch.available && lunch.items?.length) {
      return <p className="wrap-any text-sm font-semibold leading-snug">{lunch.items.slice(0, 4).join(' · ')}</p>;
    }
    return <p className="ink-2 text-xs">{lunch.closed_reason || (lunch.month_available === false ? `The ${lunch.month_label ?? 'new'} menu isn’t posted yet.` : 'No menu listed for today.')}</p>;
  })();

  const cards: React.ReactNode[] = [];

  if (noSchool) {
    cards.push(
      <button key="noschool" type="button" onClick={() => { track('happening_click', { card: 'no_school' }); onOpenCalendar(); }} className={card}>
        <span className="flex items-center gap-2"><span className={chip('bg-rose-100 text-rose-700')}><CalendarX2 className="h-4 w-4" aria-hidden /></span><span className={`${label} text-rose-700`}>No school today</span></span>
        <span className="wrap-any text-sm font-semibold leading-snug">{noSchool.summary.trim()}</span>
      </button>,
    );
  }

  if (games.length) {
    cards.push(
      <button key="games" type="button" onClick={() => { track('happening_click', { card: 'games' }); onOpenCalendar(); }} className={card}>
        <span className="flex items-center gap-2"><span className={chip('bg-teal-100 text-teal-700')}><Trophy className="h-4 w-4" aria-hidden /></span><span className={`${label} text-teal-700`}>Games · {dayLabel(games[0].event_date, today)}</span></span>
        <span className="space-y-1">
          {games.map((e, i) => (
            <span key={i} className="block"><span className="wrap-any block text-sm font-semibold leading-snug">{e.summary.trim()}</span>
              <span className="ink-2 block text-xs">{timeLabel(e)}{e.home_away === 'home' ? ' · Home' : e.home_away === 'away' ? ' · Away' : ''}</span></span>
          ))}
        </span>
      </button>,
    );
  }

  cards.push(
    <div key="lunch" className={`${card} cursor-default hover:translate-y-0 hover:shadow-soft`}>
      <button type="button" onClick={() => { track('happening_click', { card: 'lunch' }); onAsk('What’s for lunch today?'); }} className="flex items-center gap-2 text-left">
        <span className={chip('bg-amber-100 text-amber-800')}><UtensilsCrossed className="h-4 w-4" aria-hidden /></span>
        <span className={`${label} text-amber-800`}>Lunch{campusName ? ` · ${campusName}` : ''}</span>
      </button>
      {lunchBody}
    </div>,
  );

  if (programs.length) {
    cards.push(
      <button key="programs" type="button" onClick={() => { track('happening_click', { card: 'programs' }); onOpenCalendar(); }} className={card}>
        <span className="flex items-center gap-2"><span className={chip('bg-violet-100 text-violet-700')}><Sparkles className="h-4 w-4" aria-hidden /></span><span className={`${label} text-violet-700`}>On campus · {dayLabel(programs[0].event_date, today)}</span></span>
        <span className="space-y-1">
          {programs.map((e, i) => (
            <span key={i} className="block"><span className="wrap-any block text-sm font-semibold leading-snug">{e.summary.trim()}</span>
              <span className="ink-2 block text-xs">{timeLabel(e)}{e.location ? ` · ${e.location}` : ''}</span></span>
          ))}
        </span>
      </button>,
    );
  }

  return (
    <section aria-label="Happening" className="animate-rise mt-9" style={{ animationDelay: '200ms' }}>
      <h2 className="mb-2.5 px-1 text-center text-[11px] font-bold uppercase tracking-[0.14em] text-brand-500">Happening</h2>
      <div className="no-scrollbar -mx-4 flex snap-x gap-3 overflow-x-auto px-4 pb-1 sm:mx-0 sm:grid sm:grid-cols-[repeat(auto-fit,minmax(0,1fr))] sm:overflow-visible sm:px-0">{cards}</div>
    </section>
  );
}
