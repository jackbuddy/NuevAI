'use client';

import { useEffect, useMemo, useState } from 'react';
import { ArrowUpRight, BookOpen, CalendarDays, ChevronDown, FileText, GraduationCap, Globe, LogIn, MapPin, MessageCircle, Search, Ticket, Users, X } from 'lucide-react';
import { RESOURCE_GROUPS, Resource } from '@/lib/resources';
import { track } from '@/lib/analytics';

interface Found { label: string; url: string; link_category: string }
const hostOf = (u: string) => { try { return new URL(u).host.replace(/^www\./, ''); } catch { return ''; } };
const norm = (u: string) => { try { const x = new URL(u); return x.host.replace(/^www\./, '') + x.pathname.replace(/\/$/, ''); } catch { return u; } };
const iconFor = (r: Resource) => (hostOf(r.url).includes('docs.google') ? FileText : r.badge === 'Booking' ? Ticket : Globe);

const TILE_ICON: Record<string, typeof FileText> = { 'Student & Family Handbook': BookOpen, '2026–27 Course Catalog': GraduationCap, 'Clubs & Affinity Groups list': Users, 'Athletics calendar': CalendarDays, 'Register for an admissions event': MapPin, 'Nexus (MySchoolApp)': LogIn };

// the handful of things most people come here for
const FEATURED = ['Student & Family Handbook', '2026–27 Course Catalog', 'Clubs & Affinity Groups list', 'Athletics calendar', 'Register for an admissions event', 'Nexus (MySchoolApp)'];
const SHORT: Record<string, string> = { 'Student & Family Handbook': 'Handbook', '2026–27 Course Catalog': 'Course catalog', 'Clubs & Affinity Groups list': 'Clubs', 'Athletics calendar': 'Athletics calendar', 'Register for an admissions event': 'Book a tour', 'Nexus (MySchoolApp)': 'Nexus sign in' };
// one restrained hue per topic, shared by the tiles, group headings and list icons
const HUE: Record<string, { soft: string; solid: string; text: string; dot: string }> = {
  start: { soft: 'bg-brand-100 text-brand-700', solid: 'group-hover:bg-brand-700 group-hover:text-white', text: 'text-brand-600', dot: 'bg-brand-500' },
  'student-life': { soft: 'bg-violet-100 text-violet-700', solid: 'group-hover:bg-violet-600 group-hover:text-white', text: 'text-violet-700', dot: 'bg-violet-500' },
  athletics: { soft: 'bg-teal-100 text-teal-700', solid: 'group-hover:bg-teal-600 group-hover:text-white', text: 'text-teal-700', dot: 'bg-teal-600' },
  academics: { soft: 'bg-sky-100 text-sky-700', solid: 'group-hover:bg-sky-600 group-hover:text-white', text: 'text-sky-700', dot: 'bg-sky-500' },
  families: { soft: 'bg-amber-100 text-amber-800', solid: 'group-hover:bg-amber-500 group-hover:text-white', text: 'text-amber-700', dot: 'bg-amber-500' },
  portals: { soft: 'bg-slate-100 text-slate-700', solid: 'group-hover:bg-slate-600 group-hover:text-white', text: 'text-slate-600', dot: 'bg-slate-400' },
};
const hueOf = (g?: string) => HUE[g ?? 'start'] ?? HUE.start;
const FILTERS = [{ id: 'all', label: 'All' }, ...RESOURCE_GROUPS.map((g) => ({ id: g.id, label: g.title.split(' & ')[0].replace('Start here', 'Essentials') }))];

export function ResourcesView({ onAsk }: { onAsk: (q: string) => void }) {
  const [q, setQ] = useState('');
  const [filter, setFilter] = useState('all');
  const [found, setFound] = useState<Found[]>([]);
  const [showFound, setShowFound] = useState(false);

  useEffect(() => { fetch('/api/links').then((r) => (r.ok ? r.json() : null)).then((j) => j?.links && setFound(j.links)).catch(() => {}); }, []);

  const all = useMemo(() => RESOURCE_GROUPS.flatMap((g) => g.items.map((i) => ({ ...i, group: g.id, groupTitle: g.title }))), []);
  const featured = useMemo(() => FEATURED.map((t) => all.find((r) => r.title === t)).filter(Boolean) as (Resource & { group: string })[], [all]);
  const needle = q.trim().toLowerCase();
  const searching = needle.length > 0;
  const groups = RESOURCE_GROUPS.filter((g) => filter === 'all' || g.id === filter);

  const known = useMemo(() => new Set(all.map((i) => norm(i.url))), [all]);
  const extra: Resource[] = useMemo(() => {
    const seen = new Set<string>();
    return found
      .filter((l) => ['booking', 'form', 'payment', 'document'].includes(l.link_category) && l.label && !known.has(norm(l.url)) && !seen.has(norm(l.url)) && seen.add(norm(l.url)))
      .slice(0, 30)
      .map((l) => ({ title: l.label.replace(/\s*\(opens in new window\/tab\)/, '').slice(0, 70), desc: hostOf(l.url), url: l.url }));
  }, [found, known]);

  // search covers the curated list and the extra links found on school pages; every word must match
  const hits = useMemo(() => {
    const words = needle.split(/\s+/).filter(Boolean);
    return [...all, ...extra].filter((r) => { const hay = `${r.title} ${r.desc} ${r.badge ?? ''}`.toLowerCase(); return words.every((w) => hay.includes(w)); });
  }, [all, extra, needle]);

  const open = (r: Resource, where: string) => track('resource_click', { label: r.title, group: where, host: hostOf(r.url) });

  return (
    <div className="scroll-thin min-h-0 flex-1 overflow-y-auto bg-gradient-to-b from-brand-50 via-white to-white">
      <div className="mx-auto w-full max-w-3xl px-4 pb-16 pt-10 md:px-6 md:pt-14">
        <h2 className="text-center text-3xl font-bold tracking-tight text-brand-900 md:text-4xl">Find a resource</h2>
        <div aria-hidden className="mx-auto mt-3 flex h-1 w-24 overflow-hidden rounded-full"><span className="flex-1 bg-brand-600" /><span className="flex-1 bg-sky-400" /><span className="flex-1 bg-teal-500" /><span className="flex-1 bg-amber-400" /></div>

        <div className="surface shadow-lift mx-auto mt-6 flex items-center gap-3 rounded-full px-5 py-3 min-h-12 transition focus-within:border-brand-400 focus-within:ring-4 focus-within:ring-brand-500/10">
          <Search className="ink-2 h-5 w-5 shrink-0" aria-hidden />
          <input value={q} onChange={(e) => setQ(e.target.value)} onBlur={() => q && track('resource_search', { q: q.slice(0, 40) })}
                 placeholder="Search handbooks, forms, clubs…" aria-label="Search resources" type="search" enterKeyHint="search"
                 className="bare tap-h w-full min-w-0 bg-transparent text-base outline-none sm:text-[15px] placeholder:text-[var(--ink-2)]" />
          {q && <button aria-label="Clear search" onClick={() => setQ('')} className="tap-box -mr-3 rounded-full"><X className="ink-2 h-4 w-4" aria-hidden /></button>}
        </div>

        {searching ? (
          <section className="mt-8" aria-live="polite">
            <p className="sr-only-live">{hits.length} result{hits.length === 1 ? '' : 's'}</p>
            {hits.length === 0 ? (
              <div className="py-10 text-center">
                <p className="wrap-any font-semibold">Nothing matches “{q}”</p>
                <button onClick={() => { track('resource_search_ask', { q: q.slice(0, 60) }); onAsk(q); }} className="tap-box mt-3 rounded-full bg-brand-900 px-5 py-2.5 text-sm font-semibold text-white hover:bg-brand-700">Ask NuevAI instead</button>
              </div>
            ) : <List items={hits} onAsk={onAsk} onOpen={(r) => open(r, 'search')} />}
          </section>
        ) : (
          <>
            <section className="mt-9">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                {featured.map((r) => {
                  const Icon = TILE_ICON[r.title] ?? iconFor(r);
                  return (
                    <a key={r.url} href={r.url} target="_blank" rel="noopener noreferrer" data-tracked="1" onClick={() => open(r, 'featured')}
                       className="surface shadow-soft group flex min-w-0 flex-col gap-3 rounded-2xl p-4 transition hover:-translate-y-0.5 hover:shadow-lift">
                      <span className={`grid h-10 w-10 place-items-center rounded-full transition ${hueOf(r.group).soft} ${hueOf(r.group).solid}`}><Icon className="h-[18px] w-[18px]" aria-hidden /></span>
                      <span className="wrap-any text-sm font-bold leading-tight">{SHORT[r.title] ?? r.title}</span>
                    </a>
                  );
                })}
              </div>
            </section>

            <div role="group" aria-label="Filter by topic" className="no-scrollbar -mx-4 mt-10 flex gap-2 overflow-x-auto px-4 md:mx-0 md:px-0">
              {FILTERS.map((f) => (
                <button key={f.id} onClick={() => { setFilter(f.id); track('resource_filter', { filter: f.id }); }} aria-pressed={filter === f.id}
                        className={`tap-h shrink-0 rounded-full px-4 py-1.5 text-[13px] font-semibold transition ${filter === f.id ? 'bg-brand-900 text-white' : 'surface-2 ink-2 hover:text-[var(--ink)]'}`}>
                  {f.label}
                </button>
              ))}
            </div>

            <div className="mt-6 space-y-8">
              {groups.map((g) => (
                <section key={g.id}>
                  <h3 className={`mb-2 flex items-center gap-2 px-1 text-xs font-bold uppercase tracking-wider ${hueOf(g.id).text}`}><span aria-hidden className={`h-2 w-2 rounded-full ${hueOf(g.id).dot}`} />{g.title}</h3>
                  <List items={g.items} group={g.id} onAsk={onAsk} onOpen={(r) => open(r, g.title)} />
                </section>
              ))}
            </div>

            {extra.length > 0 && filter === 'all' && (
              <section className="mt-10">
                <button onClick={() => { setShowFound(!showFound); track('resource_more_toggle', { open: !showFound }); }}
                        aria-expanded={showFound} className="ink-2 tap-h mx-auto flex items-center gap-1.5 text-[13px] font-semibold hover:text-[var(--ink)]">
                  {showFound ? 'Hide' : 'Show'} {extra.length} more links from school pages
                  <ChevronDown className={`h-4 w-4 transition ${showFound ? 'rotate-180' : ''}`} />
                </button>
                {showFound && <div className="mt-4"><List items={extra} onAsk={onAsk} onOpen={(r) => open(r, 'found')} /></div>}
              </section>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function List({ items, group, onAsk, onOpen }: { items: Resource[]; group?: string; onAsk: (q: string) => void; onOpen: (r: Resource) => void }) {
  return (
    <ul className="surface divide-y divide-[var(--line)] overflow-hidden rounded-2xl">
      {items.map((r) => {
        const Icon = iconFor(r);
        return (
          <li key={r.url} className="group flex items-center gap-2 py-1 pl-4 pr-2 transition hover:bg-[var(--surface-2)] md:gap-3 md:py-2">
            <span className={`grid h-7 w-7 shrink-0 place-items-center rounded-full ${hueOf((r as { group?: string }).group ?? group).soft}`}><Icon className="h-3.5 w-3.5" aria-hidden /></span>
            <a href={r.url} target="_blank" rel="noopener noreferrer" data-tracked="1" onClick={() => onOpen(r)} className="tap-h flex min-w-0 flex-1 flex-col justify-center py-1.5">
              <span className="block truncate text-sm font-semibold">{r.title}</span>
              <span className="ink-2 block truncate text-xs">{r.desc}</span>
            </a>
            {r.ask && (
              <button onClick={() => { track('resource_ask', { label: r.title }); onAsk(r.ask!); }} aria-label={`Ask NuevAI about ${r.title}`} title="Ask NuevAI"
                      className="ink-2 tap-box h-8 w-8 shrink-0 rounded-full transition hover:bg-white hover:text-brand-700">
                <MessageCircle className="h-4 w-4" aria-hidden />
              </button>
            )}
            <a href={r.url} target="_blank" rel="noopener noreferrer" data-tracked="1" onClick={() => onOpen(r)} aria-hidden tabIndex={-1}
               className="ink-2 tap-box h-8 w-8 shrink-0 rounded-full transition hover:bg-white hover:text-brand-700">
              <ArrowUpRight className="h-4 w-4" aria-hidden />
            </a>
          </li>
        );
      })}
    </ul>
  );
}
