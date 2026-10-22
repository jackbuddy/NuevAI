'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { BookOpen, CalendarDays, MessageSquare } from 'lucide-react';
import { ChatView } from './ChatView';
import { CalendarView } from './CalendarView';
import { ResourcesView } from './ResourcesView';
import { Logo } from './Logo';
import { startAnalytics, track } from '@/lib/analytics';

type Tab = 'calendar' | 'chat' | 'resources';
const TABS: { id: Tab; label: string; icon: typeof CalendarDays }[] = [
  { id: 'calendar', label: 'Calendar', icon: CalendarDays },
  { id: 'chat', label: 'Chat', icon: MessageSquare },
  { id: 'resources', label: 'Resources', icon: BookOpen },
];

export function Shell() {
  const [tab, setTab] = useState<Tab>('chat');
  const [visited, setVisited] = useState<Set<Tab>>(new Set(['chat']));
  const [pendingAsk, setPendingAsk] = useState<string | null>(null);
  const [backend, setBackend] = useState<'warm' | 'waking'>('warm');
  const startedAt = useRef(Date.now());

  const go = useCallback((t: Tab, source = 'nav') => {
    setTab(t);
    setVisited((v) => new Set(v).add(t));
    try { const u = new URL(location.href); u.searchParams.set('tab', t); history.replaceState(null, '', u); } catch {}
    track('tab_view', { tab: t, source });
  }, []);

  useEffect(() => {
    startAnalytics();
    const initial = new URLSearchParams(location.search).get('tab') as Tab | null;
    if (initial && TABS.some((t) => t.id === initial)) { setTab(initial); setVisited(new Set(['chat', initial])); track('tab_view', { tab: initial, source: 'url' }); }
    else track('tab_view', { tab: 'chat', source: 'load' });
    // wake the backend while the visitor is still reading the page
    const t0 = performance.now();
    const waking = setTimeout(() => setBackend('waking'), 2500);
    fetch('/api/warm').then((r) => r.json()).then((j) => { track('backend_warm', { ms: Math.round(performance.now() - t0), ready: !!j?.model_loaded }); }).catch(() => {}).finally(() => { clearTimeout(waking); setBackend('warm'); });
    return () => clearTimeout(waking);
  }, []);

  // ask NuevAI from anywhere (calendar event, resource card) -> jump to chat with the question
  const ask = useCallback((q: string) => { setPendingAsk(q); go('chat', 'ask_cta'); }, [go]);
  const consumed = useCallback(() => setPendingAsk(null), []);

  const idx = TABS.findIndex((t) => t.id === tab);

  return (
    <div className="flex h-dvh flex-col">
      <header className="glass relative z-20 border-x-0 border-t-0">
        <div className="mx-auto grid w-full max-w-5xl grid-cols-[1fr_auto_1fr] items-center gap-3 px-4 py-2.5 md:px-6">
          <button onClick={() => go('chat', 'logo')} className="flex items-center gap-2.5 justify-self-start" aria-label="NuevAI home">
            <Logo size={32} />
            <span className="hidden flex-col leading-none sm:flex">
              <span className="font-display text-lg font-semibold tracking-tight">NuevAI</span>
              <span className="ink-2 text-[10px] font-medium uppercase tracking-[0.14em]">The Nueva School</span>
            </span>
          </button>

          <nav role="tablist" aria-label="Sections" className="relative grid grid-cols-3 rounded-full p-1 surface-2 shadow-inner" style={{ border: '1px solid var(--line)' }}>
            <span aria-hidden className="absolute inset-y-1 left-1 w-[calc((100%-0.5rem)/3)] rounded-full bg-white shadow-soft transition-transform duration-300 ease-[cubic-bezier(.3,.9,.3,1)] dark:bg-brand-800"
                  style={{ transform: `translateX(${idx * 100}%)` }} />
            {TABS.map((t) => (
              <button key={t.id} role="tab" aria-selected={tab === t.id} onClick={() => go(t.id)}
                      className={`relative z-10 flex items-center justify-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold transition-colors sm:px-5 sm:text-[13px] ${tab === t.id ? 'text-brand-700 dark:text-white' : 'ink-2 hover:text-[var(--ink)]'}`}>
                <t.icon className="h-3.5 w-3.5" />{t.label}
              </button>
            ))}
          </nav>

          <div className="flex items-center gap-2 justify-self-end">
            <span className={`hidden items-center gap-2 text-xs font-medium sm:flex ${backend === 'waking' ? 'text-amber-600 dark:text-amber-300' : 'ink-2'}`} title={backend === 'waking' ? 'The assistant is starting up' : 'Assistant online'}>
              <span className={`h-2 w-2 rounded-full ${backend === 'waking' ? 'bg-amber-500' : 'animate-pulse-ring bg-emerald-500'}`} />
              {backend === 'waking' ? 'Starting up…' : 'Online'}
            </span>
          </div>
        </div>
      </header>

      <main className="relative flex min-h-0 flex-1 flex-col">
        <div className={tab === 'chat' ? 'flex min-h-0 flex-1 flex-col' : 'hidden'}><ChatView pendingAsk={pendingAsk} onAskConsumed={consumed} onOpenCalendar={() => go('calendar', 'today_strip')} /></div>
        {visited.has('calendar') && <div className={tab === 'calendar' ? 'flex min-h-0 flex-1 flex-col' : 'hidden'}><CalendarView onAsk={ask} /></div>}
        {visited.has('resources') && <div className={tab === 'resources' ? 'flex min-h-0 flex-1 flex-col' : 'hidden'}><ResourcesView onAsk={ask} /></div>}
      </main>
    </div>
  );
}
