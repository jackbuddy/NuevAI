'use client';

import { useEffect, useMemo, useState } from 'react';
import { ArrowUpRight, FileText, Globe, MessageCircle, Search, Ticket, X } from 'lucide-react';
import { RESOURCE_GROUPS, Resource } from '@/lib/resources';
import { track } from '@/lib/analytics';

interface Found { label: string; url: string; link_category: string }
const hostOf = (u: string) => { try { return new URL(u).host.replace(/^www\./, ''); } catch { return ''; } };
const norm = (u: string) => { try { const x = new URL(u); return x.host.replace(/^www\./, '') + x.pathname.replace(/\/$/, ''); } catch { return u; } };

export function ResourcesView({ onAsk }: { onAsk: (q: string) => void }) {
  const [q, setQ] = useState('');
  const [found, setFound] = useState<Found[]>([]);

  useEffect(() => {
    fetch('/api/links').then((r) => (r.ok ? r.json() : null)).then((j) => j?.links && setFound(j.links)).catch(() => {});
  }, []);

  const needle = q.trim().toLowerCase();
  const match = (r: Resource) => !needle || `${r.title} ${r.desc} ${r.badge ?? ''}`.toLowerCase().includes(needle);

  const known = useMemo(() => new Set(RESOURCE_GROUPS.flatMap((g) => g.items.map((i) => norm(i.url)))), []);
  const extra: Resource[] = useMemo(() => {
    const seen = new Set<string>();
    return found
      .filter((l) => ['booking', 'form', 'payment', 'document'].includes(l.link_category) && l.label && !known.has(norm(l.url)) && !seen.has(norm(l.url)) && seen.add(norm(l.url)))
      .slice(0, 24)
      .map((l) => ({ title: l.label.replace(/\s*\(opens in new window\/tab\)/, '').slice(0, 70), desc: hostOf(l.url), url: l.url, badge: l.link_category === 'booking' ? 'Booking' : l.link_category === 'form' ? 'Form' : l.link_category === 'payment' ? 'Payment' : 'Document' }));
  }, [found, known]);

  const groups = RESOURCE_GROUPS.map((g) => ({ ...g, items: g.items.filter(match) })).filter((g) => g.items.length);
  const extraShown = extra.filter(match);

  return (
    <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-4 pb-12 pt-6 md:px-6">
        <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-sm font-medium text-brand-600 dark:text-brand-300">Resources</p>
            <h2 className="font-display text-3xl font-semibold tracking-tight md:text-4xl">Everything in one place</h2>
            <p className="ink-2 mt-1 text-sm">Handbooks, catalogs, forms and portals — open them, or ask NuevAI about what’s inside.</p>
          </div>
          <div className="surface flex w-full items-center gap-2 rounded-full px-4 py-2.5 sm:w-72">
            <Search className="ink-2 h-4 w-4" />
            <input value={q} onChange={(e) => setQ(e.target.value)} onBlur={() => q && track('resource_search', { q: q.slice(0, 40) })} placeholder="Search resources" aria-label="Search resources"
                   className="w-full bg-transparent text-sm outline-none placeholder:text-[var(--ink-2)]" />
            {q && <button aria-label="Clear search" onClick={() => setQ('')}><X className="h-3.5 w-3.5" /></button>}
          </div>
        </div>

        {groups.length === 0 && extraShown.length === 0 && (
          <div className="surface rounded-2xl p-8 text-center">
            <p className="font-semibold">No resources match “{q}”</p>
            <button onClick={() => { track('resource_search_ask', { q: q.slice(0, 60) }); onAsk(q); }} className="mt-3 rounded-full bg-brand-700 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-600">Ask NuevAI instead</button>
          </div>
        )}

        <div className="space-y-9">
          {groups.map((g) => (
            <section key={g.id}>
              <div className="mb-3">
                <h3 className="text-lg font-semibold">{g.title}</h3>
                <p className="ink-2 text-sm">{g.blurb}</p>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                {g.items.map((r) => <Card key={r.url} r={r} group={g.title} onAsk={onAsk} />)}
              </div>
            </section>
          ))}
          {extraShown.length > 0 && (
            <section>
              <div className="mb-3">
                <h3 className="text-lg font-semibold">Found on the school’s pages</h3>
                <p className="ink-2 text-sm">Booking, form and document links NuevAI collected automatically.</p>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                {extraShown.map((r) => <Card key={r.url} r={r} group="Found on pages" onAsk={onAsk} />)}
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

function Card({ r, group, onAsk }: { r: Resource; group: string; onAsk: (q: string) => void }) {
  const host = hostOf(r.url);
  const Icon = host.includes('docs.google') ? FileText : r.badge === 'Booking' ? Ticket : Globe;
  return (
    <div className="surface shadow-soft group flex flex-col rounded-2xl p-4 transition hover:-translate-y-0.5 hover:shadow-lift">
      <div className="flex items-start gap-3">
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-brand-500/10 text-brand-600 dark:text-brand-300"><Icon className="h-5 w-5" /></span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h4 className="truncate text-sm font-semibold">{r.title}</h4>
            {r.badge && <span className="shrink-0 rounded-full bg-amber-400/15 px-2 py-0.5 text-[10px] font-semibold text-amber-700 dark:text-amber-300">{r.badge}</span>}
          </div>
          <p className="ink-2 mt-0.5 line-clamp-2 text-xs leading-relaxed">{r.desc}</p>
        </div>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <a href={r.url} target="_blank" rel="noopener noreferrer" data-tracked="1"
           onClick={() => track('resource_click', { label: r.title, group, host })}
           className="inline-flex items-center gap-1 rounded-full bg-brand-700 px-3.5 py-1.5 text-xs font-semibold text-white transition hover:bg-brand-600">
          Open<ArrowUpRight className="h-3.5 w-3.5" />
        </a>
        {r.ask && (
          <button onClick={() => { track('resource_ask', { label: r.title }); onAsk(r.ask!); }}
                  className="surface-2 inline-flex items-center gap-1 rounded-full px-3 py-1.5 text-xs font-semibold transition hover:bg-brand-500/10">
            <MessageCircle className="h-3.5 w-3.5" />Ask NuevAI
          </button>
        )}
        <span className="ink-2 ml-auto truncate text-[11px]">{host}</span>
      </div>
    </div>
  );
}
