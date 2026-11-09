/**
 * Pure decision logic for first-run, hints, install prompt, what's new, starters and teams.
 * No DOM, no storage, no imports: everything takes its inputs as arguments so it can be unit-tested
 * with plain node (scripts/ux-logic.test.mjs). lib/visit.ts is the storage layer on top of this.
 */

export const HOUR = 3_600_000;
export const DAY = 24 * HOUR;

// ---------------------------------------------------------------- topics

export type Topic = 'lunch' | 'games' | 'clubs' | 'school_day' | 'calendar' | 'resources' | 'policy' | 'other';

/** Rough, local-only classification of a question (drives hints, loading labels and starter ranking). */
export function topicOf(q: string): Topic {
  const s = q.toLowerCase();
  if (/\bclubs?\b/.test(s)) return 'clubs';
  if (/\b(lunch|cafe|cafeteria|menu)\b/.test(s)) return 'lunch';
  if (/\b(games?|scrimmages?|matches?|teams?|basketball|soccer|volleyball|tennis|swim(ming)?|baseball|softball|track|sports?|athletics?|varsity)\b/.test(s) && !/\b(game design|video games?)\b/.test(s)) return 'games';
  if (/\b(school|class(es)?)\s+(tomorrow|today|on|monday|tuesday|wednesday|thursday|friday)\b|\b[ab]['’]?[- ]?day\b|\bwhat (kind of )?day\b|\bday type\b|\bno[- ]school\b/.test(s)) return 'school_day';
  if (/\b(forms?|links?|book|booking|portal|tutorial|tour|handbook|resources?|tech|laptop|help ?desk|transcripts?|lost and found)\b/.test(s)) return 'resources';
  if (/\b(when|calendar|schedule|break|holiday|pd days?|dates?|deadline|conferences?|finals?|exams?)\b/.test(s)) return 'calendar';
  if (/\b(policy|policies|late|absent|absence|attendance|honor|dress|grades?|courses?|graduat\w*|requirements?|phones?|credit)\b/.test(s)) return 'policy';
  return 'other';
}

// ---------------------------------------------------------------- visits and first run

/**
 * Which visit is this? Prefer the counter lib/analytics.ts keeps (nuevai.visits); when it is missing
 * (older build, blocked storage) infer from the identity keys: a stored id that predates this session means 2+.
 */
export function visitNumber(i: { visitsRaw: string | null; firstIso: string | null; hasUid: boolean; now: number }): number {
  const n = Math.floor(Number(i.visitsRaw));
  if (Number.isFinite(n) && n >= 1) return n;
  if (!i.hasUid) return 1;
  const first = i.firstIso ? Date.parse(i.firstIso) : NaN;
  if (Number.isFinite(first)) return i.now - first > 30 * 60_000 ? 2 : 1;
  return 2;   // an id with no first-visit stamp predates this feature
}

/** The quiet first-run explanation: only until the visitor has done anything. */
export function isFirstRun(i: { visit: number; hasPrefs: boolean; answers: number; onboarded: boolean }): boolean {
  return i.visit <= 1 && !i.hasPrefs && i.answers === 0 && !i.onboarded;
}

// ---------------------------------------------------------------- hints

export type HintId = 'campus' | 'games_calendar' | 'calendar_tab' | 'resources' | 'ideas';
export interface HintState { shown: Record<string, number>; resolved: Record<string, 'click' | 'dismiss'>; last: number; total: number }
export interface HintCtx {
  topic: Topic;
  answers: number;              // answers completed so far, including the one just finished
  hasCampus: boolean;
  campusInQuestion: boolean;    // the question already named a campus
  used: Record<string, number | boolean | undefined>;   // features already tried: ideas, calendar, resources, follow
  degraded: boolean;
  error: boolean;
}
export const HINT_COOLDOWN_MS = 6 * HOUR;   // global: at most one hint per six hours
export const HINT_LIFETIME_CAP = 6;

export const emptyHints = (): HintState => ({ shown: {}, resolved: {}, last: 0, total: 0 });

/**
 * Picks at most one hint for the answer that just finished, or null.
 * Never on a failed/degraded answer, never during the cooldown, each id at most once ever, a lifetime cap.
 * The first answer stays clean, with one exception: choosing a campus right after a lunch answer that listed both.
 */
export function chooseHint(c: HintCtx, st: HintState, now: number): HintId | null {
  if (c.degraded || c.error) return null;
  if (st.total >= HINT_LIFETIME_CAP) return null;
  if (st.last && now - st.last < HINT_COOLDOWN_MS) return null;
  const fresh = (id: HintId) => !st.shown[id];
  if (c.topic === 'lunch' && !c.hasCampus && !c.campusInQuestion && fresh('campus')) return 'campus';
  if (c.answers < 2) return null;
  if (c.topic === 'games' && fresh('games_calendar') && !c.used.calendar && !c.used.follow) return 'games_calendar';
  if ((c.topic === 'calendar' || c.topic === 'school_day') && fresh('calendar_tab') && !c.used.calendar) return 'calendar_tab';
  if (c.topic === 'resources' && fresh('resources') && !c.used.resources) return 'resources';
  if (c.answers >= 3 && fresh('ideas') && !c.used.ideas) return 'ideas';
  return null;
}

export function recordHintShown(st: HintState, id: HintId, now: number): HintState {
  return { ...st, shown: { ...st.shown, [id]: now }, last: now, total: st.total + 1 };
}

// ---------------------------------------------------------------- install prompt

export interface InstallCtx {
  platform: 'android' | 'ios' | null;
  standalone: boolean;
  visit: number;
  answers: number;
  dismissed: number[];          // timestamps of earlier dismissals
  lastShown: number;
  installed: boolean;
  idle: boolean;                // nothing in progress: empty home, no draft, no answer streaming
  now: number;
}
export const INSTALL_DISMISS_COOLDOWN_MS = 45 * DAY;
export const INSTALL_SHOW_COOLDOWN_MS = 7 * DAY;

/** Only after real value (a second visit or three answers), never mid-task, long cooldowns, gives up after two dismissals. */
export function canShowInstall(c: InstallCtx): boolean {
  if (!c.platform || c.standalone || c.installed || !c.idle) return false;
  if (!(c.visit >= 2 || c.answers >= 3)) return false;
  if (c.dismissed.length >= 2) return false;
  const lastDismiss = c.dismissed.length ? Math.max(...c.dismissed) : 0;
  if (lastDismiss && c.now - lastDismiss < INSTALL_DISMISS_COOLDOWN_MS) return false;
  if (c.lastShown && c.now - c.lastShown < INSTALL_SHOW_COOLDOWN_MS) return false;
  return true;
}

// ---------------------------------------------------------------- what's new

export const WHATS_NEW_SHOW_LIMIT = 3;       // page loads before it retires itself
export const WHATS_NEW_WINDOW_MS = 21 * DAY; // and never after three weeks

/** Shown to someone who used the app before this release; new visitors have nothing to compare with. */
export function whatsNewActive(i: { id: string; releasedAt: number; seenId: string | null; shownCount: number; returning: boolean; now: number }): boolean {
  if (!i.returning || i.seenId === i.id) return false;
  if (i.shownCount >= WHATS_NEW_SHOW_LIMIT) return false;
  return i.now - i.releasedAt < WHATS_NEW_WINDOW_MS;
}

// ---------------------------------------------------------------- starters and recents

export interface Starter { label: string; topic: Topic }
const norm = (s: string) => s.toLowerCase().replace(/[’']/g, '').replace(/[^a-z0-9]+/g, ' ').trim();

/** Starters adapt to what this browser has already asked: unseen topics first, exact repeats last. */
export function rankStarters<T extends Starter>(pool: T[], askedTopics: string[], recent: string[], n = 4): T[] {
  const askedLabels = new Set(recent.map(norm));
  const seen = new Set(askedTopics);
  const unasked = pool.filter((s) => !askedLabels.has(norm(s.label)));
  const rest = pool.filter((s) => askedLabels.has(norm(s.label)));
  const fresh = unasked.filter((s) => !seen.has(s.topic));
  const known = unasked.filter((s) => seen.has(s.topic));
  return [...fresh, ...known, ...rest].slice(0, n);
}

export function pushRecent(list: string[], q: string, max = 8): string[] {
  const t = q.trim().replace(/\s+/g, ' ');
  if (t.length < 3 || t.length > 140) return list.slice(0, max);
  const key = norm(t);
  return [t, ...list.filter((x) => norm(x) !== key)].slice(0, max);
}

// ---------------------------------------------------------------- provenance

const TYPE_LABEL: Record<string, string> = {
  calendar: 'the school calendar', lunch: 'the cafe menu', school_clubs: 'the clubs list', school_day: 'the school schedule',
  calendar_sheet: 'team schedules', school_courses: 'the course catalog', school_advisory: 'advisory info', link_registry: 'school links', school_facts: 'school facts', email: 'a school email',
};
/** "the school calendar", "the cafe menu", otherwise the title of the best document: what an answer was built from. */
export function provenance(sources: { title?: string | null; type?: string }[] | undefined, max = 2): string[] {
  const structured: string[] = [], docs: string[] = [];
  for (const s of sources ?? []) {
    const known = s.type ? TYPE_LABEL[s.type] : undefined;
    const label = known || (s.title ? s.title.replace(/\s*\|\s*The Nueva School$/i, '').trim().slice(0, 48) : '');
    const list = known ? structured : docs;
    if (label && !list.includes(label)) list.push(label);
  }
  // calendar/menu/club data is what an answer is built on; document titles are only named when nothing structured was used
  return (structured.length ? structured : docs).slice(0, max);
}

// ---------------------------------------------------------------- teams

export interface Team { key: string; label: string; opponent: string }
/**
 * "US Girls Basketball - Game CSD Fremont - Home" -> {label: "US Girls Basketball", opponent: "CSD Fremont"}.
 * Parentheses may contain " - " ("MS (7th/8th - VA) Girls' Soccer - Game Menlo School - Away"). Returns null when
 * the summary does not follow the "<team> - <what>" shape.
 */
export function parseTeam(summary: string): Team | null {
  const masked = summary.replace(/\([^)]*\)/g, (m) => m.replace(/ - /g, '\u0001'));
  const parts = masked.split(/\s+-\s+/);
  if (parts.length < 2) return null;
  const tidy = (s: string) => s.replace(/\u0001/g, ' - ').replace(/\s+/g, ' ').trim();
  const label = tidy(parts[0]);
  if (label.length < 4 || label.length > 64) return null;
  let rest = parts.slice(1).map(tidy).filter((p) => !/^(home|away)$/i.test(p));
  const opponent = (rest[0] ?? '').replace(/^(friendship |ccs sportsmanship |scrimmage |first day of )?(game|scrimmages?)\s*/i, '').trim();
  return { key: label.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim(), label, opponent };
}
