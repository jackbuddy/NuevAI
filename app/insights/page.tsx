'use client';

import { useCallback, useEffect, useState } from 'react';
import { Logo } from '@/components/Logo';

interface Metrics {
  days: number; storage: string; events_total: number; unique_users: number; sessions: number; chats: number; chat_errors: number;
  cache_hit_rate: number | null; repeat_users: number; new_users: number; returning_users: number;
  latency_ms: { total_p50: number | null; total_p95: number | null; first_token_p50: number | null; first_token_p95: number | null };
  feedback: Record<string, number>; devices: Record<string, number>; tab_views: Record<string, number>; events_by_type: Record<string, number>;
  top_questions: [string, number][]; unanswered_questions: [string, number][]; resource_clicks: [string, number][]; calendar_filters: [string, number][];
  daily: { day: string; users: number; chats: number }[];
  server?: { answer_cache: Record<string, number>; llm_model: string; models: Record<string, boolean> };
}

const ms = (v: number | null | undefined) => (v == null ? '—' : v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${v}ms`);

export default function Insights() {
  const [token, setToken] = useState('');
  const [days, setDays] = useState(14);
  const [m, setM] = useState<Metrics | null>(null);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (t: string, d: number) => {
    setLoading(true); setErr('');
    try {
      const r = await fetch(`/api/metrics?days=${d}`, { headers: { 'x-metrics-token': t } });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || `Error ${r.status}`);
      setM(j); try { sessionStorage.setItem('nuevai.metrics.token', t); } catch {}
    } catch (e: any) { setErr(e.message); setM(null); } finally { setLoading(false); }
  }, []);

  useEffect(() => { try { const t = sessionStorage.getItem('nuevai.metrics.token'); if (t) { setToken(t); load(t, days); } } catch {} }, []); // eslint-disable-line

  const maxChats = Math.max(1, ...(m?.daily.map((d) => d.chats) ?? [1]));
  const up = m?.feedback.up ?? 0, down = m?.feedback.down ?? 0;

  return (
    <div className="min-h-dvh px-4 py-8 md:px-8">
      <div className="mx-auto max-w-5xl">
        <header className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <a href="/" className="flex items-center gap-3"><Logo size={36} /><div><h1 className="text-2xl font-bold leading-none">Insights</h1><p className="ink-2 text-xs">NuevAI usage &amp; performance</p></div></a>
          <form onSubmit={(e) => { e.preventDefault(); load(token, days); }} className="flex flex-wrap items-center gap-2">
            <input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="Metrics token" aria-label="Metrics token" className="surface w-44 rounded-full px-4 py-2 text-sm outline-none" />
            <select value={days} onChange={(e) => { const d = Number(e.target.value); setDays(d); if (token) load(token, d); }} className="surface rounded-full px-3 py-2 text-sm" aria-label="Range">
              {[1, 7, 14, 30, 60].map((d) => <option key={d} value={d}>Last {d}d</option>)}
            </select>
            <button className="rounded-full bg-brand-900 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700">{loading ? 'Loading…' : 'Load'}</button>
          </form>
        </header>

        {err && <div className="mb-5 rounded-2xl border border-brand-200 bg-brand-50 p-4 text-sm text-brand-800">{err}</div>}
        {!m && !err && <p className="ink-2 text-sm">Enter the metrics token (the METRICS_TOKEN set on the backend) to view usage.</p>}

        {m && (
          <div className="space-y-6">
            {m.storage.startsWith('memory') && <div className="rounded-2xl border border-amber-500/40 bg-amber-500/10 p-4 text-sm">Events are only being kept in memory and will be lost on restart. Run <code>supabase/analytics.sql</code> in the Supabase SQL editor to store them permanently.</div>}
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <Kpi label="Unique users" value={m.unique_users} sub={`${m.new_users} new · ${m.returning_users} returning`} />
              <Kpi label="Sessions" value={m.sessions} sub={`${m.repeat_users} came back on another day`} />
              <Kpi label="Questions answered" value={m.chats} sub={`${m.chat_errors} errors`} />
              <Kpi label="Answered from cache" value={m.cache_hit_rate == null ? '—' : `${Math.round(m.cache_hit_rate * 100)}%`} sub="instant replies" />
              <Kpi label="First words (median)" value={ms(m.latency_ms.first_token_p50)} sub={`p95 ${ms(m.latency_ms.first_token_p95)}`} />
              <Kpi label="Full answer (median)" value={ms(m.latency_ms.total_p50)} sub={`p95 ${ms(m.latency_ms.total_p95)}`} />
              <Kpi label="Helpful" value={up + down ? `${Math.round((up / (up + down)) * 100)}%` : '—'} sub={`${up} 👍 · ${down} 👎`} />
              <Kpi label="Devices" value={Object.entries(m.devices).map(([k, v]) => `${v} ${k}`).join(' · ') || '—'} small />
            </div>

            <Card title="Questions per day">
              <div className="flex h-36 items-end gap-1.5">
                {m.daily.map((d) => (
                  <div key={d.day} className="group flex flex-1 flex-col items-center justify-end gap-1" title={`${d.day}: ${d.chats} questions, ${d.users} users`}>
                    <div className="w-full rounded-t-md bg-brand-800" style={{ height: `${Math.max(4, (d.chats / maxChats) * 100)}%` }} />
                    <span className="ink-2 text-[9px]">{d.day.slice(5)}</span>
                  </div>
                ))}
                {m.daily.length === 0 && <p className="ink-2 text-sm">No data yet.</p>}
              </div>
            </Card>

            <div className="grid gap-6 md:grid-cols-2">
              <Card title="Top questions"><List rows={m.top_questions} /></Card>
              <Card title="Questions it struggled with"><List rows={m.unanswered_questions} empty="None — nice." /></Card>
              <Card title="Tab views"><List rows={Object.entries(m.tab_views)} /></Card>
              <Card title="Resource clicks"><List rows={m.resource_clicks} /></Card>
              <Card title="Calendar filters & searches"><List rows={m.calendar_filters} /></Card>
              <Card title="All event types"><List rows={Object.entries(m.events_by_type)} /></Card>
            </div>
            <p className="ink-2 text-xs">{m.events_total.toLocaleString()} events · storage: {m.storage}{m.server ? ` · model ${m.server.llm_model}` : ''}</p>
          </div>
        )}
      </div>
    </div>
  );
}

function Kpi({ label, value, sub, small }: { label: string; value: string | number; sub?: string; small?: boolean }) {
  return (
    <div className="surface shadow-soft rounded-2xl p-4">
      <p className="ink-2 text-xs font-medium">{label}</p>
      <p className={`mt-1 font-bold ${small ? 'text-lg' : 'text-3xl'}`}>{value}</p>
      {sub && <p className="ink-2 mt-0.5 text-xs">{sub}</p>}
    </div>
  );
}
function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="surface shadow-soft rounded-2xl p-5"><h2 className="mb-3 text-sm font-semibold">{title}</h2>{children}</section>;
}
function List({ rows, empty = 'No data yet.' }: { rows: [string, number][]; empty?: string }) {
  if (!rows.length) return <p className="ink-2 text-sm">{empty}</p>;
  return <ul className="space-y-1.5 text-sm">{rows.map(([k, v]) => <li key={k} className="flex items-baseline justify-between gap-3"><span className="truncate">{k}</span><span className="ink-2 tabular-nums">{v}</span></li>)}</ul>;
}
