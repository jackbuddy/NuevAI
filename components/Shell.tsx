'use client';

import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, BookOpen, CalendarDays, MessageSquare } from 'lucide-react';
import { ChatView } from './ChatView';
import { CalendarView } from './CalendarView';
import { ResourcesView } from './ResourcesView';
import { Logo } from './Logo';
import { FeedbackButton } from './FeedbackButton';
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
  const [status, setStatus] = useState<'ready' | 'waking' | 'nodata'>('ready');
  const [animate, setAnimate] = useState(false);   // the toggle only slides after the first paint

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
    const raf = requestAnimationFrame(() => requestAnimationFrame(() => setAnimate(true)));
    // wake the backend while the visitor is still reading the page, and learn whether it can reach its data
    const t0 = performance.now();
    const waking = setTimeout(() => setStatus((s) => (s === 'ready' ? 'waking' : s)), 2500);
    fetch('/api/warm').then((r) => r.json()).then((j) => {
      track('backend_warm', { ms: Math.round(performance.now() - t0), data_ok: j?.data_ok ?? null });
      setStatus(j?.data_ok === false ? 'nodata' : 'ready');
    }).catch(() => setStatus('ready')).finally(() => clearTimeout(waking));
    return () => { clearTimeout(waking); cancelAnimationFrame(raf); };
  }, []);

  const ask = useCallback((q: string) => { setPendingAsk(q); go('chat', 'ask_cta'); }, [go]);
  const consumed = useCallback(() => setPendingAsk(null), []);
  const idx = TABS.findIndex((t) => t.id === tab);

  return (
    <div className="flex h-dvh flex-col bg-white">
      <header className="relative z-20 border-b hairline bg-white/90 backdrop-blur">
        <div className="mx-auto grid w-full max-w-5xl grid-cols-[1fr_auto_1fr] items-center gap-3 px-4 py-2.5 md:px-6">
          <button onClick={() => go('chat', 'logo')} className="flex items-center gap-2 justify-self-start" aria-label="NuevAI home">
            <Logo size={26} />
            <span className="hidden text-[15px] font-bold tracking-tight sm:block">NuevAI</span>
            <span className="hidden rounded-full bg-brand-100 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-brand-700 sm:block">Alpha</span>
          </button>

          <nav role="tablist" aria-label="Sections" className="surface-2 relative grid grid-cols-3 rounded-full p-1">
            <span aria-hidden className={`absolute inset-y-1 left-1 w-[calc((100%-0.5rem)/3)] rounded-full bg-brand-900 shadow-soft ${animate ? 'transition-transform duration-300 ease-[cubic-bezier(.3,.9,.3,1)]' : ''}`}
                  style={{ transform: `translateX(${idx * 100}%)` }} />
            {TABS.map((t) => (
              <button key={t.id} role="tab" aria-selected={tab === t.id} onClick={() => go(t.id)}
                      className={`relative z-10 flex items-center justify-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold transition-colors sm:px-5 sm:text-[13px] ${tab === t.id ? 'text-white' : 'ink-2 hover:text-[var(--ink)]'}`}>
                <t.icon className="h-3.5 w-3.5" />{t.label}
              </button>
            ))}
          </nav>

          <div className="flex items-center gap-1.5 justify-self-end" title={status === 'ready' ? 'Assistant online' : status === 'waking' ? 'Assistant is starting up' : 'Setup needed'}>
            <FeedbackButton tab={tab} />
            <span className={`h-2 w-2 rounded-full ${status === 'ready' ? 'animate-pulse-ring bg-brand-700' : status === 'waking' ? 'bg-brand-300' : 'bg-amber-500'}`} />
          </div>
        </div>
        {status === 'nodata' && (
          <div role="alert" className="flex items-center justify-center gap-2 border-t border-amber-200 bg-amber-50 px-4 py-2 text-xs font-medium text-amber-900">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            The assistant can’t reach the school’s knowledge base right now (server setup). Answers will be unavailable until it’s fixed.
          </div>
        )}
      </header>

      <main className="relative flex min-h-0 flex-1 flex-col">
        <div className={tab === 'chat' ? 'flex min-h-0 flex-1 flex-col' : 'hidden'}><ChatView pendingAsk={pendingAsk} onAskConsumed={consumed} onOpenCalendar={() => go('calendar', 'today_strip')} /></div>
        {visited.has('calendar') && <div className={tab === 'calendar' ? 'flex min-h-0 flex-1 flex-col' : 'hidden'}><CalendarView onAsk={ask} /></div>}
        {visited.has('resources') && <div className={tab === 'resources' ? 'flex min-h-0 flex-1 flex-col' : 'hidden'}><ResourcesView onAsk={ask} /></div>}
      </main>
    </div>
  );
}
