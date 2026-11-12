// Unit tests for lib/calendar.ts (campus/division filter, day badges, .ics output). No dependencies: `node scripts/calendar-logic.test.mjs` (Node 22.6+ strips the types).
import assert from 'node:assert/strict';
import { appliesToCampus, cancelsClasses, dayBadge, dayHeadline, foldIcsLine, icsFor, onDay, timeLabel } from '../lib/calendar.ts';

let passed = 0;
const t = (name, fn) => { try { fn(); passed++; } catch (e) { console.error('FAIL', name, '\n ', e.message); process.exitCode = 1; } };
const ev = (o) => ({ summary: 'Event', event_date: '2026-11-20', end_date: '2026-11-20', when: '', time: '', all_day: false, start_time: '19:00', end_time: '20:30',
  location: null, description: null, category: 'community', kind: 'event', home_away: null, ...o });

t('campus: San Mateo sees US and whole-school events, not LS/MS only', () => {
  assert.equal(appliesToCampus(ev({ division: ['us'] }), 'san_mateo'), true);
  assert.equal(appliesToCampus(ev({ division: [] }), 'san_mateo'), true);
  assert.equal(appliesToCampus(ev({}), 'san_mateo'), true);
  assert.equal(appliesToCampus(ev({ division: ['ls', 'ms'] }), 'san_mateo'), false);
  assert.equal(appliesToCampus(ev({ division: ['ms'] }), 'hillsborough'), true);
  assert.equal(appliesToCampus(ev({ division: ['us'] }), 'hillsborough'), false);
  assert.equal(appliesToCampus(ev({ division: ['us'] }), null), true);
});
t('no classes: whole school, or only the viewer division', () => {
  assert.equal(cancelsClasses({ no_classes: ['all'] }, 'san_mateo'), true);
  assert.equal(cancelsClasses({ no_classes: ['ls', 'ms'] }, 'san_mateo'), false);
  assert.equal(cancelsClasses({ no_classes: ['ls', 'ms'] }, 'hillsborough'), true);
  assert.equal(cancelsClasses({ no_classes: ['us'] }, null), false);
  assert.equal(cancelsClasses({ no_classes: null }, 'san_mateo'), false);
  assert.equal(cancelsClasses({}, 'san_mateo'), false);
});
t('day badge and headline', () => {
  assert.deepEqual(dayBadge({ day_type: "A'" }), { text: "A'", short: "A'", tone: 'class' });
  assert.deepEqual(dayBadge({ day_type: 'B', schedule: 'community_90' }), { text: 'B', short: 'B', tone: 'special' });
  assert.equal(dayBadge({ kind: 'full_day_event' }).short, 'Full');
  assert.equal(dayBadge({ kind: 'no_classes' }).tone, 'off');
  assert.equal(dayBadge({ kind: 'full_day_event', schedule: 'full_day' }).text, 'Full day');
  assert.equal(dayBadge(undefined), null);
  assert.equal(dayBadge({ kind: 'other' }), null);
  assert.equal(dayHeadline({ day_type: "B'", schedule: 'community_40' }), "B' day · blocks 5, 6, 8, 7 · Community time (40 min) schedule");
  assert.equal(dayHeadline({ kind: 'no_classes' }), 'No classes');
});
t('multi-day events show on each day', () => {
  const e = ev({ event_date: '2026-11-23', end_date: '2026-11-27', all_day: true });
  assert.equal(onDay(e, '2026-11-25'), true);
  assert.equal(onDay(e, '2026-11-28'), false);
});
t('ics: timed event keeps its own end day and a Pacific TZID', () => {
  const s = icsFor(ev({ summary: 'Overnight, trip; "x"', event_date: '2026-10-16', end_date: '2026-10-17', start_time: '19:00', end_time: '08:00' }));
  assert.match(s, /DTSTART;TZID=America\/Los_Angeles:20261016T190000/);
  assert.match(s, /DTEND;TZID=America\/Los_Angeles:20261017T080000/);
  assert.match(s, /SUMMARY:Overnight\\, trip\\; "x"/);
  assert.match(s, /BEGIN:VTIMEZONE/);
});
t('ics: all-day multi-day uses the exclusive end date', () => {
  const s = icsFor(ev({ all_day: true, start_time: null, end_time: null, event_date: '2026-11-23', end_date: '2026-11-27', summary: 'Thanksgiving Break' }));
  assert.match(s, /DTSTART;VALUE=DATE:20261123/);
  assert.match(s, /DTEND;VALUE=DATE:20261128/);
  assert.doesNotMatch(s, /VTIMEZONE/);
});
t('ics: no end time means no DTEND, not a zero-length wrong one on another day', () => {
  const s = icsFor(ev({ end_time: null }));
  assert.doesNotMatch(s, /DTEND/);
});
t('ics: backslashes and newlines are escaped, lines end CRLF, long lines fold at 75 octets', () => {
  const s = icsFor(ev({ description: 'Line one\nLine two with a back\\slash and ' + 'word '.repeat(40) }));
  assert.match(s, /DESCRIPTION:Line one\\nLine two with a back\\\\slash/);
  assert.ok(s.endsWith('\r\n'));
  for (const line of s.split('\r\n')) assert.ok(new TextEncoder().encode(line).length <= 75, line);
  const unfolded = s.replace(/\r\n /g, '');
  assert.match(unfolded, /word word word/);
});
t('ics fold counts octets, not characters', () => {
  const line = 'SUMMARY:' + 'é'.repeat(60);
  for (const part of foldIcsLine(line).split('\r\n')) assert.ok(new TextEncoder().encode(part).length <= 75);
  assert.equal(foldIcsLine(line).replace(/\r\n /g, ''), line);
});
t('time label', () => {
  assert.equal(timeLabel(ev({})), '7 PM – 8:30 PM');
  assert.equal(timeLabel(ev({ all_day: true })), 'All day');
});
console.log(`${passed} calendar-logic checks passed`);
