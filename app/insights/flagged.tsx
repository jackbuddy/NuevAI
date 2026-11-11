'use client';

import { useCallback, useEffect, useState } from 'react';
import { Check, ChevronDown, Copy } from 'lucide-react';
import { Card, Empty, Notice, pac, Pill } from './ui';
import type { Ctx } from './sections';

/* eslint-disable @typescript-eslint/no-explicit-any */
// Flagged & low-rated answers (built by flagged.py). Uses the page's range and audience; filters by type and reason; click a row to expand.

const TYPE: Record<string, { label: string; tone: 'bad' | 'warn' | 'info' | 'neutral' }> = {
  down: { label: 'Thumbs down', tone: 'bad' }, not_really: { label: 'Not really', tone: 'warn' }, written: { label: 'Written', tone: 'info' }, auto: { label: 'Auto-flagged', tone: 'neutral' },
};
const REASON: Record<string, string> = { wrong: 'Wrong', outdated: 'Outdated', not_answered: 'Didn’t answer', too_long: 'Too long', other: 'Other' };
const reasonLabel = (r: string) => REASON[r] ?? r;
const PAGE = 40;

/** A ready-to-paste stub for the quality golden set: {q, notes}. History is not recorded with events, so it is left out (add it by hand for follow-ups). */
export function evalCase(r: any): string {
  const when = pac(r.ts, { year: 'numeric', month: 'short', day: 'numeric' });
  const why = [...(r.types as string[]).map((t) => TYPE[t]?.label ?? t), ...(r.reasons as string[]).map(reasonLabel)].join(', ');
  const bits = [`Flagged ${when} (${why}).`];
  if (r.comment) bits.push(`User said: "${String(r.comment).replace(/\s+/g, ' ')}".`);
  if (r.report) bits.push(`Report: "${String(r.report).replace(/\s+/g, ' ')}".`);
  if (r.answer) bits.push(`Bad answer was: "${String(r.answer).replace(/\s+/g, ' ').slice(0, 240)}".`);
  bits.push('TODO: add the expected facts (expect / forbid) and history if this was a follow-up.');
  return JSON.stringify({ q: r.q, notes: bits.join(' ') }, null, 2);
}

async function copyText(t: string): Promise<boolean> {
  try { await navigator.clipboard.writeText(t); return true; } catch {}
  try {
    const ta = document.createElement('textarea'); ta.value = t; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select(); const ok = document.execCommand('copy'); ta.remove(); return ok;
  } catch { return false; }
}

function Row({ r }: { r: any }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState<'ok' | 'fail' | null>(null);
  const snippet = r.answer ? String(r.answer).replace(/\s+/g, ' ').slice(0, 140) : null;
  const copy = async () => { const ok = await copyText(evalCase(r)); setCopied(ok ? 'ok' : 'fail'); setTimeout(() => setCopied(null), 1800); };
  return (
    <li className="border-b border-[var(--line)] last:border-0">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="tap-h flex w-full items-start gap-2 py-2.5 text-left">
        <ChevronDown aria-hidden className={`mt-1 h-4 w-4 shrink-0 text-brand-500 transition ${open ? '' : '-rotate-90'}`} />
        <span className="min-w-0 flex-1">
          <span className="ink-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px]">
            <span className="tabular-nums">{pac(r.ts)} PT</span>
            {r.aud !== 'user' && <Pill tone="warn">{r.aud}</Pill>}
            {r.types.map((t: string) => <Pill key={t} tone={TYPE[t]?.tone}>{TYPE[t]?.label ?? t}</Pill>)}
            {r.reasons.map((x: string) => <Pill key={x}>{reasonLabel(x)}</Pill>)}
          </span>
          <span className="wrap-any mt-1 block text-sm font-semibold">{r.q}</span>
          <span className={`wrap-any mt-0.5 block text-[13px] ${snippet ? 'text-[var(--ink)]' : 'ink-2 italic'}`}>{snippet ? `${snippet}${r.answer.length > 140 ? '…' : ''}` : 'Answer text not recorded for this event.'}</span>
          {(r.comment || r.report) && !open && <span className="wrap-any mt-0.5 block text-[13px] text-brand-700">“{(r.comment || r.report).slice(0, 120)}”</span>}
        </span>
      </button>
      {open && (
        <div className="space-y-3 pb-4 pl-6 text-sm">
          <div>
            <p className="ink-2 text-[11px] font-semibold uppercase tracking-wide">Answer</p>
            {r.answer ? <p className="wrap-any mt-0.5 whitespace-pre-wrap rounded-xl bg-[var(--surface-2)] p-3 text-[13px]">{r.answer}{r.answer.length >= 590 ? '…' : ''}</p> : <p className="ink-2 mt-0.5 text-[13px]">{r.answer_note}</p>}
          </div>
          {r.comment && <div><p className="ink-2 text-[11px] font-semibold uppercase tracking-wide">Comment</p><p className="wrap-any mt-0.5 whitespace-pre-wrap text-[13px]">{r.comment}</p></div>}
          {r.report && <div><p className="ink-2 text-[11px] font-semibold uppercase tracking-wide">Written report{r.report_kind ? ` (${r.report_kind})` : ''}</p><p className="wrap-any mt-0.5 whitespace-pre-wrap text-[13px]">{r.report}</p></div>}
          <p className="ink-2 text-xs">
            {r.route ? `Route ${r.route}${r.route_inferred ? ' (inferred)' : ''}` : 'Server record not found'}
            {r.model ? ` · ${r.model}` : ''}{r.outcome ? ` · outcome ${r.outcome}` : ''}{r.ms != null ? ` · ${(r.ms / 1000).toFixed(1)}s` : ''}{r.n_src != null ? ` · ${r.n_src} source${r.n_src === 1 ? '' : 's'}` : ''}{r.topic ? ` · ${String(r.topic).replace(/_/g, ' ')}` : ''}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={copy} className="tap-box h-9 gap-1.5 rounded-full border border-[var(--line)] bg-white px-3.5 text-[13px] font-semibold text-brand-800 hover:bg-brand-50">
              {copied === 'ok' ? <Check className="h-3.5 w-3.5" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}{copied === 'ok' ? 'Copied' : copied === 'fail' ? 'Copy failed' : 'Copy as eval case'}
            </button>
            <span className="ink-2 text-xs" role="status">{copied === 'ok' ? 'JSON stub for the golden set is on your clipboard.' : ''}</span>
          </div>
        </div>
      )}
    </li>
  );
}

export function Flagged({ api, days, aud }: Pick<Ctx, 'api' | 'days' | 'aud'>) {
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(false);
  const [types, setTypes] = useState<string[]>([]);
  const [reasons, setReasons] = useState<string[]>([]);
  const [shown, setShown] = useState(PAGE);
  const [infra, setInfra] = useState(false);       // degraded-only replies (the model was down) are hidden unless asked for

  const load = useCallback(async () => {
    setLoading(true); setErr('');
    try { setD(await api(`/api/metrics/flagged?days=${days}&aud=${aud}`)); setShown(PAGE); }
    catch (e: any) { setErr(e?.message || 'Could not load flagged answers.'); }
    finally { setLoading(false); }
  }, [api, days, aud]);
  useEffect(() => { load(); }, [load]);

  const toggle = (set: React.Dispatch<React.SetStateAction<string[]>>, v: string) => { set((a) => (a.includes(v) ? a.filter((x) => x !== v) : [...a, v])); setShown(PAGE); };
  const base: any[] = (d?.rows ?? []).filter((r: any) => infra || !r.infra);      // what the filter chips count: the rows that are listed
  const nType = (k: string) => base.filter((r) => r.types.includes(k)).length;
  const tally: Record<string, number> = {};
  base.forEach((r) => r.reasons.forEach((x: string) => { tally[x] = (tally[x] ?? 0) + 1; }));
  const reasonCounts = Object.entries(tally).sort((a, b) => b[1] - a[1]);
  const rows: any[] = base.filter((r: any) => (!types.length || r.types.some((t: string) => types.includes(t))) && (!reasons.length || r.reasons.some((x: string) => reasons.includes(x))));
  const chip = (on: boolean) => `tap-h rounded-full border px-3 py-1 text-xs font-semibold transition ${on ? 'border-brand-900 bg-brand-900 text-white' : 'border-[var(--line)] bg-white text-brand-800 hover:bg-brand-50'}`;

  return (
    <Card title="Flagged answers to review" help="Answers that something went wrong with: thumbs-down (with the reason and comment people gave), 'Not really' on fallback replies, written reports tied to an answer, and answers flagged automatically (the assistant hedged, had no sources, was degraded, or leaned on a single source). Newest first. Uses the range and audience above."
          right={<button type="button" onClick={load} disabled={loading} className="ink-2 tap-box rounded-full px-3 py-1 text-xs font-semibold hover:bg-[var(--surface-2)] disabled:opacity-50">{loading ? 'Loading…' : 'Reload'}</button>}>
      {err && <Notice tone="warn">{err}</Notice>}
      {!d && !err && <div className="shimmer h-20 rounded-xl" aria-busy="true" aria-label="Loading flagged answers" />}
      {d && (
        <div className="space-y-3">
          <p className="text-sm" aria-live="polite">
            <b>{d.total_main}</b> flagged{d.truncated ? ` (newest ${d.rows.filter((r: any) => !r.infra).length} listed)` : ''}
            {' · '}{d.counts.types.down} thumbs-down · {d.counts.types.not_really} “not really” · {d.counts.types.written} written · {d.counts.types.auto - d.total_infra} auto-flagged
            {(types.length > 0 || reasons.length > 0) && <span className="ink-2"> · showing {rows.length}</span>}
          </p>
          {d.total_infra > 0 && (
            <label className="ink-2 flex min-h-11 items-center gap-2 text-xs">
              <input type="checkbox" checked={infra} onChange={(e) => { setInfra(e.target.checked); setShown(PAGE); }} className="h-4 w-4 accent-[var(--color-brand-900)]" />
              Also show {d.total_infra} degraded repl{d.total_infra === 1 ? 'y' : 'ies'} (the model was unavailable; an outage, not an answer-quality problem)
            </label>
          )}
          {d.total > 0 && d.with_answer_text < d.total && <p className="ink-2 text-xs">{d.with_answer_text} of {d.total} flagged have the answer text. Older events and automatic flags that nobody rated were recorded without it; they are marked, not guessed.</p>}
          {d.total > 0 && (
            <div className="space-y-2">
              <div role="group" aria-label="Filter by type" className="flex flex-wrap gap-1.5">
                {Object.entries(TYPE).map(([k, v]) => <button key={k} type="button" aria-pressed={types.includes(k)} onClick={() => toggle(setTypes, k)} className={chip(types.includes(k))}>{v.label} {nType(k)}</button>)}
              </div>
              <div role="group" aria-label="Filter by reason" className="flex flex-wrap gap-1.5">
                {reasonCounts.map(([k, n]) => <button key={k} type="button" aria-pressed={reasons.includes(k)} onClick={() => toggle(setReasons, k)} className={chip(reasons.includes(k))}>{reasonLabel(k)} {n}</button>)}
                {(types.length > 0 || reasons.length > 0) && <button type="button" onClick={() => { setTypes([]); setReasons([]); }} className="tap-h px-2 text-xs font-semibold text-brand-600 hover:underline">Clear filters</button>}
              </div>
            </div>
          )}
          {d.total_main === 0 && !infra ? <Empty>Nothing flagged in this range. That is good news, or nobody has rated an answer yet.</Empty>
            : rows.length === 0 ? <Empty>No flagged answers match these filters.</Empty>
            : (
              <>
                <ul>{rows.slice(0, shown).map((r) => <Row key={r.id} r={r} />)}</ul>
                {rows.length > shown && <button type="button" onClick={() => setShown((s) => s + PAGE)} className="tap-box h-10 rounded-full border border-[var(--line)] bg-white px-4 text-sm font-semibold text-brand-800 hover:bg-brand-50">Show {Math.min(PAGE, rows.length - shown)} more</button>}
              </>
            )}
        </div>
      )}
    </Card>
  );
}
