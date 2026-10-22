'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { CalendarPlus, ChevronLeft, ChevronRight, Clock, MapPin, MessageCircle, Search, X, CalendarX2, Home, Plane } from 'lucide-react';
import { CATEGORIES, CalEvent, catMeta, downloadIcs, iso, onDay, parseISO, timeLabel } from '@/lib/calendar';
import { track } from '@/lib/analytics';

type View = 'month' | 'agenda';

export function CalendarView({ onAsk }: { onAsk: (q: string) => void }) {
  const todayIso = iso(new Date());
  const [cursor, setCursor] = useState(() => { const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), 1); });
  const [view, setView] = useState<View>('month');
  const [cats, setCats] = useState<Set<string>>(new Set());
  const [q, setQ] = useState('');
  const [practices, setPractices] = useState(false);
  const [selected, setSelected] = useState<string>(todayIso);
  const [events, setEvents] = useState<CalEvent[]>([]);
  const [state, setState] = useState<'loading' | 'ok' | 'error'>('loading');

  const grid = useMemo(() => {
    const first = new Date(cursor);
    const start = new Date(first); start.setDate(1 - first.getDay());
    const days: Date[] = [];
    for (let i = 0; i < 42; i++) { const d = new Date(start); d.setDate(start.getDate() + i); days.push(d); }
    return days;
  }, [cursor]);

  const load = useCallback(() => {
    setState('loading');
    const from = iso(grid[0]), to = iso(grid[41]);
    fetch(`/api/calendar?from=${from}&to=${to}&limit=600`)
      .then((r) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then((j) => { setEvents(j.events ?? []); setState('ok'); })
      .catch(() => setState('error'));
  }, [grid]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { track('calendar_month', { month: iso(cursor).slice(0, 7), view }); }, [cursor, view]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return events.filter((e) =>
      (practices || e.kind !== 'practice') &&
      (cats.size === 0 || cats.has(e.category)) &&
      (!needle || e.summary.toLowerCase().includes(needle) || (e.location ?? '').toLowerCase().includes(needle)));
  }, [events, cats, q, practices]);

  const byDay = useMemo(() => {
    const m = new Map<string, CalEvent[]>();
    for (const d of grid) { const k = iso(d); const list = filtered.filter((e) => onDay(e, k)); if (list.length) m.set(k, list); }
    return m;
  }, [grid, filtered]);

  const monthLabel = cursor.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
  const move = (delta: number) => { setCursor((c) => new Date(c.getFullYear(), c.getMonth() + delta, 1)); track('calendar_nav', { delta }); };
  const goToday = () => { const d = new Date(); setCursor(new Date(d.getFullYear(), d.getMonth(), 1)); setSelected(todayIso); track('calendar_today'); };
  const toggleCat = (id: string) => { setCats((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); track('calendar_filter', { category: id, on: n.has(id) }); return n; }); };

  const dayEvents = byDay.get(selected) ?? [];
  const agendaDays = [...byDay.entries()].filter(([k]) => parseISO(k).getMonth() === cursor.getMonth() && (cursor.getMonth() !== new Date().getMonth() || cursor.getFullYear() !== new Date().getFullYear() || k >= todayIso));

  return (
    <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-4 pb-10 pt-6 md:px-6">
        <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="text-sm font-medium text-brand-600 dark:text-brand-300">School calendar</p>
            <h2 className="font-display text-3xl font-semibold tracking-tight md:text-4xl">{monthLabel}</h2>
          </div>
          <div className="flex items-center gap-2">
            <div className="surface flex rounded-full p-0.5 text-xs font-semibold">
              {(['month', 'agenda'] as View[]).map((v) => (
                <button key={v} onClick={() => setView(v)} className={`rounded-full px-3.5 py-1.5 capitalize transition ${view === v ? 'bg-brand-700 text-white' : 'ink-2'}`}>{v}</button>
              ))}
            </div>
            <button onClick={goToday} className="surface rounded-full px-3.5 py-2 text-xs font-semibold hover:bg-[var(--surface-2)]">Today</button>
            <div className="surface flex rounded-full">
              <button aria-label="Previous month" onClick={() => move(-1)} className="rounded-l-full p-2 hover:bg-[var(--surface-2)]"><ChevronLeft className="h-4 w-4" /></button>
              <button aria-label="Next month" onClick={() => move(1)} className="rounded-r-full p-2 hover:bg-[var(--surface-2)]"><ChevronRight className="h-4 w-4" /></button>
            </div>
          </div>
        </div>

        <div className="mb-4 flex flex-wrap items-center gap-2">
          <div className="surface flex min-w-[12rem] flex-1 items-center gap-2 rounded-full px-3.5 py-2 sm:max-w-xs">
            <Search className="ink-2 h-4 w-4" />
            <input value={q} onChange={(e) => setQ(e.target.value)} onBlur={() => q && track('calendar_search', { q: q.slice(0, 40) })} placeholder="Search events or places" aria-label="Search events"
                   className="w-full bg-transparent text-sm outline-none placeholder:text-[var(--ink-2)]" />
            {q && <button aria-label="Clear" onClick={() => setQ('')}><X className="h-3.5 w-3.5" /></button>}
          </div>
          <div className="no-scrollbar -mx-1 flex gap-1.5 overflow-x-auto px-1">
            {CATEGORIES.filter((c) => c.id !== 'other').map((c) => {
              const on = cats.has(c.id);
              return (
                <button key={c.id} onClick={() => toggleCat(c.id)} aria-pressed={on}
                        className={`flex shrink-0 items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-semibold transition ${on ? 'border-transparent bg-brand-700 text-white' : 'surface ink-2 hover:bg-[var(--surface-2)]'}`}>
                  <span className={`h-2 w-2 rounded-full ${c.dot}`} />{c.label}
                </button>
              );
            })}
          </div>
          <label className="ink-2 ml-auto flex cursor-pointer items-center gap-2 text-xs font-medium">
            <input type="checkbox" checked={practices} onChange={(e) => { setPractices(e.target.checked); track('calendar_practices', { on: e.target.checked }); }} className="accent-[var(--color-brand-600)]" />
            Show practices
          </label>
        </div>

        {state === 'error' && (
          <div className="surface rounded-2xl p-8 text-center">
            <CalendarX2 className="ink-2 mx-auto mb-3 h-8 w-8" />
            <p className="font-semibold">The calendar couldn’t load</p>
            <p className="ink-2 mt-1 text-sm">The server may be waking up. Give it a few seconds.</p>
            <button onClick={load} className="mt-4 rounded-full bg-brand-700 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-600">Try again</button>
          </div>
        )}

        {state !== 'error' && view === 'month' && (
          <div className="grid gap-5 lg:grid-cols-[1fr_20rem]">
            <div className="surface shadow-soft overflow-hidden rounded-3xl">
              <div className="grid grid-cols-7 border-b hairline text-center text-[11px] font-semibold uppercase tracking-wider ink-2">
                {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((d) => <div key={d} className="py-2">{d}</div>)}
              </div>
              <div className={`grid grid-cols-7 ${state === 'loading' ? 'opacity-50' : ''}`}>
                {grid.map((d) => {
                  const k = iso(d), list = byDay.get(k) ?? [], inMonth = d.getMonth() === cursor.getMonth(), isToday = k === todayIso, isSel = k === selected;
                  return (
                    <button key={k} onClick={() => { setSelected(k); track('calendar_day', { day: k, events: list.length }); }}
                            className={`relative min-h-[4.25rem] border-b border-r hairline p-1.5 text-left transition hover:bg-[var(--surface-2)] md:min-h-[6rem] ${!inMonth ? 'opacity-40' : ''} ${isSel ? 'bg-brand-500/10 ring-2 ring-inset ring-brand-500' : ''}`}>
                      <span className={`grid h-6 w-6 place-items-center rounded-full text-xs font-semibold ${isToday ? 'bg-brand-700 text-white' : ''}`}>{d.getDate()}</span>
                      <div className="mt-1 hidden space-y-0.5 md:block">
                        {list.slice(0, 2).map((e, i) => (
                          <div key={i} className={`truncate rounded px-1 py-0.5 text-[10px] font-medium leading-tight ${catMeta(e.category).chip}`}>{e.summary.trim()}</div>
                        ))}
                        {list.length > 2 && <div className="ink-2 px-1 text-[10px] font-medium">+{list.length - 2} more</div>}
                      </div>
                      <div className="mt-1 flex flex-wrap gap-0.5 md:hidden">
                        {list.slice(0, 4).map((e, i) => <span key={i} className={`h-1.5 w-1.5 rounded-full ${catMeta(e.category).dot}`} />)}
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>

            <aside className="surface shadow-soft h-fit rounded-3xl p-4 lg:sticky lg:top-4">
              <p className="text-xs font-semibold uppercase tracking-wider text-brand-600 dark:text-brand-300">{selected === todayIso ? 'Today' : 'Selected day'}</p>
              <h3 className="font-display mb-3 text-xl font-semibold">{parseISO(selected).toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })}</h3>
              {dayEvents.length === 0 ? <Empty practicesHidden={!practices && events.some((e) => onDay(e, selected) && e.kind === 'practice')} /> :
                <div className="space-y-2.5">{dayEvents.map((e, i) => <EventCard key={i} e={e} onAsk={onAsk} />)}</div>}
            </aside>
          </div>
        )}

        {state !== 'error' && view === 'agenda' && (
          <div className="space-y-6">
            {state === 'ok' && agendaDays.length === 0 && <div className="surface rounded-2xl p-8"><Empty /></div>}
            {agendaDays.map(([k, list]) => (
              <section key={k}>
                <h3 className="mb-2 flex items-baseline gap-2 text-sm font-semibold">
                  <span className={k === todayIso ? 'text-brand-600 dark:text-brand-300' : ''}>{parseISO(k).toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })}</span>
                  {k === todayIso && <span className="rounded-full bg-brand-700 px-2 py-0.5 text-[10px] text-white">Today</span>}
                </h3>
                <div className="grid gap-2.5 md:grid-cols-2">{list.map((e, i) => <EventCard key={i} e={e} onAsk={onAsk} />)}</div>
              </section>
            ))}
          </div>
        )}
        {state === 'ok' && events.length === 0 && (
          <p className="ink-2 mt-6 text-center text-sm">No events found for this month yet.</p>
        )}
      </div>
    </div>
  );
}

function Empty({ practicesHidden }: { practicesHidden?: boolean }) {
  return (
    <div className="py-4 text-center">
      <CalendarX2 className="ink-2 mx-auto mb-2 h-6 w-6" />
      <p className="text-sm font-medium">Nothing scheduled</p>
      {practicesHidden && <p className="ink-2 mt-1 text-xs">Practices are hidden — turn on “Show practices”.</p>}
    </div>
  );
}

function EventCard({ e, onAsk }: { e: CalEvent; onAsk: (q: string) => void }) {
  const cat = catMeta(e.category);
  return (
    <div className="surface rounded-2xl p-3.5">
      <div className="flex items-start gap-2.5">
        <span className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${cat.dot}`} />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold leading-snug">{e.summary.trim()}</p>
          <div className="ink-2 mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            <span className="inline-flex items-center gap-1"><Clock className="h-3 w-3" />{e.end_date > e.event_date ? e.when : timeLabel(e)}</span>
            {e.location && <span className="inline-flex items-center gap-1"><MapPin className="h-3 w-3" />{e.location}</span>}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${cat.chip}`}>{cat.label}</span>
            {e.kind === 'game' && <span className="rounded-full bg-orange-500/10 px-2 py-0.5 text-[10px] font-semibold text-orange-700 dark:text-orange-300">Game</span>}
            {e.home_away === 'home' && <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-semibold text-emerald-700 dark:text-emerald-300"><Home className="h-2.5 w-2.5" />Home</span>}
            {e.home_away === 'away' && <span className="inline-flex items-center gap-1 rounded-full bg-slate-500/10 px-2 py-0.5 text-[10px] font-semibold"><Plane className="h-2.5 w-2.5" />Away</span>}
          </div>
          {e.description && <p className="ink-2 mt-2 text-xs leading-relaxed">{e.description}</p>}
          <div className="mt-2.5 flex gap-1.5">
            <button onClick={() => { downloadIcs(e); track('calendar_add', { summary: e.summary.slice(0, 60) }); }} className="surface-2 inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-semibold hover:bg-brand-500/10"><CalendarPlus className="h-3 w-3" />Add to calendar</button>
            <button onClick={() => { track('calendar_ask', { summary: e.summary.slice(0, 60) }); onAsk(`Tell me about "${e.summary.trim()}" on ${e.when}`); }} className="surface-2 inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-semibold hover:bg-brand-500/10"><MessageCircle className="h-3 w-3" />Ask NuevAI</button>
          </div>
        </div>
      </div>
    </div>
  );
}
