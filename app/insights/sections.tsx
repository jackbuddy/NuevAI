'use client';

import { useCallback, useEffect, useState } from 'react';
import { Flagged } from './flagged';
import { Bars, Card, catColor, DataTable, Delta, Empty, fmtInt, fmtMs, fmtPct, fmtTok, HBars, Heat, Help, Kpi, Meter, Notice, pac, Pill, SimpleTable, Stacked, ago, PAL } from './ui';

// The payload is built by insights.py; it is typed loosely here on purpose (the server owns the shape, empty states handle gaps).
/* eslint-disable @typescript-eslint/no-explicit-any */
export type M = any;
export type Api = (path: string, init?: RequestInit) => Promise<any>;
export interface Ctx { m: M; api: Api; days: number; aud: string; setAud: (a: string) => void; reload: () => void }

const RANGE_LABEL: Record<number, string> = { 1: '24 hours', 7: '7 days', 30: '30 days', 90: '90 days', 0: 'all time' };
export const rangeLabel = (d: number) => RANGE_LABEL[d] ?? `${d} days`;
const prevLabel = (m: M) => (m.kpis.prev ? undefined : 'all time has no previous period');
const seriesOf = (m: M, k: string): number[] => m.series.map((s: any) => s[k] ?? 0);
const barData = (m: M, k: string, noun: string) => m.series.map((s: any) => ({ label: s.label, value: s[k] ?? 0, tip: `${s.label}: ${s[k] ?? 0} ${noun}` }));
const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const HOURS = Array.from({ length: 24 }, (_, h) => (h === 0 ? '12a' : h < 12 ? `${h}a` : h === 12 ? '12p' : `${h - 12}p`));
const MODEL_ORDER = undefined;

// ------------------------------------------------------------------ Overview
export function Overview({ m, aud, days, setAud }: Ctx) {
  const c = m.kpis.cur, p = m.kpis.prev;
  const small = m.meta.selector === 'users' && c.active < 10;
  const hidden = m.audiences.window;
  const hid = (['internal', 'test', 'bot'] as const).filter((k) => hidden[k]?.ids).map((k) => `${hidden[k].ids} ${k}`).join(', ');
  return (
    <div className="space-y-4">
      {aud === 'users' && hid && <Notice>Showing real users only. Hidden in this range: {hid} browsers.<button className="font-semibold underline" onClick={() => setAud('all')}>Show everything</button></Notice>}
      {small && <Notice tone="warn">Very small numbers ({c.active} active in this range): treat percentages and trends as anecdotes, not signals.</Notice>}
      {m.no_interaction.browsers > 0 && <p className="ink-2 text-xs">{m.no_interaction.browsers} of {m.no_interaction.of} browsers in this range only loaded a page and did nothing (counted as visitors, not as active users; not assumed to be bots).</p>}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi label="Active users" value={fmtInt(c.active)} help="Browsers that did something (asked, tapped a tab, opened a link), not just loaded the page. One browser = one person, roughly: nobody logs in." delta={<Delta cur={c.active} prev={p?.active} label={prevLabel(m)} />} sub={`${fmtInt(c.visitors)} visitors incl. page-loads only`} spark={seriesOf(m, 'active')} sparkLabel="Active users per bucket" />
        <Kpi label="New / returning" value={`${fmtInt(c.new)} / ${fmtInt(c.returning)}` } help="New = this browser's first-ever event is inside the range (checked against the whole history, not just this range). Returning = seen before the range started." delta={<Delta cur={c.new} prev={p?.new} label={prevLabel(m)} />} spark={seriesOf(m, 'new')} sparkLabel="New visitors per bucket" />
        <Kpi label="Questions answered" value={fmtInt(c.chats)} delta={<Delta cur={c.chats} prev={p?.chats} label={prevLabel(m)} />} sub={`${fmtInt(c.askers)} people asked`} spark={seriesOf(m, 'chats')} sparkLabel="Questions per bucket" />
        <Kpi label="Answered well" value={fmtPct(c.well_rate)} help="Share of answers that were not degraded (model unavailable), had something behind them (sources) and did not say the information was not found. It is an automatic proxy, not a judgment of correctness: see Helpful for what people said." delta={<Delta cur={c.well_rate} prev={p?.well_rate} rate label={prevLabel(m)} />} sub={`${c.well} of ${c.chats}`} />
        <Kpi label="Helpful (thumbs)" value={fmtPct(c.helpful)} help="Thumbs-up divided by all thumbs. Few people vote, so check the counts." delta={<Delta cur={c.helpful} prev={p?.helpful} rate label={prevLabel(m)} />} sub={`${c.up} up · ${c.down} down`} />
        <Kpi label="Answer time (median)" value={fmtMs(c.p50_total)} help="Server time from question to full answer, including instant cached replies. Excludes the network. 'Seen in browser' below is what people experienced." delta={<Delta cur={c.p50_total} prev={p?.p50_total} better="down" label={prevLabel(m)} />} sub={`p95 ${fmtMs(c.p95_total)}`} spark={m.series.map((s: any) => s.p50)} sparkLabel="Median answer time per bucket" />
        <Kpi label="Seen in browser (median)" value={fmtMs(c.p50_seen)} help="Measured in the visitor's browser by the chat_rendered event: time until the full answer finished rendering, over the real network." sub={`first words ${fmtMs(c.p50_first_seen)} · p95 ${fmtMs(c.p95_seen)}`} delta={<Delta cur={c.p50_seen} prev={p?.p50_seen} better="down" label={prevLabel(m)} />} />
        <Kpi label="Degraded answers" value={fmtPct(c.chats ? c.degraded / c.chats : null)} help="Answers where the AI was unavailable and a facts-and-links reply was shown instead." sub={`${c.degraded} of ${c.chats}`} delta={<Delta cur={p ? c.degraded : null} prev={p?.degraded} better="down" label={prevLabel(m)} />} />
      </div>
      <Card title={`Questions per ${m.meta.granularity}`} help="Questions the assistant answered (server-recorded), bucketed in Pacific time.">
        <Bars data={barData(m, 'chats', 'questions')} title="Questions answered" tableHead={['Bucket', 'Questions']} />
      </Card>
      <Card title={`Active users per ${m.meta.granularity}`}>
        <Bars data={barData(m, 'active', 'active users')} color={PAL[1]} title="Active users" tableHead={['Bucket', 'Active users']} />
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ Adoption & retention
export function Adoption({ m }: Ctx) {
  const a = m.adoption, r = m.retention;
  const f = m.funnel;
  const top = Math.max(1, f[0]?.n ?? 1);
  const wk = Math.max(0, ...m.cohorts.map((c: any) => c.weeks.length));
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi label="Visitors today" value={fmtInt(a.dau_today)} sub={`7-day daily average ${a.dau_avg_7 ?? '—'}`} help={`Counts ${a.basis}. Always anchored to today, whatever range is selected.`} />
        <Kpi label="Visitors, last 7 days" value={fmtInt(a.wau)} sub="weekly (WAU)" />
        <Kpi label="Visitors, last 30 days" value={fmtInt(a.mau)} sub="monthly (MAU)" />
        <Kpi label="Stickiness" value={fmtPct(a.stickiness_wau)} help="Average daily visitors divided by weekly visitors (DAU/WAU): of the people who show up in a week, how many are there on a typical day. Monthly version: DAU/MAU." sub={`DAU/MAU ${fmtPct(a.stickiness_mau)}`} />
      </div>
      <Card title={`New vs returning per ${m.meta.granularity}`} help="New = the browser's first-ever event falls in that bucket. Returning = it had been seen before.">
        <Stacked title="New and returning visitors" data={m.series.map((s: any) => ({ label: s.label, parts: { New: s.new, Returning: s.returning } }))} order={['New', 'Returning']} />
      </Card>
      <Card title="Activation funnel" help="Browsers seen in the selected range, step by step: loaded the app, did something, asked a question, got an answer, asked at least two questions, and were seen on a second calendar day (any time in history).">
        {f[0]?.n ? (
          <ol className="space-y-2">
            {f.map((s: any, i: number) => (
              <li key={s.step}>
                <div className="flex items-baseline justify-between gap-3 text-sm"><span>{s.step}</span><span className="tabular-nums"><b>{fmtInt(s.n)}</b> <span className="ink-2 text-xs">{fmtPct(s.n / top)}{i > 0 && f[i - 1].n ? ` · ${fmtPct(s.n / f[i - 1].n)} of previous` : ''}</span></span></div>
                <div className="surface-2 mt-0.5 h-2 overflow-hidden rounded-full"><div className="h-full rounded-full" style={{ width: `${(s.n / top) * 100}%`, background: PAL[0], opacity: 1 - i * 0.12 }} /></div>
              </li>
            ))}
          </ol>
        ) : <Empty />}
      </Card>
      <div className="grid gap-4 md:grid-cols-2">
        <Card title="Retention" help="Of browsers old enough to have had the chance (maturity), how many came back. D1 = on the next calendar day; D7 = exactly 7 days later; within 7 days = any later day in the first week. Calendar days, Pacific time.">
          <ul className="space-y-2 text-sm">
            {(['d1', 'ret7', 'd7'] as const).map((k) => (
              <li key={k} className="flex items-baseline justify-between gap-3"><span>{r.labels[k]}</span><span className="tabular-nums"><b>{fmtPct(r[k].rate, 1)}</b> <span className="ink-2 text-xs">{r[k].hit}/{r[k].n}</span> {r[k].small_n && <Pill tone="warn">small n</Pill>}</span></li>
            ))}
          </ul>
        </Card>
        <Card title="Returning behaviour" help="Sessions per user counts distinct sessions (30 minutes of inactivity starts a new one). Days to return is the gap between a browser's first two active days.">
          <ul className="space-y-1.5 text-sm">
            <li className="flex justify-between"><span>Sessions per user</span><b className="tabular-nums">{a.sessions_per_user ?? '—'}</b></li>
            <li className="flex justify-between"><span>Median days to return</span><b className="tabular-nums">{a.days_to_return_median ?? '—'} <span className="ink-2 text-xs font-normal">n={a.days_to_return_n}</span></b></li>
          </ul>
          {a.days_to_return_n > 0 && <div className="mt-3"><HBars rows={Object.entries(a.days_to_return_buckets).map(([k, v]) => ({ label: k, value: v as number }))} color={PAL[1]} /></div>}
        </Card>
      </div>
      <Card title="Weekly cohorts" help="Each row is the group of browsers first seen in that week (Monday start, Pacific). Columns show the share active in week 0, 1, 2… after it. Rows with fewer than 20 browsers are flagged: one person moves them a lot.">
        {m.cohorts.length ? (
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full min-w-max text-left text-sm">
              <thead><tr><th scope="col" className="ink-2 py-1 pr-3 text-xs font-medium">First seen week of</th><th scope="col" className="ink-2 py-1 pr-3 text-xs font-medium">Browsers</th>{Array.from({ length: wk }, (_, i) => <th scope="col" key={i} className="ink-2 py-1 pr-3 text-xs font-medium">Week {i}</th>)}</tr></thead>
              <tbody>{m.cohorts.map((c: any) => (
                <tr key={c.week} className="border-t border-[var(--line)]">
                  <td className="py-1.5 pr-3">{pac(c.week + 'T12:00:00Z', { month: 'short', day: 'numeric' })}</td>
                  <td className="py-1.5 pr-3 tabular-nums">{c.n} {c.small_n && <Pill tone="warn">small n</Pill>}</td>
                  {Array.from({ length: wk }, (_, i) => <td key={i} className="py-1.5 pr-3 tabular-nums" style={c.weeks[i] != null ? { background: `rgba(27,65,119,${0.06 + 0.5 * c.weeks[i]})`, color: c.weeks[i] > 0.6 ? '#fff' : undefined } : undefined}>{c.weeks[i] == null ? '' : fmtPct(c.weeks[i])}</td>)}
                </tr>))}
              </tbody>
            </table>
          </div>
        ) : <Empty />}
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ Usage patterns
export function Usage({ m, api, aud }: Ctx) {
  const u = m.usage;
  const [date, setDate] = useState<string>(m.today.date);
  const [tl, setTl] = useState<any>(m.today);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  useEffect(() => { setDate(m.today.date); setTl(m.today); }, [m.today]);
  const pick = useCallback(async (d: string) => {
    setDate(d); setErr('');
    if (!d) return;
    if (d === m.today.date) { setTl(m.today); return; }
    setBusy(true);
    try { setTl(await api(`/api/metrics/day?date=${d}&aud=${aud}`)); } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }, [api, aud, m.today]);
  const total = u.weekday + u.weekend;
  return (
    <div className="space-y-4">
      <Card title="Questions by hour of day (Pacific)" help="When people ask, over the selected range. Hours are America/Los_Angeles.">
        <Bars data={u.by_hour.map((v: number, h: number) => ({ label: HOURS[h], value: v, tip: `${HOURS[h]}: ${v} questions` }))} title="Questions by hour of day" color={PAL[0]} />
      </Card>
      <Card title="Day of week × hour" help="Questions per cell. Darker = more.">
        <Heat matrix={u.heatmap} rows={DOW} cols={HOURS} title="Questions by day of week and hour (Pacific)" />
      </Card>
      <div className="grid gap-4 md:grid-cols-2">
        <Card title="Weekdays vs weekends" help={u.note}>
          {total ? <HBars rows={[{ label: 'Mon-Fri', value: u.weekday, right: `${u.weekday} · ${fmtPct(u.weekday / total)}` }, { label: 'Sat-Sun', value: u.weekend, right: `${u.weekend} · ${fmtPct(u.weekend / total)}` }]} max={total} color={PAL[1]} /> : <Empty />}
        </Card>
        <Card title="Pick a day" right={<label className="text-xs"><span className="sr-only">Date</span><input type="date" value={date} max={m.today.date} onChange={(e) => pick(e.target.value)} className="surface rounded-lg px-2 py-1 text-base sm:text-sm" /></label>}>
          {err && <p role="alert" className="mb-2 text-sm text-rose-700">{err}</p>}
          <p className="ink-2 mb-1 text-xs">{busy ? 'Loading…' : `${date === m.today.date ? 'Today so far' : date}: ${tl?.total_chats ?? 0} questions`}</p>
          {tl?.hours?.length ? <Bars data={tl.hours.map((h: any) => ({ label: HOURS[h.h], value: h.chats, tip: `${HOURS[h.h]}: ${h.chats} questions, ${h.active} active users` }))} title={`Questions by hour on ${date}`} color={PAL[2]} height={90} /> : <Empty>No events that day.</Empty>}
        </Card>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ Engagement
export function Engagement({ m }: Ctx) {
  const e = m.engagement;
  const ev = e.discovery;
  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <Card title="Tab usage" help="Taps on the Chat / Calendar / Resources tabs. Page loads and deep links are not counted as a choice.">
          <HBars rows={e.tabs.map((t: any) => ({ label: t.tab, value: t.views, right: `${t.views} views · ${t.users} users` }))} />
        </Card>
        <Card title="How questions get asked" help="chat_sent.via: typed by hand, picked from starters/suggestions, asked from another tab, or a retry.">
          <HBars rows={Object.entries(e.chat_via).map(([k, v]) => ({ label: k, value: v as number }))} color={PAL[1]} empty="No questions in this range." />
        </Card>
      </div>
      <Card title="Feature adoption" help={`Share = people who used the feature divided by active users in this range (${e.n_active}).`}>
        <HBars rows={e.features.map((f: any) => ({ label: f.feature, value: f.users, right: `${f.users} users · ${f.events} uses${f.share != null ? ` · ${fmtPct(f.share)}` : ''}` }))} max={Math.max(1, e.n_active)} color={PAL[0]} />
      </Card>
      <Card title="Onboarding and discovery" help="Events from the first-time experience. 'Not collected yet' means the app has not sent that event in any audience since tracking began.">
        <SimpleTable head={['Event', 'Users', 'Events', 'Top values', 'Status']} rows={ev.map((d: any) => [
          <code key="e" className="text-xs">{d.event}</code>, d.users, d.events, d.top.map(([k, v]: [string, number]) => `${k} (${v})`).join(', ') || '—',
          d.collected ? <Pill key="s" tone="good">since {pac(d.since, { month: 'short', day: 'numeric' })}</Pill> : <Pill key="s">not collected yet</Pill>])} />
      </Card>
      <Diagnostics m={m} />
    </div>
  );
}

function Diagnostics({ m }: { m: M }) {
  return (
    <details className="surface shadow-soft rounded-2xl p-4 sm:p-5">
      <summary className="cursor-pointer text-sm font-semibold">Diagnostics: every event type in this range ({m.diagnostics.length})</summary>
      <div className="mt-3"><HBars rows={m.diagnostics.map(([k, v]: [string, number]) => ({ label: k, value: v }))} color={PAL[7]} /></div>
    </details>
  );
}

// ------------------------------------------------------------------ Questions & content
function Occurrences({ k, api, days, aud }: { k: string; api: Api; days: number; aud: string }) {
  const [rows, setRows] = useState<any[] | null>(null);
  const [err, setErr] = useState('');
  useEffect(() => { let on = true; api(`/api/metrics/inspect?key=${encodeURIComponent(k)}&days=${days}&aud=${aud}`).then((j) => on && setRows(j.rows)).catch((e) => on && setErr(e.message)); return () => { on = false; }; }, [k, api, days, aud]);
  if (err) return <p role="alert" className="py-1 text-xs text-rose-700">{err}</p>;
  if (!rows) return <p className="ink-2 py-1 text-xs">Loading…</p>;
  return <div className="surface-2 mb-1 rounded-lg p-2"><SimpleTable head={['When (PT)', 'Audience', 'Route', 'Model', 'Time', 'Outcome']} rows={rows.map((r) => [pac(r.ts), r.aud, r.route + (r.route_inferred ? ' *' : ''), r.model ?? '—', fmtMs(r.ms), r.outcome])} empty="No occurrences in range." />{rows.some((r) => r.route_inferred) && <p className="ink-2 mt-1 text-[11px]">* route inferred from older flags (recorded before route tracking)</p>}</div>;
}

function QList({ rows, ctx, empty, extra }: { rows: any[]; ctx: Ctx; empty: string; extra?: (r: any) => string }) {
  const [open, setOpen] = useState<string>('');
  return <HBars empty={empty} rows={rows.map((r) => ({ label: r.q, value: r.n || r.thumbs_down, right: `${r.n}×${r.users ? ` · ${r.users} user${r.users > 1 ? 's' : ''}` : ''}`, sub: [r.topic, extra?.(r)].filter(Boolean).join(' · '),
    onClick: () => setOpen(open === r.key ? '' : r.key), expanded: open === r.key, children: open === r.key ? <Occurrences k={r.key} api={ctx.api} days={ctx.days} aud={ctx.aud} /> : null }))} />;
}

export function Questions(ctx: Ctx) {
  const c = ctx.m.content;
  return (
    <div className="space-y-4">
      <Card title="Topics" help={c.topic_note}>
        <HBars rows={c.topics.map((t: any) => ({ label: t.topic.replace(/_/g, ' '), value: t.n, right: `${t.n} · ${fmtPct(t.well_rate)} well`, sub: t.inferred ? `${t.inferred} classified from the question text (before server-side topics)` : undefined }))} />
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={`Top questions (${c.distinct_questions} distinct)`} help="Same wording, ignoring case and punctuation. Tap a row to see recent occurrences with their route, model and outcome."><QList rows={c.top_questions.slice(0, 15)} ctx={ctx} empty="No questions in this range." /></Card>
        <Card title="Content gaps" help="Questions that got a degraded reply, had no sources, or were answered with 'not found'. These are the best candidates for new or fixed knowledge-base content."><QList rows={c.gaps.slice(0, 15)} ctx={ctx} empty="No gaps in this range." extra={(r) => Object.entries(r.reasons).map(([k, v]) => `${k} ×${v}`).join(', ')} /></Card>
        <Card title="Marked not helpful (thumbs down)"><QList rows={c.thumbs_down} ctx={ctx} empty="No thumbs-down in this range." extra={(r) => `${r.thumbs_down} 👎`} /></Card>
        <Card title={`Written feedback (${c.feedback_messages.length})`}>
          {c.feedback_messages.length === 0 ? <Empty>None in this range.</Empty> : (
            <ul className="space-y-3">{c.feedback_messages.map((f: any, i: number) => (
              <li key={i} className="text-sm"><Pill>{f.kind || 'Other'}</Pill> <span className="ink-2 text-xs">{pac(f.ts)} · {f.tab} · {f.aud}</span><p className="wrap-any mt-1">{f.text}</p></li>))}</ul>
          )}
        </Card>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ Quality
export function Quality({ m, api, days, aud }: Ctx) {
  const q = m.quality;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi label="Answered well" value={fmtPct(q.well_rate)} sub={`${q.n} answers`} help="Not degraded, had sources, did not say 'not found'. A proxy, not proof of correctness." />
        <Kpi label="Helpful (thumbs)" value={fmtPct(q.helpful)} sub={`${q.up} up · ${q.down} down`} />
        <Kpi label="Degraded" value={fmtPct(q.degraded_rate, 1)} help="The AI was unavailable (rate limit, overload, timeout); a facts-and-links reply was shown." />
        <Kpi label="No sources / not found" value={`${fmtPct(q.no_context_rate)} / ${fmtPct(q.not_found_rate)}`} help="No sources: retrieval found nothing to ground the answer. Not found: the answer said the information is not available." small />
      </div>
      <Flagged api={api} days={days} aud={aud} />
      <Card title="By topic">
        <SimpleTable head={['Topic', 'Answers', 'Well', 'Degraded']} rows={q.by_topic.map((t: any) => [t.topic.replace(/_/g, ' '), t.n, fmtPct(t.well_rate), t.degraded])} />
      </Card>
      <Card title="By serving route" help="How the answer was produced: cache (memory or database), canned (greeting and similar), guard (safety reply), structured (exact lunch records), llm, degraded. * = inferred from older flags.">
        <SimpleTable head={['Route', 'Answers', 'Well', 'Degraded', 'No sources']} rows={q.by_route.map((r: any) => [r.route + (r.inferred ? ' *' : ''), r.n, fmtPct(r.well_rate), r.degraded, r.no_context])} />
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ System & models
export function System({ m }: Ctx) {
  const s = m.system, t = s.tokens;
  const since = m.notes.find((n: any) => n.metric.startsWith('Answering model'))?.since;
  return (
    <div className="space-y-4">
      {s.model_known === 0 && <Notice>Model, route-level and attempt data start with the first request after this version was deployed ({since ? pac(since) : 'not collected yet'}). Older events show as “unknown (older event)”. Nothing is back-filled.</Notice>}
      <Card title="Groq free-tier quota (all audiences)" help={t.note}>
        {t.models.length ? (
          <div className="space-y-4">{t.models.map((x: any) => (
            <div key={x.model}>
              <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm"><b className="wrap-any">{x.model}</b><span className="tabular-nums">{fmtTok(x.today_used)} / {fmtTok(x.cap)} today (UTC){x.today_est ? ` · ${fmtTok(x.today_est)} estimated` : ''}</span></div>
              <div className="my-1"><Meter value={x.today_used} max={x.cap} label={`${x.model} tokens today`} /></div>
              <p className="ink-2 text-xs">{x.today_requests} requests · projected end of day {fmtTok(x.projected_today)} {x.projected_exhausted ? <Pill tone="bad">would exhaust the daily cap</Pill> : <Pill tone="good">within cap</Pill>} · peak minute {fmtTok(x.peak_minute)} / {fmtTok(x.tpm_cap)} per-minute cap</p>
              {x.days.length > 1 && <div className="mt-2"><Bars data={x.days.map((d: any) => ({ label: d.day.slice(5), value: d.tokens, tip: `${d.day}: ${fmtTok(d.tokens)} tokens, ${d.requests} requests` }))} title={`${x.model} tokens per day`} color={PAL[2]} height={70} unit=" tokens" /></div>}
            </div>))}</div>
        ) : <Empty>Token usage not collected yet (it starts with the first answered request after this version is deployed).</Empty>}
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={`Which model answered, per ${m.meta.granularity}`}><Stacked title="Model that served each answer" data={s.served_by_bucket.map((b: any) => ({ label: b.label, parts: b.models }))} order={MODEL_ORDER} /></Card>
        <Card title="Which model answered, by hour of day (Pacific)"><Stacked title="Model by hour" data={s.served_by_hour.map((b: any) => ({ label: HOURS[b.h], parts: b.models }))} /></Card>
      </div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi label="Fallback rate" value={fmtPct(s.fallback.rate)} sub={`${s.fallback.fallbacks} of ${s.fallback.llm_requests} model requests`} help="A request is a fallback when a model other than the first choice wrote the answer. Reasons below." />
        <Kpi label="Cache hit rate" value={fmtPct(s.cache.hit_rate)} sub={`L1 ${s.cache.l1} · L2 ${s.cache.l2}${s.cache.unknown_layer ? ` · layer unknown ${s.cache.unknown_layer}` : ''}`} help="Answers served without calling the model, by layer: L1 memory, L2 Supabase." />
        <Kpi label="Cold vs warm (median)" value={`${fmtMs(s.cold.cold_p50)} / ${fmtMs(s.cold.warm_p50)}`} sub={`${s.cold.cold_n} cold · ${s.cold.warm_n} warm`} help="Cold = the first chat request after the server booted (Render free sleeps when idle). Needs requests recorded after this version shipped." small />
        <Kpi label="Concurrency / memory" value={`${s.inflight.max ?? '—'} / ${s.memory.max_mb != null ? Math.round(s.memory.max_mb) : '—'}${s.memory.max_mb ? ' MB' : ''}`} sub={`median in-flight ${s.inflight.p50 ?? '—'}`} help="Peak simultaneous chat requests and peak process memory (512 MB limit on the free plan)." small />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Fallback reasons">{Object.keys(s.fallback.reasons).length ? <HBars rows={Object.entries(s.fallback.reasons).map(([k, v]) => ({ label: k.replace(/_/g, ' '), value: v as number }))} color={PAL[3]} /> : <Empty>No fallbacks recorded in this range.</Empty>}</Card>
        <Card title={`Errors per ${m.meta.granularity} (429 / overload / timeout)`} help="Each failed LLM attempt, by kind. Older events only recorded 'degraded' with the HTTP status.">
          <Stacked title="LLM attempt errors" data={s.errors_by_bucket.map((b: any) => ({ label: b.label, parts: b.kinds }))} height={100} />
          {s.old_rate_limit_degraded > 0 && <p className="ink-2 mt-1 text-xs">{s.old_rate_limit_degraded} degraded answers from older events were rate limits (429).</p>}
        </Card>
      </div>
      <Card title="LLM attempts by model" help="Every call to a model, including failed ones that triggered a fallback. Latency is for successful attempts. Single-attempt successes from older events are included using the recorded model time.">
        <SimpleTable head={['Model', 'Served', 'Attempts', 'OK', '429', 'Overloaded', 'Timeout', 'Conn.', '5xx', 'Other', 'p50', 'p95']} rows={s.models.map((x: any) => [x.model, x.served, x.attempts, fmtPct(x.ok_rate), x.rate_limit, x.overloaded, x.timeout, x.connection, x.server_error, x.other, fmtMs(x.p50_ms), fmtMs(x.p95_ms)])} empty="No model data in this range." />
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Latency by stage" help="Median / 95th percentile of server-side time, uncached answers only.">
          <SimpleTable head={['Stage', 'n', 'p50', 'p95']} rows={Object.values(s.stages).filter((x: any) => x.n).map((x: any) => [x.label, x.n, fmtMs(x.p50), fmtMs(x.p95)])} />
        </Card>
        <Card title="Latency by model"><SimpleTable head={['Model', 'n', 'p50', 'p95']} rows={s.latency_by_model.map((x: any) => [x.model, x.n, fmtMs(x.p50), fmtMs(x.p95)])} empty="Not collected yet." /></Card>
        <Card title="Dependency errors" help="Embedding, vector search, calendar or link lookups that failed during a question (all audiences). The answer still went out using whatever else worked.">
          {Object.keys(s.dependency_errors.by_dep).length ? <HBars rows={Object.entries(s.dependency_errors.by_dep).map(([k, v]) => ({ label: k, value: v as number }))} color={PAL[3]} /> : <Empty>None recorded in this range.</Empty>}
        </Card>
        <Card title="Rate limits, restarts, client errors">
          <ul className="space-y-1 text-sm">
            <li>Rate-limited requests: {Object.keys(s.rate_limited).length ? Object.entries(s.rate_limited).map(([k, v]) => `${k} ${v}`).join(', ') : 'none'}</li>
            <li>Server boots: {s.boots.length ? s.boots.map((b: any) => pac(b.ts)).join('; ') : 'none recorded'}</li>
          </ul>
          <p className="ink-2 mb-1 mt-3 text-xs font-semibold">Client errors (shown audience)</p>
          <HBars rows={s.client_errors.map(([k, v]: [string, number]) => ({ label: k || '(empty)', value: v }))} color={PAL[3]} empty="None." />
        </Card>
      </div>
      <Card title="Web vitals by device" help="LCP (largest contentful paint, lower is better; under 2.5 s is good) and CLS (layout shift; under 0.1 is good), from the visitor's browser.">
        <SimpleTable head={['Device', 'Page loads', 'LCP p50', 'LCP p75', 'CLS p75']} rows={s.vitals.map((v: any) => [v.device, v.n, fmtMs(v.lcp_p50), fmtMs(v.lcp_p75), v.cls_p75 ?? '—'])} empty="No web-vitals events in this range." />
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ Recent
export function Recent({ m }: Ctx) {
  return (
    <Card title="Last 50 questions" help="Questions are truncated to 90 characters; no IPs or ids are shown. * = route inferred from older flags.">
      <SimpleTable caption="Recent chat requests" head={['When (PT)', 'Audience', 'Question', 'Route', 'Model', 'Time', 'Outcome']} empty="No questions in this range."
        rows={m.recent.map((r: any) => [pac(r.ts), r.aud, <span key="q" className="wrap-any block max-w-[22rem] whitespace-normal">{r.q}</span>, r.route + (r.route_inferred ? ' *' : ''), r.model ?? '—', fmtMs(r.ms), <Pill key="o" tone={r.outcome === 'ok' ? 'good' : 'warn'}>{r.outcome}</Pill>])} />
    </Card>
  );
}

// ------------------------------------------------------------------ Audience management
export function AudiencePanel({ m, api, reload, internal, onInternal }: Ctx & { internal: boolean; onInternal: (on: boolean) => void }) {
  const [rows, setRows] = useState<any[] | null>(null);
  const [state, setState] = useState<{ overrides_ok?: boolean | null; profiles_ok?: boolean | null }>({});
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState('');
  const load = useCallback(async () => { try { const j = await api('/api/metrics/ids'); setRows(j.ids); setState({ overrides_ok: j.overrides_ok, profiles_ok: j.profiles_ok }); setErr(''); } catch (e: any) { setErr(e.message); } }, [api]);
  useEffect(() => { load(); }, [load]);
  const mark = async (id: string, audience: string | null) => {
    setBusy(id); setErr('');
    try { const j = await api('/api/metrics/mark', { method: 'POST', body: JSON.stringify({ id, audience }) }); if (!j.persisted) setErr('Saved in server memory only: the overrides table is missing, so this is lost on restart. See NEEDS_USER.md.'); await load(); reload(); }
    catch (e: any) { setErr(e.message); } finally { setBusy(''); }
  };
  const w = m.audiences.all_time;
  return (
    <div className="space-y-4">
      <Card title="Who is in the data" help="Every browser (anonymous id) is classified once, across its whole history: your override > test > internal > bot > user. 'measured' = decided when the event arrived; 'inferred' = derived afterwards from the id prefix or user-agent for older rows.">
        <SimpleTable head={['Audience', 'Browsers', 'Events', 'Questions']} rows={(['user', 'internal', 'test', 'bot'] as const).map((k) => [k, w[k]?.ids ?? 0, w[k]?.events ?? 0, w[k]?.chats ?? 0])} caption="All-time audience counts" />
        <p className="ink-2 mt-2 text-xs">Basis: {m.audiences.sources.map((s: any) => `${s.ids} ${s.audience} (${s.source})`).join(' · ')}</p>
      </Card>
      <Card title="This browser" help="Marks this browser as yours: from now on its events are stamped internal, and (because the whole browser history is re-classified) its earlier events leave the real-user numbers too. Stored only in this browser (localStorage).">
        <p className="text-sm">{internal ? 'This browser is marked as internal (me).' : 'This browser is not marked as internal.'}</p>
        <button type="button" onClick={() => onInternal(!internal)} className="tap-box mt-2 rounded-full bg-brand-900 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700">{internal ? 'Unmark this browser' : 'Mark this browser as me'}</button>
        <p className="ink-2 mt-2 text-xs">Other devices: open the site once with <code>/?internal=1</code> (undo with <code>?internal=0</code>).</p>
      </Card>
      <Card title="Heaviest browsers" help="Busiest anonymous ids, for cleaning up history. Ids are shown as short hashes. Changing a row only adds an override in analytics_audience_overrides; no event is changed or deleted.">
        {state.overrides_ok === false && <Notice tone="warn">The overrides table is not installed yet: changes are kept in server memory only. Run supabase/analytics_v2.sql (see NEEDS_USER.md).</Notice>}
        {err && <p role="alert" className="mb-2 text-sm text-rose-700">{err}</p>}
        {!rows ? <p className="ink-2 text-sm">Loading…</p> : (
          <SimpleTable head={['Id', 'Audience', 'Events', 'Questions', 'Days', 'First seen', 'Last seen', 'Device', 'Mark as']} caption="Heaviest browsers" rows={rows.map((r) => [
            <code key="i" className="text-xs">{r.id}</code>,
            <span key="a"><Pill tone={r.audience === 'user' ? 'info' : 'neutral'}>{r.audience}</Pill> <span className="ink-2 text-[11px]">{r.source}</span></span>,
            r.events, r.chats, r.days, pac(r.first, { month: 'short', day: 'numeric' }), pac(r.last, { month: 'short', day: 'numeric', hour: 'numeric' }), <span key="d" title={r.ua}>{r.device}</span>,
            <span key="m" className="flex flex-wrap gap-1">{(['user', 'internal', 'test', 'bot'] as const).map((k) => (
              <button key={k} type="button" disabled={busy === r.id} aria-pressed={r.override === k} aria-label={`Mark ${r.id} as ${k === 'user' ? 'real user' : k}`} onClick={() => mark(r.id, k)}
                className={`tap-box rounded-full border px-2.5 py-1 text-xs font-semibold ${r.override === k ? 'border-brand-900 bg-brand-900 text-white' : 'border-[var(--line)] hover:bg-[var(--surface-2)]'}`}>{k === 'user' ? 'real' : k === 'internal' ? 'me' : k}</button>))}
              {r.override && <button type="button" disabled={busy === r.id} onClick={() => mark(r.id, null)} className="tap-box rounded-full px-2.5 py-1 text-xs underline">clear</button>}</span>])} />
        )}
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ Data notes
export function Notes({ m }: Ctx) {
  const h = m.health;
  return (
    <div className="space-y-4">
      <Card title="Data notes: measured vs inferred" help="When each newer metric started appearing in the data (taken from the data itself). Anything older is either absent or inferred, and labelled as such wherever it is shown.">
        <SimpleTable head={['Metric', 'Kind', 'Available since (PT)']} rows={m.notes.map((n: any) => [<span key="m" className="wrap-any block max-w-[28rem] whitespace-normal">{n.metric}{n.detail && <span className="ink-2 block text-xs">{n.detail}</span>}</span>, <Pill key="k" tone={n.kind === 'measured' ? 'good' : 'warn'}>{n.kind}</Pill>, n.since ? pac(n.since) : n.kind === 'inferred' ? 'rules, any date' : 'not collected yet'])} />
      </Card>
      <Card title="Pipeline health">
        <ul className="space-y-1 text-sm">
          <li>Table: {h.table_ok === false ? 'MISSING (events held in memory)' : 'ok'} · queued {h.queued} · written since boot {h.written} · dropped {h.dropped} · duplicates ignored {h.duplicates} · rejected {h.rejected}</li>
          <li>Flushes ok {h.flush_ok} / failed {h.flush_err}{h.last_error ? ` · last error: ${h.last_error}` : ''}</li>
          <li>Rows loaded for this view: {fmtInt(m.meta.rows_loaded)} (cap {fmtInt(h.max_rows)}){m.meta.truncated ? ' · TRUNCATED: older rows were left out' : ''} · {m.meta.duplicates_removed} duplicate events removed while aggregating · profile source: {m.meta.profile_source}</li>
          <li>Computed in {m.compute_ms ?? '—'} ms (cached up to 60 s).</li>
        </ul>
      </Card>
    </div>
  );
}

export { ago };
