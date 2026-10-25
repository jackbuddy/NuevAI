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
  { id: 'athletics', label: 'Athletics', dot: 'bg-brand-800', chip: 'bg-brand-800/10 text-brand-800' },
  { id: 'academics', label: 'Academics', dot: 'bg-brand-500', chip: 'bg-brand-500/10 text-brand-600' },
  { id: 'arts', label: 'Arts', dot: 'bg-brand-300', chip: 'bg-brand-300/25 text-brand-700' },
  { id: 'community', label: 'Community', dot: 'bg-slate-400', chip: 'bg-slate-400/15 text-slate-600' },
  { id: 'no_school', label: 'No school', dot: 'bg-amber-500', chip: 'bg-amber-500/15 text-amber-800' },
  { id: 'other', label: 'Other', dot: 'bg-slate-300', chip: 'bg-slate-300/25 text-slate-600' },
];
export const catMeta = (id: string) => CATEGORIES.find((c) => c.id === id) ?? CATEGORIES[CATEGORIES.length - 1];

export const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
export const SCHOOL_TZ = 'America/Los_Angeles';
/** The current date/hour on the San Mateo campus, whatever timezone the visitor's device is in. */
export function nowLA(d: Date = new Date()): { date: string; hour: number } {
  try {
    const p = Object.fromEntries(new Intl.DateTimeFormat('en-US', { timeZone: SCHOOL_TZ, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', hourCycle: 'h23' }).formatToParts(d).map((x) => [x.type, x.value]));
    return { date: `${p.year}-${p.month}-${p.day}`, hour: Number(p.hour) };
  } catch { return { date: iso(d), hour: d.getHours() }; }
}
export const todayLA = () => nowLA().date;
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

// Pacific time rules (US DST), so Outlook and others place timed events correctly
const VTIMEZONE = ['BEGIN:VTIMEZONE', 'TZID:America/Los_Angeles',
  'BEGIN:DAYLIGHT', 'TZOFFSETFROM:-0800', 'TZOFFSETTO:-0700', 'TZNAME:PDT', 'DTSTART:19700308T020000', 'RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU', 'END:DAYLIGHT',
  'BEGIN:STANDARD', 'TZOFFSETFROM:-0700', 'TZOFFSETTO:-0800', 'TZNAME:PST', 'DTSTART:19701101T020000', 'RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU', 'END:STANDARD',
  'END:VTIMEZONE'];

/** Builds an .ics file for one event so it can be added to Google/Apple/Outlook calendar. */
export function icsFor(e: CalEvent): string {
  const stamp = (d: string, t: string | null) => d.replaceAll('-', '') + (t ? 'T' + t.replace(':', '') + '00' : '');
  const esc = (s: string) => s.replace(/[\;,]/g, (c) => '\\' + c).replace(/\n/g, '\\n');
  const h = [...e.summary].reduce((a, c) => (Math.imul(a, 31) + c.charCodeAt(0)) | 0, 7) >>> 0;
  const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//NuevAI//EN', ...(e.all_day ? [] : VTIMEZONE), 'BEGIN:VEVENT', `UID:${stamp(e.event_date, e.start_time)}-${h.toString(36)}@nuevai`,
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
