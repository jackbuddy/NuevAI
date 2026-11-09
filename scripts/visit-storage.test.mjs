// lib/visit.ts must keep working (in memory) when localStorage throws on every access, as in some private modes and embedded browsers.
// `node scripts/visit-storage.test.mjs` (Node 22.6+ strips types; a tiny resolve hook lets extensionless relative imports find .ts files).
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { existsSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';

registerHooks({
  resolve(spec, ctx, next) {
    if (spec.startsWith('.') && !/\.[a-z]+$/.test(spec) && ctx.parentURL) {
      const p = fileURLToPath(new URL(spec + '.ts', ctx.parentURL));
      if (existsSync(p)) return next(pathToFileURL(p).href, ctx);
    }
    return next(spec, ctx);
  },
});

const blocked = () => { throw new DOMException('blocked', 'SecurityError'); };
const win = new EventTarget();
for (const k of ['localStorage', 'sessionStorage']) Object.defineProperty(win, k, { get: blocked });
globalThis.window = win;
Object.defineProperty(globalThis, 'localStorage', { get: blocked, configurable: true });
Object.defineProperty(globalThis, 'sessionStorage', { get: blocked, configurable: true });
globalThis.location = { pathname: '/', search: '' };
globalThis.fetch = async () => ({});
win.setTimeout = setTimeout;

const v = await import('../lib/visit.ts');
let passed = 0;
const t = (name, fn) => { try { fn(); passed++; } catch (e) { console.error('FAIL', name, '\n ', e.message); process.exitCode = 1; } };

t('readVisit: a blocked browser looks like a first visit', () => { const r = v.readVisit(); assert.equal(r.visit, 1); assert.equal(r.firstRun, true); });
t('state survives in memory', () => {
  v.noteAnswer('lunch');
  assert.equal(v.ux().answers, 1);
  assert.equal(v.readVisit().firstRun, false);
});
t('hints are recorded in memory (cap still works within the page)', () => { v.hintShown('ideas'); assert.equal(v.ux().hints.total, 1); assert.ok(v.ux().hints.shown.ideas); });
t('recents', () => { v.addRecent('What’s for lunch today?'); assert.deepEqual(v.recents(), ['What’s for lunch today?']); });
t('follows toggle in memory', () => {
  assert.equal(v.toggleFollow({ key: 'us girls basketball', label: 'US Girls Basketball' }), true);
  assert.equal(v.getFollows().length, 1);
  assert.equal(v.toggleFollow({ key: 'us girls basketball', label: 'US Girls Basketball' }), false);
  assert.equal(v.getFollows().length, 0);
});
t('endOnboarding is harmless', () => { v.endOnboarding('test'); assert.equal(v.ux().onboarded, true); });

console.log(`${passed} passed${process.exitCode ? ', with failures' : ''}`);
process.exit(process.exitCode ?? 0);
