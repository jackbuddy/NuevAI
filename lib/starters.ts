/**
 * Starter questions that change with the date. Pure (no DOM, no storage) so scripts/ux-logic.test.mjs can test it.
 * Every question here was checked against the live assistant and answers from the course sheet or the school calendar.
 * Update COMING_UP each school year and TEACH when the master schedule changes.
 */
import type { Topic } from './ux-logic';

export interface DatedStarter { label: string; topic: Topic }

/** One real teacher/course question per day, rotating. Names and courses come from the 2026-27 master schedule. */
const TEACH: DatedStarter[] = [
  { label: 'Does Mara Ballmer teach Biology?', topic: 'courses' },
  { label: 'What does Max Cowan teach?', topic: 'courses' },
  { label: 'Who teaches Physics?', topic: 'courses' },
  { label: 'Who teaches Math 3?', topic: 'courses' },
];

/** The next thing on the calendar people ask about: [last day this question is shown (YYYY-MM-DD), question]. */
const COMING_UP: [string, string][] = [
  ['2026-11-27', 'When is Thanksgiving break?'],
  ['2027-01-01', 'When is winter break?'],
  ['2027-04-02', 'When is spring break?'],
  ['2027-06-04', 'When is the last day of school?'],
];

const dayNumber = (iso: string) => Math.floor(Date.parse(iso + 'T00:00:00Z') / 86_400_000);

export function teacherStarter(todayIso: string): DatedStarter {
  const n = dayNumber(todayIso);
  return TEACH[((n % TEACH.length) + TEACH.length) % TEACH.length];
}

export function comingUpStarter(todayIso: string): DatedStarter {
  const hit = COMING_UP.find(([last]) => todayIso <= last);
  return { label: hit ? hit[1] : 'When is the next no-school day?', topic: 'calendar' };
}
