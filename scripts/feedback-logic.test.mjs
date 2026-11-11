// Unit tests for lib/feedback-logic.ts: `node scripts/feedback-logic.test.mjs` (Node 22.6+ strips the types).
import assert from 'node:assert/strict';
import { clip, jsonBytes, fitProps, fallbackWhy, ratingProps, detailProps, checkProps, fallbackProps, REASONS, FIT_BYTES } from '../lib/feedback-logic.ts';

let passed = 0;
const t = (name, fn) => { try { fn(); passed++; } catch (e) { console.error('FAIL', name, '\n ', e.message); process.exitCode = 1; } };
const src = [{ type: 'doc' }];
const A = (content, o = {}) => ({ id: 'm1', content, query: 'q', cached: false, sources: src, ...o });

t('reasons are the five asked for', () => assert.deepEqual(REASONS.map((r) => r.id), ['wrong', 'outdated', 'not_answered', 'too_long', 'other']));

// ---- clip: safe for Postgres jsonb
t('clip strips NUL', () => assert.equal(clip('a\u0000b', 10), 'ab'));
t('clip never ends on half a surrogate pair', () => { const s = clip('ab😀cd', 3); assert.equal(s, 'ab'); JSON.parse(JSON.stringify(s)); });
t('clip trims and truncates', () => assert.equal(clip('  hello world  ', 5), 'hello'));
t('clip handles null', () => assert.equal(clip(undefined, 5), ''));

// ---- size accounting matches Python ensure_ascii
t('jsonBytes counts non-ASCII as 6', () => assert.equal(jsonBytes({ a: 'é' }), '{"a":"x"}'.length - 1 + 6));
t('fitProps keeps small events untouched', () => { const p = { mid: 'a', answer: 'short' }; assert.deepEqual(fitProps(p), p); });
t('fitProps shortens a huge answer, keeps ids and reason', () => {
  const p = fitProps({ mid: 'abc', value: 'down', reason: 'wrong', query: 'q'.repeat(200), answer: 'é'.repeat(600), comment: 'c'.repeat(300) });
  assert.ok(jsonBytes(p) <= FIT_BYTES, String(jsonBytes(p)));
  assert.equal(p.mid, 'abc'); assert.equal(p.reason, 'wrong'); assert.ok(p.trunc);
});
t('fitProps shortens the answer before the comment', () => {
  const p = fitProps({ mid: 'abc', answer: '€'.repeat(600), comment: 'x'.repeat(300), query: 'y'.repeat(200) });
  assert.ok(jsonBytes(p) <= FIT_BYTES);
  assert.ok(p.comment && p.comment.length >= 150);
});
t('worst-case rating payload fits', () => {
  const p = ratingProps(A('日本語'.repeat(300), { query: '漢'.repeat(300) }), 'down');
  assert.ok(jsonBytes(p) <= FIT_BYTES); assert.equal(p.value, 'down'); assert.equal(p.mid, 'm1');
});
t('typical rating payload keeps the full answer', () => {
  const ans = 'Lunch today at San Mateo is chicken teriyaki with rice and steamed vegetables. '.repeat(7).trim();
  const p = ratingProps(A(ans), 'up');
  assert.equal(p.answer, ans.slice(0, 600).trimEnd()); assert.ok(!p.trunc);
});
t('detail payload is small and joins by mid', () => {
  const p = detailProps(A('x'), { value: 'down', src: 'thumb', reason: 'outdated', comment: 'z'.repeat(500) });
  assert.equal(p.mid, 'm1'); assert.equal(p.comment.length, 300); assert.ok(jsonBytes(p) < 700);
});
t('check and fallback payloads', () => {
  assert.equal(checkProps(A('I don’t have that.', { sources: [] }), 'no').value, 'no');
  assert.equal(fallbackProps(A('x'), 'hedge').why, 'hedge');
});

// ---- fallback detection
t('good answer: not a fallback', () => assert.equal(fallbackWhy(A('Lunch today is chicken teriyaki, rice and a salad bar. Dessert is fruit.')), null));
t('good long answer with sources', () => assert.equal(fallbackWhy(A('The varsity boys basketball team plays at home on Friday at 7pm against Menlo, then away at Crystal Springs on Saturday. '.repeat(10))), null));
t('degraded', () => assert.equal(fallbackWhy(A('Here is what I found…', { degraded: true })), 'degraded'));
t('no sources and not cached', () => assert.equal(fallbackWhy(A('Maybe try the office.', { sources: [] })), 'no_sources'));
t('canned reply (cached, no sources) is NOT a fallback', () => assert.equal(fallbackWhy(A('Hi! I can help with lunch, games and more.', { cached: true, sources: [] })), null));
t('hedge: I don’t have', () => assert.equal(fallbackWhy(A('I don’t have that. Please contact the main office at 650-555-0100.')), 'hedge'));
t('hedge: straight apostrophe', () => assert.equal(fallbackWhy(A("I don't have the spring schedule yet.")), 'hedge'));
t('hedge: couldn’t find', () => assert.equal(fallbackWhy(A('I couldn’t find a bus schedule in the school’s pages.')), 'hedge'));
t('hedge: not available', () => assert.equal(fallbackWhy(A('The Friday lunch menu is not available yet.')), 'hedge'));
t('hedge: not on the school calendar', () => assert.equal(fallbackWhy(A('That date is not on the school calendar.')), 'hedge'));
t('hedge deep in a long answer is ignored', () => assert.equal(fallbackWhy(A('Here are the details. '.repeat(60) + 'The map is not in this list.')), null));
t('errors, streaming, stopped and empty never qualify', () => {
  assert.equal(fallbackWhy(A('I don’t have that', { error: true })), null);
  assert.equal(fallbackWhy(A('I don’t have that', { streaming: true })), null);
  assert.equal(fallbackWhy(A('I don’t have that', { stopped: true })), null);
  assert.equal(fallbackWhy(A('')), null);
});

console.log(`feedback-logic: ${passed} passed`);
