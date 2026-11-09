'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Logo } from '@/components/Logo';
import { isInternal, setInternal } from '@/lib/analytics';
import { Adoption, AudiencePanel, Engagement, Notes, Overview, Questions, Quality, Recent, System, Usage, type Ctx, type M } from './sections';
import { ago, Help, pac, Pill } from './ui';

const TOKEN_KEY = 'nuevai.metrics.token';
const OPTOUT_KEY = 'nuevai.internal.optout';
const RANGES: { d: number; label: string }[] = [{ d: 1, label: '24h' }, { d: 7, label: '7d' }, { d: 30, label: '30d' }, { d: 90, label: '90d' }, { d: 0, label: 'All' }];
const AUDS: { id: string; label: string }[] = [{ id: 'users', label: 'Real users' }, { id: 'users_internal', label: 'Users + internal' }, { id: 'test_bots', label: 'Test & bots' }, { id: 'all', label: 'Everything' }];
const TABS = [
  { id: 'overview', label: 'Overview' }, { id: 'adoption', label: 'Adoption' }, { id: 'usage', label: 'Usage' }, { id: 'engagement', label: 'Engagement' },
  { id: 'questions', label: 'Questions' }, { id: 'quality', label: 'Quality' }, { id: 'system', label: 'System' }, { id: 'recent', label: 'Recent' },
  { id: 'audience', label: 'Audience' }, { id: 'notes', label: 'Data notes' },
] as const;
type TabId = (typeof TABS)[number]['id'];

function readParam<T extends string | number>(k: string, ok: (v: string) => T | null, dflt: T): T {
  try { const v = new URLSearchParams(location.search).get(k); const r = v == null ? null : ok(v); return r == null ? dflt : r; } catch { return dflt; }
}

export default function Insights() {
  const [token, setToken] = useState('');
  const [days, setDays] = useState(7);
  const [aud, setAud] = useState('users');
  const [tab, setTab] = useState<TabId>('overview');
  const [m, setM] = useState<M | null>(null);
  const [err, setErr] = useState<{ msg: string; kind: 'auth' | 'offline' | 'server' } | null>(null);
  const [loading, setLoading] = useState(false);
  const [updated, setUpdated] = useState<number | null>(null);
  const [, tick] = useState(0);
  const [internal, setInternalState] = useState(false);
  const [autoMarked, setAutoMarked] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const tokenRef = useRef('');
  tokenRef.current = token;

  const api = useCallback(async (path: string, init?: RequestInit) => {
    const r = await fetch(path, { ...init, headers: { 'x-metrics-token': tokenRef.current, ...(init?.body ? { 'Content-Type': 'application/json' } : {}) }, cache: 'no-store' });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || `Error ${r.status}`);
    return j;
  }, []);

  const load = useCallback(async (t: string, d: number, a: string, refresh = false) => {
    abort.current?.abort();
    const ac = new AbortController();
    abort.current = ac;
    setLoading(true); setErr(null);
    try {
      const r = await fetch(`/api/metrics?days=${d}&aud=${a}${refresh ? '&refresh=1' : ''}`, { headers: { 'x-metrics-token': t }, signal: ac.signal, cache: 'no-store' });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        if (r.status === 401) { try { sessionStorage.removeItem(TOKEN_KEY); } catch {} throw Object.assign(new Error('That token was not accepted.'), { kind: 'auth' }); }
        throw Object.assign(new Error(j.detail || `Error ${r.status}`), { kind: 'server' });
      }
      setM(j); setUpdated(Date.now());
      try { sessionStorage.setItem(TOKEN_KEY, t); } catch {}
      // a valid token on /insights marks this browser as the owner's, unless they opted out
      try {
        if (!localStorage.getItem(OPTOUT_KEY) && !isInternal()) { setInternal(true); setInternalState(true); setAutoMarked(true); }
      } catch {}
    } catch (e: any) {
      if (e?.name === 'AbortError') return;
      const offline = typeof navigator !== 'undefined' && navigator.onLine === false;
      setErr({ msg: offline ? 'You appear to be offline. Reconnect and press Refresh.' : e.kind ? e.message : 'Could not reach the server. It may be waking up (free hosting): try again in a moment.', kind: offline ? 'offline' : e.kind ?? 'server' });
    } finally { if (abort.current === ac) setLoading(false); }
  }, []);

  useEffect(() => {
    const d = readParam('range', (v) => (RANGES.some((r) => String(r.d) === v) ? Number(v) : null), 7);
    const a = readParam('aud', (v) => (AUDS.some((x) => x.id === v) ? v : null), 'users');
    const tb = readParam('tab', (v) => (TABS.some((x) => x.id === v) ? (v as TabId) : null), 'overview');
    setDays(d); setAud(a); setTab(tb);
    setInternalState(isInternal());
    try { const t = sessionStorage.getItem(TOKEN_KEY); if (t) { setToken(t); load(t, d, a); } } catch {}
    const t = setInterval(() => tick((x) => x + 1), 30_000);
    return () => { clearInterval(t); abort.current?.abort(); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const sync = (patch: Record<string, string>) => {
    try { const q = new URLSearchParams(location.search); Object.entries(patch).forEach(([k, v]) => q.set(k, v)); history.replaceState(null, '', `${location.pathname}?${q}`); } catch {}
  };
  const pickRange = (d: number) => { setDays(d); sync({ range: String(d) }); if (token) load(token, d, aud); };
  const pickAud = (a: string) => { setAud(a); sync({ aud: a }); if (token) load(token, days, a); };
  const pickTab = (t: TabId) => { setTab(t); sync({ tab: t }); };
  const reload = useCallback(() => load(tokenRef.current, days, aud, true), [load, days, aud]);
  const toggleInternal = (on: boolean) => { setInternal(on); setInternalState(on); try { if (on) localStorage.removeItem(OPTOUT_KEY); else localStorage.setItem(OPTOUT_KEY, '1'); } catch {} setAutoMarked(false); };

  const h = m?.health;
  const health = useMemo(() => {
    if (!h) return null;
    const bad = h.table_ok === false || h.flush_err > 0 && h.queued > 50 || m?.meta.truncated;
    const last = m?.meta.last_event;
    return { bad, text: `Data ${bad ? 'needs attention' : 'healthy'} · last event ${ago(last)} · ${h.queued} queued · ${h.dropped} dropped · ${h.flush_err} flush errors · table ${h.table_ok === false ? 'MISSING' : 'ok'}${m?.meta.truncated ? ' · TRUNCATED' : ''}` };
  }, [h, m]);

  const ctx: Ctx = { m, api, days, aud, setAud: pickAud, reload };
  const onTab = (e: React.KeyboardEvent) => {
    const i = TABS.findIndex((x) => x.id === tab);
    const n = e.key === 'ArrowRight' ? (i + 1) % TABS.length : e.key === 'ArrowLeft' ? (i + TABS.length - 1) % TABS.length : e.key === 'Home' ? 0 : e.key === 'End' ? TABS.length - 1 : -1;
    if (n < 0) return;
    e.preventDefault(); pickTab(TABS[n].id);
    requestAnimationFrame(() => document.getElementById(`tab-${TABS[n].id}`)?.focus());
  };

  return (
    <main className="px-safe min-h-dvh pb-12">
      <div className="mx-auto max-w-6xl">
        <header className="z-30 -mx-4 border-b border-[var(--line)] bg-white/90 px-4 py-3 backdrop-blur md:sticky md:top-0 md:mx-0 md:rounded-b-2xl md:px-4">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <a href="/" className="tap-h flex items-center gap-3 rounded-lg" aria-label="NuevAI home"><Logo size={32} /><div><h1 className="text-xl font-bold leading-none">Insights</h1><p className="ink-2 text-[11px]">Pacific time (America/Los_Angeles)</p></div></a>
            <form onSubmit={(e) => { e.preventDefault(); if (token) load(token, days, aud, true); }} className="ml-auto flex flex-wrap items-center gap-2">
              <input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="Metrics token" aria-label="Metrics token" autoComplete="off" className="bare surface tap-h w-36 rounded-full px-4 py-2 text-base outline-none focus:border-brand-400 focus:ring-4 focus:ring-brand-500/10 sm:text-sm" />
              <button disabled={loading || !token} className="tap-box rounded-full bg-brand-900 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-50">{loading ? 'Loading…' : m ? 'Refresh' : 'Load'}</button>
            </form>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2">
            <div role="group" aria-label="Range" className="surface flex rounded-full p-0.5">
              {RANGES.map((r) => <button key={r.d} type="button" aria-pressed={days === r.d} onClick={() => pickRange(r.d)} className={`tap-box rounded-full px-3 py-1.5 text-sm font-semibold ${days === r.d ? 'bg-brand-900 text-white' : 'hover:bg-[var(--surface-2)]'}`}>{r.label}</button>)}
            </div>
            <label className="flex items-center gap-2 text-sm"><span className="ink-2 text-xs">Audience</span>
              <select value={aud} onChange={(e) => pickAud(e.target.value)} className="surface tap-h rounded-full px-3 py-1.5 text-base sm:text-sm" aria-label="Audience">
                {AUDS.map((a) => <option key={a.id} value={a.id}>{a.label}</option>)}
              </select>
            </label>
            <Help text="Real users excludes your own browsers (internal), our test harnesses (test) and automated clients (bot). Switch to see them. Every browser is classified once across its whole history." label="About audiences" />
            <span className="ink-2 ml-auto text-xs" aria-live="polite">{updated ? `Updated ${pac(new Date(updated).toISOString(), { hour: 'numeric', minute: '2-digit' })} PT` : ''}</span>
          </div>
          {health && <p className={`mt-2 truncate text-xs ${health.bad ? 'font-semibold text-amber-800' : 'ink-2'}`} title={health.text}><span aria-hidden className={`mr-1.5 inline-block h-2 w-2 rounded-full ${health.bad ? 'bg-amber-500' : 'bg-emerald-500'}`} />{health.text}</p>}
        </header>

        <div className="px-0 pt-4">
          {err && <div role="alert" className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-2xl border border-brand-200 bg-brand-50 p-4 text-sm text-brand-800"><span>{err.msg}</span>{err.kind !== 'auth' && token && <button onClick={() => load(token, days, aud, true)} className="tap-box rounded-full border border-brand-300 px-3 py-1 font-semibold">Retry</button>}</div>}
          {!m && !err && !loading && <p className="ink-2 text-sm">Enter the metrics token (the METRICS_TOKEN set on the backend) to view usage. It stays in this tab only. Opening Insights with a valid token also marks this browser as yours so it stays out of the real-user numbers (undo on the Audience tab).</p>}
          {!m && loading && <div className="space-y-3" aria-busy="true" aria-label="Loading"><div className="shimmer h-24 rounded-2xl" /><div className="shimmer h-40 rounded-2xl" /></div>}

          {m && (
            <div className={loading ? 'opacity-60 transition-opacity' : 'transition-opacity'} aria-busy={loading}>
              {autoMarked && <p className="mb-3 rounded-xl bg-emerald-500/10 p-3 text-sm text-emerald-900">This browser is now marked as internal, so your own visits are not counted as real users. <button className="font-semibold underline" onClick={() => toggleInternal(false)}>Undo</button></p>}
              {m.health?.table_ok === false && <p className="mb-3 rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-sm">Events are only being kept in memory and will be lost on restart. Run <code>supabase/analytics.sql</code> in the Supabase SQL editor.</p>}
              {m.meta.truncated && <p className="mb-3 rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-sm">This range has more events than the dashboard loads ({m.meta.rows_loaded.toLocaleString()}); the oldest were left out, so totals for long ranges are undercounted. Pick a shorter range.</p>}
              <div className="sticky top-0 z-20 -mx-4 bg-white/95 px-4 backdrop-blur md:static md:mx-0 md:bg-transparent md:px-0">
                <div role="tablist" aria-label="Dashboard sections" onKeyDown={onTab} className="no-scrollbar flex gap-1 overflow-x-auto border-b border-[var(--line)] py-1">
                  {TABS.map((t) => <button key={t.id} id={`tab-${t.id}`} role="tab" aria-selected={tab === t.id} aria-controls="panel" tabIndex={tab === t.id ? 0 : -1} onClick={() => pickTab(t.id)} className={`tap-box shrink-0 whitespace-nowrap rounded-full px-3.5 py-1.5 text-sm font-semibold ${tab === t.id ? 'bg-brand-900 text-white' : 'ink-2 hover:bg-[var(--surface-2)]'}`}>{t.label}</button>)}
                </div>
              </div>
              <div id="panel" role="tabpanel" aria-labelledby={`tab-${tab}`} className="pt-4">
                <p className="ink-2 mb-3 text-xs">{m.meta.window.start ? `${pac(m.meta.window.start, { month: 'short', day: 'numeric', hour: 'numeric' })} to ${pac(m.meta.window.end, { month: 'short', day: 'numeric', hour: 'numeric' })} PT` : 'All time'} · <Pill tone={aud === 'users' ? 'info' : 'neutral'}>{AUDS.find((a) => a.id === aud)?.label}</Pill>{' '}{m.meta.profile_source !== 'sql_all_time' && <Pill tone="warn">first-seen from loaded rows only</Pill>}</p>
                {tab === 'overview' && <Overview {...ctx} />}
                {tab === 'adoption' && <Adoption {...ctx} />}
                {tab === 'usage' && <Usage {...ctx} />}
                {tab === 'engagement' && <Engagement {...ctx} />}
                {tab === 'questions' && <Questions {...ctx} />}
                {tab === 'quality' && <Quality {...ctx} />}
                {tab === 'system' && <System {...ctx} />}
                {tab === 'recent' && <Recent {...ctx} />}
                {tab === 'audience' && <AudiencePanel {...ctx} internal={internal} onInternal={toggleInternal} />}
                {tab === 'notes' && <Notes {...ctx} />}
                <p className="ink-2 mt-6 text-xs">{m.meta.rows_loaded.toLocaleString()} events loaded · {m.server ? `model ${m.server.llm_model} · ` : ''}no personal data, IPs or accounts: anonymous browser ids only.</p>
              </div>
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
