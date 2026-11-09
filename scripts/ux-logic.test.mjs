// Unit tests for lib/ux-logic.ts. No dependencies: `node scripts/ux-logic.test.mjs` (Node 22.6+ strips the types).
import assert from 'node:assert/strict';
import {
  DAY, HOUR, HINT_COOLDOWN_MS, HINT_LIFETIME_CAP, emptyHints, recordHintShown, chooseHint, topicOf, visitNumber, isFirstRun,
  canShowInstall, whatsNewActive, rankStarters, pushRecent, provenance, parseTeam,
} from '../lib/ux-logic.ts';

let passed = 0;
const t = (name, fn) => { try { fn(); passed++; } catch (e) { console.error('FAIL', name, '\n ', e.message); process.exitCode = 1; } };
const NOW = Date.parse('2026-11-09T18:00:00Z');

// ---- topics
t('topic: clubs beats lunch', () => assert.equal(topicOf('What clubs meet at lunch?'), 'clubs'));
t('topic: lunch', () => assert.equal(topicOf('What’s for lunch today?'), 'lunch'));
t('topic: games', () => { assert.equal(topicOf('What games are this week?'), 'games'); assert.equal(topicOf('varsity basketball schedule'), 'games'); });
t('topic: school day', () => { assert.equal(topicOf('Is there school tomorrow?'), 'school_day'); assert.equal(topicOf('is tomorrow an A day or B day'), 'school_day'); assert.equal(topicOf("what's an A' day"), 'school_day'); });
t('topic: resources', () => assert.equal(topicOf('How do I book a campus tour?'), 'resources'));
t('topic: calendar', () => assert.equal(topicOf('When is the next PD day?'), 'calendar'));
t('topic: policy', () => assert.equal(topicOf('What is the attendance policy?'), 'policy'));
t('topic: other', () => assert.equal(topicOf('hello there'), 'other'));

// ---- visits / first run
t('visit: counter wins', () => assert.equal(visitNumber({ visitsRaw: '4', firstIso: null, hasUid: true, now: NOW }), 4));
t('visit: new browser', () => assert.equal(visitNumber({ visitsRaw: null, firstIso: null, hasUid: false, now: NOW }), 1));
t('visit: first session, no counter', () => assert.equal(visitNumber({ visitsRaw: null, firstIso: new Date(NOW - 5 * 60_000).toISOString(), hasUid: true, now: NOW }), 1));
t('visit: next day, no counter', () => assert.equal(visitNumber({ visitsRaw: null, firstIso: new Date(NOW - DAY).toISOString(), hasUid: true, now: NOW }), 2));
t('visit: id without first stamp is returning', () => assert.equal(visitNumber({ visitsRaw: 'abc', firstIso: null, hasUid: true, now: NOW }), 2));
t('visit: junk counter ignored', () => assert.equal(visitNumber({ visitsRaw: '0', firstIso: null, hasUid: false, now: NOW }), 1));
t('first run: brand new', () => assert.equal(isFirstRun({ visit: 1, hasPrefs: false, answers: 0, onboarded: false }), true));
t('first run: dissolves after an answer / pref / second visit / dismissal', () => {
  assert.equal(isFirstRun({ visit: 1, hasPrefs: false, answers: 1, onboarded: false }), false);
  assert.equal(isFirstRun({ visit: 1, hasPrefs: true, answers: 0, onboarded: false }), false);
  assert.equal(isFirstRun({ visit: 2, hasPrefs: false, answers: 0, onboarded: false }), false);
  assert.equal(isFirstRun({ visit: 1, hasPrefs: false, answers: 0, onboarded: true }), false);
});

// ---- hints
const base = { topic: 'other', answers: 2, hasCampus: false, campusInQuestion: false, used: {}, degraded: false, error: false };
t('hint: first answer stays clean', () => assert.equal(chooseHint({ ...base, topic: 'games', answers: 1 }, emptyHints(), NOW), null));
t('hint: campus allowed on a first lunch answer', () => assert.equal(chooseHint({ ...base, topic: 'lunch', answers: 1 }, emptyHints(), NOW), 'campus'));
t('hint: no campus hint when campus set or named', () => {
  assert.equal(chooseHint({ ...base, topic: 'lunch', answers: 1, hasCampus: true }, emptyHints(), NOW), null);
  assert.equal(chooseHint({ ...base, topic: 'lunch', answers: 1, campusInQuestion: true }, emptyHints(), NOW), null);
});
t('hint: games -> calendar, unless already used', () => {
  assert.equal(chooseHint({ ...base, topic: 'games' }, emptyHints(), NOW), 'games_calendar');
  assert.equal(chooseHint({ ...base, topic: 'games', used: { calendar: 1 } }, emptyHints(), NOW), null);
  assert.equal(chooseHint({ ...base, topic: 'games', used: { follow: 1 } }, emptyHints(), NOW), null);
});
t('hint: resources and calendar relevance', () => {
  assert.equal(chooseHint({ ...base, topic: 'resources' }, emptyHints(), NOW), 'resources');
  assert.equal(chooseHint({ ...base, topic: 'school_day' }, emptyHints(), NOW), 'calendar_tab');
  assert.equal(chooseHint({ ...base, topic: 'calendar', used: { calendar: 1 } }, emptyHints(), NOW), null);
});
t('hint: ideas after three answers only', () => {
  assert.equal(chooseHint({ ...base, answers: 2 }, emptyHints(), NOW), null);
  assert.equal(chooseHint({ ...base, answers: 3 }, emptyHints(), NOW), 'ideas');
  assert.equal(chooseHint({ ...base, answers: 3, used: { ideas: 1 } }, emptyHints(), NOW), null);
});
t('hint: never on failed or degraded answers', () => {
  assert.equal(chooseHint({ ...base, answers: 3, degraded: true }, emptyHints(), NOW), null);
  assert.equal(chooseHint({ ...base, answers: 3, error: true }, emptyHints(), NOW), null);
});
t('hint: global cooldown, then released', () => {
  const st = recordHintShown(emptyHints(), 'campus', NOW);
  assert.equal(chooseHint({ ...base, answers: 3 }, st, NOW + HINT_COOLDOWN_MS - 1), null);
  assert.equal(chooseHint({ ...base, answers: 3 }, st, NOW + HINT_COOLDOWN_MS), 'ideas');
});
t('hint: each id once ever', () => {
  const st = recordHintShown(emptyHints(), 'ideas', NOW - 30 * DAY);
  assert.equal(chooseHint({ ...base, answers: 5 }, st, NOW), null);
});
t('hint: lifetime cap', () => {
  const st = { ...emptyHints(), total: HINT_LIFETIME_CAP, last: NOW - 30 * DAY };
  assert.equal(chooseHint({ ...base, answers: 5 }, st, NOW), null);
});
t('hint: recording increments', () => {
  const st = recordHintShown(emptyHints(), 'resources', NOW);
  assert.deepEqual([st.total, st.last, st.shown.resources], [1, NOW, NOW]);
});

// ---- install
const ic = { platform: 'android', standalone: false, visit: 2, answers: 0, dismissed: [], lastShown: 0, installed: false, idle: true, now: NOW };
t('install: needs demonstrated value', () => {
  assert.equal(canShowInstall({ ...ic, visit: 1, answers: 2 }), false);
  assert.equal(canShowInstall({ ...ic, visit: 1, answers: 3 }), true);
  assert.equal(canShowInstall(ic), true);
});
t('install: never mid-task, in standalone, installed or without a platform', () => {
  assert.equal(canShowInstall({ ...ic, idle: false }), false);
  assert.equal(canShowInstall({ ...ic, standalone: true }), false);
  assert.equal(canShowInstall({ ...ic, installed: true }), false);
  assert.equal(canShowInstall({ ...ic, platform: null }), false);
});
t('install: dismissal cooldown, then at most two dismissals', () => {
  assert.equal(canShowInstall({ ...ic, dismissed: [NOW - 10 * DAY] }), false);
  assert.equal(canShowInstall({ ...ic, dismissed: [NOW - 46 * DAY] }), true);
  assert.equal(canShowInstall({ ...ic, dismissed: [NOW - 200 * DAY, NOW - 100 * DAY] }), false);
});
t('install: shown-but-ignored cooldown', () => {
  assert.equal(canShowInstall({ ...ic, lastShown: NOW - 2 * DAY }), false);
  assert.equal(canShowInstall({ ...ic, lastShown: NOW - 8 * DAY }), true);
});

// ---- what's new
const wn = { id: 'v1', releasedAt: NOW - 2 * DAY, seenId: null, shownCount: 0, returning: true, now: NOW };
t('whatsnew: once for returning users', () => {
  assert.equal(whatsNewActive(wn), true);
  assert.equal(whatsNewActive({ ...wn, seenId: 'v1' }), false);
  assert.equal(whatsNewActive({ ...wn, seenId: 'v0' }), true);
});
t('whatsnew: never for new visitors, retires after N shows or three weeks', () => {
  assert.equal(whatsNewActive({ ...wn, returning: false }), false);
  assert.equal(whatsNewActive({ ...wn, shownCount: 3 }), false);
  assert.equal(whatsNewActive({ ...wn, releasedAt: NOW - 22 * DAY }), false);
});

// ---- starters / recents
const pool = [{ label: 'What games are this week?', topic: 'games' }, { label: 'What’s for lunch today?', topic: 'lunch' }, { label: 'Is there school tomorrow?', topic: 'school_day' }, { label: 'Which clubs meet today?', topic: 'clubs' }];
t('starters: untouched topics first, exact repeats last', () => {
  const r = rankStarters(pool, ['lunch'], ['whats for lunch today'], 4).map((s) => s.topic);
  assert.deepEqual(r, ['games', 'school_day', 'clubs', 'lunch']);
});
t('starters: nothing asked keeps order; n limits', () => {
  assert.deepEqual(rankStarters(pool, [], [], 4).map((s) => s.topic), ['games', 'lunch', 'school_day', 'clubs']);
  assert.equal(rankStarters(pool, [], [], 2).length, 2);
});
t('recents: newest first, deduped, capped, junk skipped', () => {
  let l = [];
  l = pushRecent(l, 'What’s for lunch today?');
  l = pushRecent(l, 'Is there school tomorrow?');
  l = pushRecent(l, '  whats   for lunch today ');
  assert.deepEqual(l, ['whats for lunch today', 'Is there school tomorrow?']);
  assert.deepEqual(pushRecent(l, 'hi'), l);
  let big = []; for (let i = 0; i < 12; i++) big = pushRecent(big, `question number ${i}`);
  assert.equal(big.length, 8); assert.equal(big[0], 'question number 11');
});

// ---- provenance
t('provenance: friendly labels, deduped, capped', () => {
  assert.deepEqual(provenance([{ type: 'calendar', title: 'x' }, { type: 'calendar', title: 'y' }, { type: 'lunch', title: 'z' }, { type: 'google_doc', title: 'Handbook' }]), ['the school calendar', 'the cafe menu']);
  assert.deepEqual(provenance([{ type: 'google_doc', title: 'Student & Family Handbook' }]), ['Student & Family Handbook']);
  assert.deepEqual(provenance(undefined), []);
});

// ---- teams
t('team: US game', () => assert.deepEqual(parseTeam('US Girls Basketball - Game CSD Fremont - Home'), { key: 'us girls basketball', label: 'US Girls Basketball', opponent: 'CSD Fremont' }));
t('team: dash inside parentheses and double space', () => {
  const x = parseTeam("MS (7th/8th - VA) Girls' Soccer - Game Menlo School - Away");
  assert.equal(x.label, "MS (7th/8th - VA) Girls' Soccer"); assert.equal(x.opponent, 'Menlo School');
  assert.equal(parseTeam("MS (5th/6th - JVA) Girls' Soccer  - Game Sacred Heart Prep - Home").label, "MS (5th/6th - JVA) Girls' Soccer");
});
t('team: special events keep the team', () => {
  assert.equal(parseTeam('US Boys Basketball JV - Friendship Game').label, 'US Boys Basketball JV');
  assert.equal(parseTeam('US Boys Basketball Varsity - CCS Sportsmanship Game').label, 'US Boys Basketball Varsity');
  assert.equal(parseTeam('US Boys Basketball Varsity - First Day Of Scrimmages').opponent, '');
});
t('team: not a team shape', () => { assert.equal(parseTeam('Veterans Day'), null); assert.equal(parseTeam('A - B'), null); });
t('team: Varsity and JV are different follows', () => assert.notEqual(parseTeam('US Boys Basketball Varsity - Game X - Home').key, parseTeam('US Boys Basketball JV - Game X - Home').key));
t('hour constant sane', () => assert.equal(HOUR * 24, DAY));

console.log(`${passed} passed${process.exitCode ? ', with failures' : ''}`);
