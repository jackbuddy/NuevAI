'use client';

import { useEffect, useId, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { CAMPUSES, Campus, Role } from '@/lib/chat';

export const ROLES: { id: Role; label: string }[] = [{ id: 'student', label: 'Student' }, { id: 'parent', label: 'Parent' }, { id: 'staff', label: 'Faculty & staff' }];

/**
 * One quiet line ("Student · San Mateo") that opens a small popover with the two choices.
 * Both preferences live in this browser only; nothing here identifies anyone.
 */
export function Preferences({ role, campus, onRole, onCampus, align = 'center' }: {
  role: Role | null; campus: Campus | null;
  onRole: (r: Role | null) => void; onCampus: (c: Campus | null) => void;
  align?: 'center' | 'left';
}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const id = useId();

  useEffect(() => {
    if (!open) return;
    const down = (e: MouseEvent | TouchEvent) => { if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false); };
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { setOpen(false); btn.current?.focus(); } };
    document.addEventListener('mousedown', down);
    document.addEventListener('touchstart', down);
    document.addEventListener('keydown', key);
    return () => { document.removeEventListener('mousedown', down); document.removeEventListener('touchstart', down); document.removeEventListener('keydown', key); };
  }, [open]);

  const roleLabel = ROLES.find((r) => r.id === role)?.label;
  const campusLabel = CAMPUSES.find((c) => c.id === campus)?.label;
  const summary = [roleLabel, campusLabel].filter(Boolean).join(' · ');
  const pill = (on: boolean) => `tap-box h-9 rounded-full px-3.5 text-[13px] font-semibold transition max-sm:h-11 ${on ? 'bg-brand-900 text-white' : 'surface-2 text-brand-800 hover:bg-brand-100'}`;

  return (
    <div ref={wrap} className={`relative inline-block ${align === 'center' ? 'text-center' : 'text-left'}`}>
      <button ref={btn} type="button" aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => setOpen((o) => !o)}
              className="tap-h inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[12px] font-medium text-brand-700 transition hover:bg-brand-100">
        {summary ? <span className="font-semibold text-brand-800">{summary}</span> : <span>Personalize<span className="hidden sm:inline"> · role &amp; campus</span></span>}
        <ChevronDown className={`h-3.5 w-3.5 transition ${open ? 'rotate-180' : ''}`} aria-hidden />
      </button>
      {open && (
        <div id={id} role="dialog" aria-label="Your role and campus"
             className={`surface shadow-lift animate-rise absolute z-30 mt-1 w-[min(20rem,calc(100vw-2rem))] rounded-2xl p-4 text-left ${align === 'center' ? 'left-1/2 -translate-x-1/2' : 'left-0'}`}>
          <p className="mb-2 text-[11px] font-bold uppercase tracking-wider text-brand-500">I’m a</p>
          <div role="group" aria-label="Role" className="flex flex-wrap gap-1.5">
            {ROLES.map((r) => <button key={r.id} type="button" aria-pressed={role === r.id} onClick={() => onRole(role === r.id ? null : r.id)} className={pill(role === r.id)}>{r.label}</button>)}
          </div>
          <p className="mb-2 mt-4 text-[11px] font-bold uppercase tracking-wider text-brand-500">Campus</p>
          <div role="group" aria-label="Campus" className="flex flex-wrap gap-1.5">
            {CAMPUSES.map((c) => <button key={c.id} type="button" aria-pressed={campus === c.id} onClick={() => onCampus(campus === c.id ? null : c.id)} className={pill(campus === c.id)}>{c.label}</button>)}
          </div>
          <p className="ink-2 mt-3 text-[11px] leading-snug">Used for suggestions and lunch menus. Stays on this device.</p>
          <div className="mt-2 flex justify-between">
            {(role || campus) ? <button type="button" onClick={() => { onRole(null); onCampus(null); }} className="tap-h text-[12px] font-semibold text-brand-600 hover:underline">Clear</button> : <span />}
            <button type="button" onClick={() => { setOpen(false); btn.current?.focus(); }} className="tap-h text-[12px] font-semibold text-brand-700 hover:underline">Done</button>
          </div>
        </div>
      )}
    </div>
  );
}
