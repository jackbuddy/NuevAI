'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, BookOpen, CalendarDays, MessageSquare, WifiOff } from 'lucide-react';
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

  const [online, setOnline] = useState(true);
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  // each tab change is a history entry, so Back/Forward move between tabs instead of leaving the site
  const go = useCallback((t: Tab, source = 'nav') => {
    setTab(t);
    setVisited((v) => new Set(v).add(t));
    try {
      const u = new URL(location.href);
      if (u.searchParams.get('tab') !== t) { u.searchParams.set('tab', t); history.pushState(null, '', u); }
    } catch {}
    track('tab_view', { tab: t, source });
  }, []);

  useEffect(() => {
    startAnalytics();
    const initial = new URLSearchParams(location.search).get('tab') as Tab | null;
    if (initial && TABS.some((t) => t.id === initial)) { setTab(initial); setVisited(new Set(['chat', initial])); track('tab_view', { tab: initial, source: 'url' }); }
    else track('tab_view', { tab: 'chat', source: 'load' });
    const onPop = () => {
      const t = new URLSearchParams(location.search).get('tab') as Tab | null;
      const next: Tab = t && TABS.some((x) => x.id === t) ? t : 'chat';
      setTab(next); setVisited((v) => new Set(v).add(next));
    };
    const setNet = () => setOnline(navigator.onLine);
    setNet();
    window.addEventListener('popstate', onPop);
    window.addEventListener('online', setNet);
    window.addEventListener('offline', setNet);
    const raf = requestAnimationFrame(() => requestAnimationFrame(() => setAnimate(true)));
    // wake the backend while the visitor is still reading the page, and learn whether it can reach its data
    const t0 = performance.now();
    const waking = setTimeout(() => setStatus((s) => (s === 'ready' ? 'waking' : s)), 2500);
    fetch('/api/warm').then((r) => r.json()).then((j) => {
      track('backend_warm', { ms: Math.round(performance.now() - t0), data_ok: j?.data_ok ?? null });
      setStatus(j?.data_ok === false ? 'nodata' : 'ready');
    }).catch(() => setStatus('ready')).finally(() => clearTimeout(waking));
    return () => {
      clearTimeout(waking); cancelAnimationFrame(raf);
      window.removeEventListener('popstate', onPop); window.removeEventListener('online', setNet); window.removeEventListener('offline', setNet);
    };
  }, []);

  const ask = useCallback((q: string) => { setPendingAsk(q); go('chat', 'ask_cta'); }, [go]);
  const consumed = useCallback(() => setPendingAsk(null), []);
  const statusText = !online ? 'You are offline' : status === 'ready' ? 'Assistant online' : status === 'waking' ? 'Assistant is starting up' : 'Setup needed';
  const idx = TABS.findIndex((t) => t.id === tab);
  const onTabKey = (e: React.KeyboardEvent) => {
    const k = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : e.key === 'Home' ? 'first' : e.key === 'End' ? 'last' : 0;
    if (!k) return;
    e.preventDefault();
    const n = k === 'first' ? 0 : k === 'last' ? TABS.length - 1 : (idx + k + TABS.length) % TABS.length;
    go(TABS[n].id, 'keyboard');
    tabRefs.current[TABS[n].id]?.focus();
  };

  return (
    <div className="flex h-dvh flex-col bg-white">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded-full focus:bg-brand-900 focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-white">Skip to content</a>
      <header className="pt-safe relative z-20 border-b hairline bg-white/90 backdrop-blur">
        <div className="px-safe mx-auto grid w-full max-w-5xl grid-cols-[1fr_auto_1fr] items-center gap-2 py-2 sm:gap-3 md:px-6">
          <button onClick={() => go('chat', 'logo')} className="tap-box -ml-1 gap-2 justify-self-start rounded-full px-1" aria-label="NuevAI home">
            <Logo size={26} />
            <span className="hidden text-[15px] font-bold tracking-tight sm:block">NuevAI</span>
            <span className="hidden rounded-full bg-brand-100 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-brand-700 sm:block">Alpha</span>
          </button>

          <nav role="tablist" aria-label="Sections" onKeyDown={onTabKey} className="surface-2 relative grid grid-cols-3 rounded-full p-1">
            <span aria-hidden className={`absolute inset-y-1 left-1 w-[calc((100%-0.5rem)/3)] rounded-full bg-brand-900 shadow-soft ${animate ? 'transition-transform duration-300 ease-[cubic-bezier(.3,.9,.3,1)]' : ''}`}
                  style={{ transform: `translateX(${idx * 100}%)` }} />
            {TABS.map((t) => (
              <button key={t.id} id={`tab-${t.id}`} ref={(el) => { tabRefs.current[t.id] = el; }} role="tab" aria-selected={tab === t.id} aria-controls={`panel-${t.id}`} tabIndex={tab === t.id ? 0 : -1} onClick={() => go(t.id)}
                      className={`tap-h relative z-10 flex items-center justify-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold transition-colors sm:px-5 sm:text-[13px] ${tab === t.id ? 'text-white' : 'ink-2 hover:text-[var(--ink)]'}`}>
                <t.icon className="h-3.5 w-3.5" aria-hidden />{t.label}
              </button>
            ))}
          </nav>

          <div className="flex items-center gap-1 justify-self-end" title={statusText}>
            <FeedbackButton tab={tab} />
            <span role="status" aria-label={statusText} className={`h-2 w-2 rounded-full ${status === 'ready' ? 'animate-pulse-ring bg-brand-700' : status === 'waking' ? 'bg-brand-300' : 'bg-amber-500'}`} />
          </div>
        </div>
        {!online && (
          <div role="status" className="flex items-center justify-center gap-2 border-t border-[var(--line)] bg-[var(--surface-2)] px-4 py-2 text-xs font-medium">
            <WifiOff className="h-3.5 w-3.5 shrink-0" aria-hidden />
            You’re offline. Questions and the calendar will work again when you reconnect.
          </div>
        )}
        {status === 'nodata' && (
          <div role="alert" className="flex items-center justify-center gap-2 border-t border-amber-200 bg-amber-50 px-4 py-2 text-xs font-medium text-amber-900">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            The assistant can’t reach the school’s knowledge base right now (server setup). Answers will be unavailable until it’s fixed.
          </div>
        )}
      </header>

      <main id="main" tabIndex={-1} className="relative flex min-h-0 flex-1 flex-col outline-none">
        <div role="tabpanel" id="panel-chat" aria-labelledby="tab-chat" className={tab === 'chat' ? 'flex min-h-0 flex-1 flex-col' : 'hidden'}><ChatView pendingAsk={pendingAsk} onAskConsumed={consumed} onOpenCalendar={() => go('calendar', 'today_strip')} /></div>
        {visited.has('calendar') && <div role="tabpanel" id="panel-calendar" aria-labelledby="tab-calendar" className={tab === 'calendar' ? 'flex min-h-0 flex-1 flex-col' : 'hidden'}><CalendarView onAsk={ask} /></div>}
        {visited.has('resources') && <div role="tabpanel" id="panel-resources" aria-labelledby="tab-resources" className={tab === 'resources' ? 'flex min-h-0 flex-1 flex-col' : 'hidden'}><ResourcesView onAsk={ask} /></div>}
      </main>
    </div>
  );
}
