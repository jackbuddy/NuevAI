export interface CalEvent {
  summary: string;
  event_date: string;      // YYYY-MM-DD
  end_date: string;
  when: string;
  time: string;
  all_day: boolean;
  start_time: string | null;
  end_time: string | null;
  location: string | null;
  description: string | null;
  category: 'athletics' | 'academics' | 'arts' | 'community' | 'no_school' | 'other';
  kind: 'game' | 'practice' | 'event';
  home_away: string | null;
}

export const CATEGORIES: { id: string; label: string; dot: string; chip: string }[] = [
  { id: 'athletics', label: 'Athletics', dot: 'bg-orange-500', chip: 'bg-orange-500/10 text-orange-700 dark:text-orange-300' },
  { id: 'academics', label: 'Academics', dot: 'bg-blue-600', chip: 'bg-blue-600/10 text-blue-700 dark:text-blue-300' },
  { id: 'arts', label: 'Arts', dot: 'bg-fuchsia-500', chip: 'bg-fuchsia-500/10 text-fuchsia-700 dark:text-fuchsia-300' },
  { id: 'community', label: 'Community', dot: 'bg-emerald-500', chip: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300' },
  { id: 'no_school', label: 'No school', dot: 'bg-rose-500', chip: 'bg-rose-500/10 text-rose-700 dark:text-rose-300' },
  { id: 'other', label: 'Other', dot: 'bg-slate-400', chip: 'bg-slate-500/10 text-slate-700 dark:text-slate-300' },
];
export const catMeta = (id: string) => CATEGORIES.find((c) => c.id === id) ?? CATEGORIES[CATEGORIES.length - 1];

export const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
export const parseISO = (s: string) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };

export function fmtTime(hhmm: string | null): string {
  if (!hhmm) return '';
  const [h, m] = hhmm.split(':').map(Number);
  const ap = h >= 12 ? 'PM' : 'AM';
  return `${((h + 11) % 12) + 1}${m ? ':' + String(m).padStart(2, '0') : ''} ${ap}`;
}
export const timeLabel = (e: CalEvent) => (e.all_day ? 'All day' : e.end_time ? `${fmtTime(e.start_time)} – ${fmtTime(e.end_time)}` : fmtTime(e.start_time));

/** Does the event happen on this calendar day (multi-day events span days)? */
export const onDay = (e: CalEvent, day: string) => e.event_date <= day && day <= e.end_date;

/** Builds an .ics file for one event so it can be added to Google/Apple/Outlook calendar. */
export function icsFor(e: CalEvent): string {
  const stamp = (d: string, t: string | null) => d.replaceAll('-', '') + (t ? 'T' + t.replace(':', '') + '00' : '');
  const esc = (s: string) => s.replace(/[\;,]/g, (c) => '\\' + c).replace(/\n/g, '\\n');
  const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//NuevAI//EN', 'BEGIN:VEVENT', `UID:${stamp(e.event_date, e.start_time)}-${e.summary.length}@nuevai`,
    `DTSTAMP:${new Date().toISOString().replace(/[-:]|\.\d+/g, '')}`, `SUMMARY:${esc(e.summary)}`];
  if (e.all_day) {
    const end = parseISO(e.end_date); end.setDate(end.getDate() + 1);
    lines.push(`DTSTART;VALUE=DATE:${stamp(e.event_date, null)}`, `DTEND;VALUE=DATE:${stamp(iso(end), null)}`);
  } else {
    lines.push(`DTSTART;TZID=America/Los_Angeles:${stamp(e.event_date, e.start_time)}`, `DTEND;TZID=America/Los_Angeles:${stamp(e.event_date, e.end_time ?? e.start_time)}`);
  }
  if (e.location) lines.push(`LOCATION:${esc(e.location)}`);
  if (e.description) lines.push(`DESCRIPTION:${esc(e.description)}`);
  lines.push('END:VEVENT', 'END:VCALENDAR');
  return lines.join('\r\n');
}

export function downloadIcs(e: CalEvent) {
  const url = URL.createObjectURL(new Blob([icsFor(e)], { type: 'text/calendar' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = `${e.summary.replace(/[^\w]+/g, '-').slice(0, 40)}.ics`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
