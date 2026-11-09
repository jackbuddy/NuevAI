'use client';

/**
 * Browser-side state for first-run, hints, recents, followed teams and the install prompt.
 * Everything lives in this browser (localStorage); nothing here identifies anyone, and none of it is sent anywhere
 * except through track() with small non-personal props. Every read/write is wrapped: when storage is blocked the
 * module keeps the same state in memory for the page's lifetime and the app renders normally.
 * Decision rules live in lib/ux-logic.ts (unit-tested).
 */
import { track } from './analytics';
import type { HintId, HintState, Team, Topic } from './ux-logic';
import { emptyHints, isFirstRun, pushRecent, recordHintShown, visitNumber } from './ux-logic';

const UX_KEY = 'nuevai.ux';
const RECENT_KEY = 'nuevai.recent';
const FOLLOW_KEY = 'nuevai.follows';
const UID_KEY = 'nuevai.uid';
const FIRST_KEY = 'nuevai.first';
const VISITS_KEY = 'nuevai.visits';
const ROLE_KEY = 'nuevai.role';
const CAMPUS_KEY = 'nuevai.campus';

export interface Ux {
  hints: HintState;
  used: Record<string, number>;       // features the visitor has tried: ideas, calendar, resources, follow
  answers: number;                    // completed answers, this browser, ever
  topics: Topic[];                    // topics asked about
  onboarded: boolean;                 // the first-run line has done its job
  install: { dismissed: number[]; lastShown: number; installed: boolean };
  whatsNew: { seenId: string | null; shownCount: number; countedId: string | null };
}
const fresh = (): Ux => ({ hints: emptyHints(), used: {}, answers: 0, topics: [], onboarded: false, install: { dismissed: [], lastShown: 0, installed: false }, whatsNew: { seenId: null, shownCount: 0, countedId: null } });

function lsGet(k: string): string | null { try { return localStorage.getItem(k); } catch { return null; } }
function lsSet(k: string, v: string) { try { localStorage.setItem(k, v); } catch {} }

let mem: Ux | null = null;
export function ux(): Ux {
  if (mem) return mem;
  let u = fresh();
  try {
    const raw = lsGet(UX_KEY);
    if (raw) { const j = JSON.parse(raw); u = { ...u, ...j, hints: { ...u.hints, ...(j.hints ?? {}) }, install: { ...u.install, ...(j.install ?? {}) }, whatsNew: { ...u.whatsNew, ...(j.whatsNew ?? {}) } }; }
  } catch {}
  mem = u;
  return u;
}
/** Applies a change and persists it (best effort). */
export function updateUx(fn: (u: Ux) => void) { const u = ux(); fn(u); lsSet(UX_KEY, JSON.stringify(u)); }

export function markUsed(feature: 'ideas' | 'calendar' | 'resources' | 'follow') {
  if (ux().used[feature]) return;
  updateUx((u) => { u.used[feature] = Date.now(); });
}

// ---------------------------------------------------------------- visit / first run

export interface Visit { visit: number; returning: boolean; firstRun: boolean; hasPrefs: boolean }
export function readVisit(): Visit {
  const hasUid = !!lsGet(UID_KEY);
  const visit = visitNumber({ visitsRaw: lsGet(VISITS_KEY), firstIso: lsGet(FIRST_KEY), hasUid, now: Date.now() });
  const hasPrefs = !!(lsGet(ROLE_KEY) || lsGet(CAMPUS_KEY));
  const u = ux();
  return { visit, returning: visit >= 2 || u.answers > 0, hasPrefs, firstRun: isFirstRun({ visit, hasPrefs, answers: u.answers, onboarded: u.onboarded }) };
}

let onboardShown = false;
const UX_EVENT = 'nuevai:ux';
export const onUxChange = (fn: () => void) => { window.addEventListener(UX_EVENT, fn); return () => window.removeEventListener(UX_EVENT, fn); };
const emit = () => { try { window.dispatchEvent(new Event(UX_EVENT)); } catch {} };

export function onboardingShown(variant: string) {
  if (onboardShown) return;
  onboardShown = true;
  track('onboarding_shown', { variant });
}
/** The first-run explanation dissolves for good. Tracked once, and only if it was actually on screen. */
export function endOnboarding(reason: string) {
  if (ux().onboarded) return;
  updateUx((u) => { u.onboarded = true; });
  if (onboardShown) track('onboarding_dismiss', { reason });
  emit();
}

// ---------------------------------------------------------------- answers, recents

export function noteAnswer(topic: Topic) {
  updateUx((u) => { u.answers += 1; if (!u.topics.includes(topic)) u.topics = [...u.topics, topic].slice(-8); u.onboarded = true; });
  emit();
}
export function loadRecent(): string[] {
  try { const j = JSON.parse(lsGet(RECENT_KEY) ?? '[]'); return Array.isArray(j) ? j.filter((x) => typeof x === 'string').slice(0, 8) : []; } catch { return []; }
}
let recentMem: string[] | null = null;
export function recents(): string[] { return (recentMem ??= loadRecent()); }
export function addRecent(q: string) { recentMem = pushRecent(recents(), q); lsSet(RECENT_KEY, JSON.stringify(recentMem)); }
export function clearRecent() { recentMem = []; lsSet(RECENT_KEY, '[]'); }

// ---------------------------------------------------------------- hints

export function hintShown(id: HintId) {
  updateUx((u) => { u.hints = recordHintShown(u.hints, id, Date.now()); });
  track('hint_shown', { id });
}
export function hintResolved(id: HintId, how: 'click' | 'dismiss') {
  updateUx((u) => { u.hints.resolved[id] = how; });
  track(how === 'click' ? 'hint_click' : 'hint_dismiss', { id });
}

// ---------------------------------------------------------------- followed teams

export interface Followed { key: string; label: string }
let followMem: Followed[] | null = null;
function loadFollows(): Followed[] {
  try {
    const j = JSON.parse(lsGet(FOLLOW_KEY) ?? '[]');
    return Array.isArray(j) ? j.filter((x) => x && typeof x.key === 'string' && typeof x.label === 'string').slice(0, 12) : [];
  } catch { return []; }
}
export const getFollows = (): Followed[] => (followMem ??= loadFollows());
const FOLLOW_EVENT = 'nuevai:follows';
export const onFollowsChange = (fn: () => void) => {
  window.addEventListener(FOLLOW_EVENT, fn);
  const st = (e: StorageEvent) => { if (e.key === FOLLOW_KEY) { followMem = loadFollows(); fn(); } };
  window.addEventListener('storage', st);
  return () => { window.removeEventListener(FOLLOW_EVENT, fn); window.removeEventListener('storage', st); };
};
export function toggleFollow(team: Pick<Team, 'key' | 'label'>): boolean {
  const cur = getFollows();
  const on = !cur.some((f) => f.key === team.key);
  followMem = on ? [...cur, { key: team.key, label: team.label }].slice(-12) : cur.filter((f) => f.key !== team.key);
  lsSet(FOLLOW_KEY, JSON.stringify(followMem));
  if (on) markUsed('follow');
  track(on ? 'follow_team' : 'unfollow_team', { team: team.label.slice(0, 60) });
  try { window.dispatchEvent(new Event(FOLLOW_EVENT)); } catch {}
  return on;
}
