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
  /** Divisions the event is for ('us' | 'ms' | 'ls'); empty or missing = the whole school. */
  division?: string[] | null;
  /** Divisions that have no classes that day (['all'] = everyone); null when the event does not cancel classes. */
  no_classes?: string[] | null;
}

/** The master schedule's record for one date (from /api/calendar `days`). */
export interface DayInfo { day_type?: string; kind?: string; label?: string; schedule?: string; event?: string }

export type CampusId = 'san_mateo' | 'hillsborough';
const CAMPUS_DIVISIONS: Record<CampusId, string[]> = { san_mateo: ['us'], hillsborough: ['ls', 'ms'] };

/** Does the event concern someone on this campus? San Mateo is the Upper School, Hillsborough the Lower and Middle School.
 *  No campus chosen, or an event with no division marker (whole school), always applies. */
export function appliesToCampus(e: Pick<CalEvent, 'division'>, campus: CampusId | null | undefined): boolean {
  if (!campus || !e.division || e.division.length === 0) return true;
  return e.division.some((d) => CAMPUS_DIVISIONS[campus].includes(d));
}

/** Is there no school for this campus because of this event? (the whole school, or the viewer's division) */
export function cancelsClasses(e: Pick<CalEvent, 'no_classes'>, campus: CampusId | null | undefined): boolean {
  const n = e.no_classes;
  if (!n || n.length === 0) return false;
  if (n.includes('all')) return true;
  return campus ? n.some((d) => CAMPUS_DIVISIONS[campus].includes(d)) : false;
}

const BLOCKS: Record<string, string> = { A: '1, 2, 3, 4', "A'": '1, 2, 4, 3', B: '5, 6, 7, 8', "B'": '5, 6, 8, 7' };
const SPECIAL: Record<string, string> = { community_40: 'Community time (40 min)', community_90: 'Community time (90 min)', full_day: 'Full-day event' };

/** Short badge for a date on the month grid, from the master schedule record. */
export function dayBadge(d: DayInfo | undefined): { text: string; short: string; tone: 'class' | 'special' | 'off' | 'event' } | null {
  if (!d) return null;
  if (d.day_type && BLOCKS[d.day_type]) return { text: d.day_type, short: d.day_type, tone: d.schedule && d.schedule !== 'full_day' ? 'special' : 'class' };
  if (d.kind === 'no_classes') return { text: 'No classes', short: 'Off', tone: 'off' };
  if (d.kind === 'full_day_event') return { text: 'Full day', short: 'Full', tone: 'event' };
  if (d.kind === 'short_day') return { text: 'Early out', short: 'Early', tone: 'event' };
  return null;
}

/** One line for the selected-day panel: "B day · blocks 5, 6, 7, 8 · Community time (90 min) schedule". */
export function dayHeadline(d: DayInfo | undefined): string {
  if (!d) return '';
  const bits: string[] = [];
  if (d.day_type && BLOCKS[d.day_type]) bits.push(`${d.day_type} day`, `blocks ${BLOCKS[d.day_type]}`);
  else if (d.kind === 'no_classes') bits.push('No classes');
  else if (d.kind === 'full_day_event') bits.push('Full-day event, no classes');
  else if (d.kind === 'short_day') bits.push('Early dismissal');
  if (d.schedule && SPECIAL[d.schedule]) bits.push(`${SPECIAL[d.schedule]} schedule`);
  return bits.join(' · ');
}

export const CATEGORIES: { id: string; label: string; dot: string; chip: string; edge: string }[] = [
  { id: 'athletics', label: 'Athletics', dot: 'bg-teal-600', chip: 'bg-teal-600/10 text-teal-800', edge: 'border-l-teal-600' },
  { id: 'academics', label: 'Academics', dot: 'bg-brand-500', chip: 'bg-brand-500/10 text-brand-700', edge: 'border-l-brand-500' },
  { id: 'arts', label: 'Arts', dot: 'bg-violet-500', chip: 'bg-violet-500/10 text-violet-700', edge: 'border-l-violet-500' },
  { id: 'community', label: 'Community', dot: 'bg-sky-500', chip: 'bg-sky-500/10 text-sky-800', edge: 'border-l-sky-500' },
  { id: 'no_school', label: 'No school', dot: 'bg-amber-500', chip: 'bg-amber-500/15 text-amber-800', edge: 'border-l-amber-500' },
  { id: 'other', label: 'Other', dot: 'bg-slate-300', chip: 'bg-slate-300/25 text-slate-600', edge: 'border-l-slate-300' },
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

/** RFC 5545: lines longer than 75 octets are folded (CRLF + one space); long descriptions otherwise break strict importers. */
export function foldIcsLine(line: string): string {
  const enc = new TextEncoder();
  if (enc.encode(line).length <= 75) return line;
  const out: string[] = [];
  let cur = '';
  let used = 0;
  for (const ch of line) {
    const n = enc.encode(ch).length;
    if (used + n > (out.length === 0 ? 75 : 74)) { out.push(cur); cur = ''; used = 0; }
    cur += ch;
    used += n;
  }
  out.push(cur);
  return out.join('\r\n ');
}

/** Builds an .ics file for one event so it can be added to Google/Apple/Outlook calendar. A timed event keeps its own end DAY (an overnight
 *  event ends the next morning), an all-day event uses the exclusive-end convention, text is escaped (backslash, comma, semicolon, newline)
 *  and long lines are folded. */
export function icsFor(e: CalEvent): string {
  const stamp = (d: string, t: string | null) => d.replaceAll('-', '') + (t ? 'T' + t.replace(':', '') + '00' : '');
  const esc = (s: string) => s.replace(/\\/g, '\\\\').replace(/[;,]/g, (c) => '\\' + c).replace(/\r?\n/g, '\\n');
  const h = [...e.summary].reduce((a, c) => (Math.imul(a, 31) + c.charCodeAt(0)) | 0, 7) >>> 0;
  const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//NuevAI//EN', 'CALSCALE:GREGORIAN', ...(e.all_day ? [] : VTIMEZONE), 'BEGIN:VEVENT', `UID:${stamp(e.event_date, e.start_time)}-${h.toString(36)}@nuevai`,
    `DTSTAMP:${new Date().toISOString().replace(/[-:]|\.\d+/g, '')}`, `SUMMARY:${esc(e.summary.trim())}`];
  if (e.all_day) {
    const end = parseISO(e.end_date || e.event_date); end.setDate(end.getDate() + 1);
    lines.push(`DTSTART;VALUE=DATE:${stamp(e.event_date, null)}`, `DTEND;VALUE=DATE:${stamp(iso(end), null)}`);
  } else {
    lines.push(`DTSTART;TZID=America/Los_Angeles:${stamp(e.event_date, e.start_time)}`);
    if (e.end_time) lines.push(`DTEND;TZID=America/Los_Angeles:${stamp(e.end_date || e.event_date, e.end_time)}`);
  }
  if (e.location) lines.push(`LOCATION:${esc(e.location)}`);
  if (e.description) lines.push(`DESCRIPTION:${esc(e.description)}`);
  lines.push('END:VEVENT', 'END:VCALENDAR');
  return lines.map(foldIcsLine).join('\r\n') + '\r\n';
}

export function downloadIcs(e: CalEvent) {
  const url = URL.createObjectURL(new Blob([icsFor(e)], { type: 'text/calendar' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = `${e.summary.replace(/[^\w]+/g, '-').slice(0, 40)}.ics`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
