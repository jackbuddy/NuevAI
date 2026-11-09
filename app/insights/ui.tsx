'use client';

/** Small hand-rolled UI + SVG/CSS chart primitives for the Insights dashboard. No chart library: every chart has a text alternative and a data table. */
import { useId, useMemo, useState } from 'react';

// Muted categorical hues, derived from the brand navy and the accent hues already used on Calendar / Resources.
export const PAL = ['#1b4177', '#3d8f86', '#c08a2b', '#b5607a', '#7a68b0', '#4673b3', '#8a9a5b', '#9aa7bd'];
export const colorFor = (i: number) => PAL[i % PAL.length];
const CAT: Record<string, string> = { 'unknown (older event)': '#c5cfdd' };
export const catColor = (name: string, i: number) => CAT[name] ?? colorFor(i);

export const fmtInt = (v: number | null | undefined) => (v == null ? '—' : Math.round(v).toLocaleString());
export const fmtMs = (v: number | null | undefined) => (v == null ? '—' : v >= 10_000 ? `${(v / 1000).toFixed(0)}s` : v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${Math.round(v)}ms`);
export const fmtPct = (v: number | null | undefined, d = 0) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`);
export const fmtTok = (v: number | null | undefined) => (v == null ? '—' : v >= 1000 ? `${(v / 1000).toFixed(v >= 100_000 ? 0 : 1)}k` : String(Math.round(v)));
export const pac = (iso: string | null | undefined, opts: Intl.DateTimeFormatOptions = { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) =>
  iso ? new Intl.DateTimeFormat('en-US', { timeZone: 'America/Los_Angeles', ...opts }).format(new Date(iso)) : '—';
export const ago = (iso: string | null | undefined) => {
  if (!iso) return 'never';
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return 'just now';
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 129_600) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86_400)} d ago`;
};

export function Help({ text, label = 'What does this mean?' }: { text: string; label?: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <span className="inline-block align-middle">
      <button type="button" aria-expanded={open} aria-controls={id} aria-label={label} onClick={() => setOpen(!open)}
        className="ink-2 relative ml-1 inline-grid h-5 w-5 place-items-center rounded-full border border-[var(--line)] align-middle text-[11px] font-bold leading-none after:absolute after:-inset-3 after:content-[''] hover:bg-[var(--surface-2)]">?</button>
      {open && <span id={id} role="note" className="surface-2 ink-2 mt-1 block rounded-lg p-2 text-xs font-normal normal-case tracking-normal">{text}</span>}
    </span>
  );
}

export function Card({ title, help, right, children, className = '' }: { title: string; help?: string; right?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={`surface shadow-soft rounded-2xl p-4 sm:p-5 ${className}`}>
      <div className="mb-3 flex items-start justify-between gap-3">
        <h3 className="text-sm font-semibold">{title}{help && <Help text={help} />}</h3>
        {right}
      </div>
      {children}
    </section>
  );
}

export function Empty({ children = 'No data in this range.' }: { children?: React.ReactNode }) {
  return <p className="ink-2 text-sm">{children}</p>;
}

export function Notice({ tone = 'info', children }: { tone?: 'info' | 'warn'; children: React.ReactNode }) {
  const c = tone === 'warn' ? 'border-amber-500/40 bg-amber-500/10' : 'border-brand-200 bg-brand-50';
  return <div role="note" className={`rounded-2xl border p-3 text-sm ${c}`}>{children}</div>;
}

export function Delta({ cur, prev, better = 'up', rate = false, label }: { cur: number | null | undefined; prev: number | null | undefined; better?: 'up' | 'down' | null; rate?: boolean; label?: string }) {
  if (prev == null || cur == null) return <span className="ink-2 text-xs">{label ?? 'no previous period'}</span>;
  let txt: string, dir: number;
  if (rate) { const d = (cur - prev) * 100; dir = Math.sign(Math.round(d)); txt = `${d > 0 ? '+' : ''}${d.toFixed(0)} pts`; }
  else if (prev === 0) { dir = cur > 0 ? 1 : 0; txt = cur > 0 ? 'new' : 'no change'; }
  else { const d = ((cur - prev) / prev) * 100; dir = Math.sign(Math.round(d)); txt = `${d > 0 ? '+' : ''}${d.toFixed(0)}%`; }
  const good = better == null || dir === 0 ? null : (dir > 0) === (better === 'up');
  const cls = good == null ? 'ink-2' : good ? 'text-emerald-700' : 'text-rose-700';
  return <span className={`text-xs font-semibold ${cls}`} title={`previous period: ${rate ? fmtPct(prev) : fmtInt(prev)}`}>{dir > 0 ? '▲' : dir < 0 ? '▼' : '■'} {txt} <span className="ink-2 font-normal">vs previous</span></span>;
}

export function Spark({ values, color = PAL[0], label }: { values: (number | null)[]; color?: string; label: string }) {
  const nums = values.map((v) => v ?? 0);
  if (nums.length < 2 || nums.every((v) => v === 0)) return <div className="h-7" aria-hidden />;
  const max = Math.max(...nums, 1);
  const pts = nums.map((v, i) => `${(i / (nums.length - 1)) * 100},${26 - (v / max) * 24}`);
  return (
    <svg viewBox="0 0 100 28" preserveAspectRatio="none" className="h-7 w-full" role="img" aria-label={`${label}: ${nums.join(', ')}`}>
      <polygon points={`0,28 ${pts.join(' ')} 100,28`} fill={color} opacity=".12" />
      <polyline points={pts.join(' ')} fill="none" stroke={color} strokeWidth="1.6" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  );
}

export function Kpi({ label, value, sub, help, delta, spark, sparkLabel, small }: { label: string; value: React.ReactNode; sub?: React.ReactNode; help?: string; delta?: React.ReactNode; spark?: (number | null)[]; sparkLabel?: string; small?: boolean }) {
  return (
    <div className="surface shadow-soft flex min-w-0 flex-col rounded-2xl p-4">
      <p className="ink-2 text-xs font-medium">{label}{help && <Help text={help} />}</p>
      <p className={`wrap-any mt-1 font-bold tabular-nums ${small ? 'text-lg' : 'text-3xl'}`}>{value}</p>
      {delta && <div className="mt-0.5">{delta}</div>}
      {sub && <p className="ink-2 mt-0.5 text-xs">{sub}</p>}
      {spark && <div className="mt-2"><Spark values={spark} label={sparkLabel ?? label} /></div>}
    </div>
  );
}

/** Collapsible text alternative for a chart. */
export function DataTable({ head, rows, summary = 'View as table' }: { head: string[]; rows: (string | number | null)[][]; summary?: string }) {
  return (
    <details className="mt-2 text-xs">
      <summary className="ink-2 cursor-pointer select-none rounded py-1">{summary}</summary>
      <div className="scroll-thin mt-1 max-h-56 overflow-auto">
        <table className="w-full text-left">
          <thead><tr>{head.map((h) => <th key={h} className="ink-2 sticky top-0 bg-[var(--surface)] py-1 pr-3 font-medium">{h}</th>)}</tr></thead>
          <tbody>{rows.map((r, i) => <tr key={i} className="border-t border-[var(--line)]">{r.map((c, j) => <td key={j} className="py-1 pr-3 tabular-nums">{c ?? '—'}</td>)}</tr>)}</tbody>
        </table>
      </div>
    </details>
  );
}

/** Keyboard-navigable readout shared by the bar charts: arrow keys move the cursor, the readout is announced politely. */
function useCursor(n: number) {
  const [i, setI] = useState<number | null>(null);
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowRight') { setI((p) => Math.min(n - 1, (p ?? -1) + 1)); e.preventDefault(); }
    else if (e.key === 'ArrowLeft') { setI((p) => Math.max(0, (p ?? n) - 1)); e.preventDefault(); }
    else if (e.key === 'Escape' || e.key === 'Home') setI(e.key === 'Home' ? 0 : null);
    else if (e.key === 'End') setI(n - 1);
  };
  return { i, setI, onKeyDown };
}

export interface BarDatum { label: string; value: number; tip?: string }

export function Bars({ data, color = PAL[0], height = 120, unit = '', title, tableHead = ['Bucket', 'Value'] }: { data: BarDatum[]; color?: string; height?: number; unit?: string; title: string; tableHead?: string[] }) {
  const max = Math.max(1, ...data.map((d) => d.value));
  const cur = useCursor(data.length);
  const step = Math.max(1, Math.ceil(data.length / 8));
  if (!data.length) return <Empty />;
  const total = data.reduce((a, d) => a + d.value, 0);
  const active = cur.i != null ? data[cur.i] : null;
  return (
    <div>
      <p className="ink-2 mb-1 min-h-4 text-xs" aria-live="polite">{active ? (active.tip ?? `${active.label}: ${fmtInt(active.value)}${unit}`) : `Total ${fmtInt(total)}${unit}. Hover or use the arrow keys to read a bar.`}</p>
      <div tabIndex={0} role="group" aria-label={`${title}. ${data.map((d) => `${d.label} ${d.value}`).join(', ')}`} onKeyDown={cur.onKeyDown} onBlur={() => cur.setI(null)} onMouseLeave={() => cur.setI(null)}
        className="flex items-end gap-[3px] rounded-lg outline-offset-4" style={{ height }}>
        {data.map((d, k) => (
          <div key={k} className="flex h-full min-w-0 flex-1 flex-col justify-end" onMouseEnter={() => cur.setI(k)} title={d.tip ?? `${d.label}: ${d.value}${unit}`}>
            <div className="w-full rounded-t-[3px]" style={{ height: `${d.value ? Math.max(3, (d.value / max) * 100) : 0}%`, background: color, opacity: cur.i == null || cur.i === k ? 1 : 0.45, minHeight: d.value ? 3 : 0 }} />
          </div>
        ))}
      </div>
      <div className="mt-1 flex gap-[3px]" aria-hidden>
        {data.map((d, k) => <span key={k} className="ink-2 min-w-0 flex-1 overflow-visible whitespace-nowrap text-center text-[9px] leading-none">{k % step === 0 ? d.label : ''}</span>)}
      </div>
      <DataTable head={tableHead} rows={data.map((d) => [d.label, d.value])} />
    </div>
  );
}

export function Stacked({ data, title, height = 130, order }: { data: { label: string; parts: Record<string, number> }[]; title: string; height?: number; order?: string[] }) {
  const names = useMemo(() => {
    const t: Record<string, number> = {};
    data.forEach((d) => Object.entries(d.parts).forEach(([k, v]) => { t[k] = (t[k] ?? 0) + v; }));
    return order ?? Object.keys(t).sort((a, b) => t[b] - t[a]);
  }, [data, order]);
  const max = Math.max(1, ...data.map((d) => Object.values(d.parts).reduce((a, b) => a + b, 0)));
  const cur = useCursor(data.length);
  const step = Math.max(1, Math.ceil(data.length / 8));
  if (!names.length) return <Empty />;
  const readout = (d: { label: string; parts: Record<string, number> }) => `${d.label}: ${names.filter((n) => d.parts[n]).map((n) => `${n} ${d.parts[n]}`).join(', ') || 'none'}`;
  return (
    <div>
      <p className="ink-2 mb-1 min-h-4 text-xs" aria-live="polite">{cur.i != null ? readout(data[cur.i]) : 'Hover or use the arrow keys to read a bar.'}</p>
      <div tabIndex={0} role="group" aria-label={`${title}. ${data.map(readout).join('; ')}`} onKeyDown={cur.onKeyDown} onBlur={() => cur.setI(null)} onMouseLeave={() => cur.setI(null)}
        className="flex items-end gap-[3px] rounded-lg outline-offset-4" style={{ height }}>
        {data.map((d, k) => {
          const sum = Object.values(d.parts).reduce((a, b) => a + b, 0);
          return (
            <div key={k} className="flex h-full min-w-0 flex-1 flex-col justify-end" onMouseEnter={() => cur.setI(k)} title={readout(d)} style={{ opacity: cur.i == null || cur.i === k ? 1 : 0.5 }}>
              <div className="flex w-full flex-col-reverse overflow-hidden rounded-t-[3px]" style={{ height: `${(sum / max) * 100}%` }}>
                {names.map((n, ni) => d.parts[n] ? <div key={n} style={{ flex: d.parts[n], background: catColor(n, ni) }} /> : null)}
              </div>
            </div>
          );
        })}
      </div>
      <div className="mt-1 flex gap-[3px]" aria-hidden>{data.map((d, k) => <span key={k} className="ink-2 min-w-0 flex-1 whitespace-nowrap text-center text-[9px] leading-none">{k % step === 0 ? d.label : ''}</span>)}</div>
      <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs">{names.map((n, i) => <li key={n} className="flex items-center gap-1"><span aria-hidden className="h-2.5 w-2.5 rounded-sm" style={{ background: catColor(n, i) }} /><span className="wrap-any">{n}</span></li>)}</ul>
      <DataTable head={['Bucket', ...names]} rows={data.map((d) => [d.label, ...names.map((n) => d.parts[n] ?? 0)])} />
    </div>
  );
}

export function Heat({ matrix, rows, cols, title }: { matrix: number[][]; rows: string[]; cols: string[]; title: string }) {
  const max = Math.max(1, ...matrix.flat());
  const [sel, setSel] = useState<string>('');
  const total = matrix.flat().reduce((a, b) => a + b, 0);
  if (!total) return <Empty />;
  return (
    <div>
      <p className="ink-2 mb-1 min-h-4 text-xs" aria-live="polite">{sel || 'Hover or focus a cell to read it.'}</p>
      <div className="scroll-thin overflow-x-auto pb-1">
        <div className="min-w-[520px]" role="table" aria-label={title}>
          {matrix.map((r, ri) => (
            <div key={ri} className="flex items-center gap-[2px]" role="row">
              <span className="ink-2 w-8 shrink-0 text-[10px]" role="rowheader">{rows[ri]}</span>
              {r.map((v, ci) => {
                const t = `${rows[ri]} ${cols[ci]}: ${v} question${v === 1 ? '' : 's'}`;
                return <button key={ci} type="button" role="cell" aria-label={t} title={t} onMouseEnter={() => setSel(t)} onFocus={() => setSel(t)}
                  className="tap-box !min-h-0 !min-w-0 h-5 flex-1 rounded-[3px]" style={{ background: v ? `rgba(27,65,119,${0.12 + 0.88 * (v / max)})` : 'var(--surface-2)' }} />;
              })}
            </div>
          ))}
          <div className="ml-8 mt-1 flex gap-[2px]" aria-hidden>{cols.map((c, i) => <span key={i} className="ink-2 flex-1 text-center text-[9px]">{i % 3 === 0 ? c : ''}</span>)}</div>
        </div>
      </div>
      <DataTable head={['Day', ...cols]} rows={matrix.map((r, i) => [rows[i], ...r])} />
    </div>
  );
}

export interface HRow { label: string; value: number; sub?: string; right?: string; onClick?: () => void; expanded?: boolean; children?: React.ReactNode }

export function HBars({ rows, max, color = PAL[0], empty = 'No data in this range.' }: { rows: HRow[]; max?: number; color?: string; empty?: string }) {
  if (!rows.length) return <Empty>{empty}</Empty>;
  const m = max ?? Math.max(1, ...rows.map((r) => r.value));
  return (
    <ul className="space-y-1.5 text-sm">
      {rows.map((r, i) => (
        <li key={`${r.label}-${i}`}>
          {(() => {
            const inner = (
              <>
                <span className="flex items-baseline justify-between gap-3"><span className="wrap-any min-w-0 text-left">{r.label}</span><span className="ink-2 shrink-0 tabular-nums text-xs">{r.right ?? fmtInt(r.value)}</span></span>
                <span className="surface-2 mt-0.5 block h-1.5 overflow-hidden rounded-full"><span className="block h-full rounded-full" style={{ width: `${Math.max(2, (r.value / m) * 100)}%`, background: color }} /></span>
                {r.sub && <span className="ink-2 mt-0.5 block text-left text-xs">{r.sub}</span>}
              </>
            );
            return r.onClick ? <button type="button" onClick={r.onClick} aria-expanded={!!r.expanded} className="tap-box block w-full rounded-lg py-1 text-left hover:bg-[var(--surface-2)]">{inner}</button> : <div>{inner}</div>;
          })()}
          {r.expanded && r.children}
        </li>
      ))}
    </ul>
  );
}

export function Pill({ children, tone = 'neutral' }: { children: React.ReactNode; tone?: 'neutral' | 'good' | 'warn' | 'bad' | 'info' }) {
  const c = { neutral: 'surface-2 ink-2', good: 'bg-emerald-500/10 text-emerald-800', warn: 'bg-amber-500/15 text-amber-800', bad: 'bg-rose-500/10 text-rose-800', info: 'bg-brand-100 text-brand-800' }[tone];
  return <span className={`inline-block rounded-full px-2 py-0.5 text-[11px] font-semibold ${c}`}>{children}</span>;
}

export function Meter({ value, max, label, warnAt = 0.8 }: { value: number; max: number; label: string; warnAt?: number }) {
  const p = Math.min(1, value / max);
  return (
    <div role="meter" aria-valuemin={0} aria-valuemax={max} aria-valuenow={Math.min(value, max)} aria-label={label}>
      <div className="surface-2 h-2.5 overflow-hidden rounded-full"><div className="h-full rounded-full" style={{ width: `${Math.max(p * 100, value ? 1.5 : 0)}%`, background: p >= 1 ? '#b5607a' : p >= warnAt ? '#c08a2b' : PAL[1] }} /></div>
    </div>
  );
}

export function SimpleTable({ head, rows, empty = 'No data in this range.', caption }: { head: string[]; rows: React.ReactNode[][]; empty?: string; caption?: string }) {
  if (!rows.length) return <Empty>{empty}</Empty>;
  return (
    <div className="scroll-thin overflow-x-auto">
      <table className="w-full min-w-max text-left text-sm">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead><tr>{head.map((h) => <th key={h} scope="col" className="ink-2 whitespace-nowrap py-1.5 pr-4 text-xs font-medium">{h}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i} className="border-t border-[var(--line)] align-top">{r.map((c, j) => <td key={j} className="py-1.5 pr-4 tabular-nums">{c}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}
