'use client';

/**
 * Instrumentation.
 *  - anon_id: random UUID kept in localStorage. No account, no personal data. Identifies a browser.
 *  - session_id: rotates after 30 minutes of inactivity.
 *  - track(event, props): queues a record that is always stamped with both ids, then batches
 *    it to /api/track (which forwards to the server, which stores it in Supabase).
 *  - envelope flags (top level of every batch and chat request, never inside props): `i` = this browser was marked as the
 *    owner/dev's ("Mark this browser as me" on /insights, or /?internal=1), `wd` = webdriver/headless, `dev` = localhost build.
 *    The server turns them into the event's audience (user | internal | test | bot).
 *  - visit counter: nuevai.visits is incremented whenever a new session starts.
 * Everything here fails silent: analytics must never break the app.
 */

const UID_KEY = 'nuevai.uid';
const SID_KEY = 'nuevai.sid';
const LAST_KEY = 'nuevai.last';
const FIRST_KEY = 'nuevai.first';
const VISITS_KEY = 'nuevai.visits';
const INTERNAL_KEY = 'nuevai.internal';
const SESSION_GAP_MS = 30 * 60 * 1000;
const FLUSH_MS = 3000;

type Props = Record<string, string | number | boolean | null | undefined>;
interface Queued { event: string; props: Props; ts: string; eid: string }

let queue: Queued[] = [];
let timer: ReturnType<typeof setTimeout> | null = null;
let ids: { anonId: string; sessionId: string } | null = null;
let started = false;

function uuid(): string {
  try {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
  } catch {}
  return 'x' + Math.random().toString(36).slice(2) + Date.now().toString(36);
}

function store(kind: 'local' | 'session') {
  try { return kind === 'local' ? window.localStorage : window.sessionStorage; } catch { return null; }
}

/** Returns the stable ids, creating them (and rotating the session) as needed. */
export function getIds(): { anonId: string; sessionId: string; newSession: boolean; returning: boolean } {
  const ls = store('local');
  let anonId = ls?.getItem(UID_KEY) ?? '';
  let returning = true;
  if (!anonId) {
    anonId = uuid();
    returning = false;
    try { ls?.setItem(UID_KEY, anonId); ls?.setItem(FIRST_KEY, new Date().toISOString()); } catch {}
  }
  const now = Date.now();
  const last = Number(ls?.getItem(LAST_KEY) ?? 0);
  let sessionId = ls?.getItem(SID_KEY) ?? '';
  let newSession = false;
  if (!sessionId || now - last > SESSION_GAP_MS) {
    sessionId = uuid();
    newSession = true;
    try {
      ls?.setItem(SID_KEY, sessionId);
      ls?.setItem(VISITS_KEY, String((Number(ls?.getItem(VISITS_KEY)) || 0) + 1));
      if (!ls?.getItem(FIRST_KEY)) ls?.setItem(FIRST_KEY, new Date().toISOString());
    } catch {}
  }
  try { ls?.setItem(LAST_KEY, String(now)); } catch {}
  ids = { anonId, sessionId };
  return { anonId, sessionId, newSession, returning };
}

/** Visit info for this browser: how many sessions it has had and since when. Safe on the server (returns nulls). */
export function getVisitInfo(): { visit_number: number | null; first_visit: boolean | null; first_seen: string | null; days_since_first: number | null } {
  const ls = store('local');
  if (!ls) return { visit_number: null, first_visit: null, first_seen: null, days_since_first: null };
  const n = Number(ls.getItem(VISITS_KEY)) || 1;
  const first = ls.getItem(FIRST_KEY);
  const t = first ? Date.parse(first) : NaN;
  return {
    visit_number: n,
    first_visit: n <= 1,
    first_seen: first,
    days_since_first: Number.isFinite(t) ? Math.max(0, Math.floor((Date.now() - t) / 86_400_000)) : null,
  };
}

/** Is this browser marked as the owner's / a developer's? Stored only in this browser. */
export function isInternal(): boolean {
  try { return store('local')?.getItem(INTERNAL_KEY) === '1'; } catch { return false; }
}
export function setInternal(on: boolean) {
  try { if (on) store('local')?.setItem(INTERNAL_KEY, '1'); else store('local')?.removeItem(INTERNAL_KEY); } catch {}
}

/** Envelope flags understood by the server. */
export function envelopeFlags(): string[] {
  const f: string[] = [];
  try {
    if (isInternal()) f.push('i');
    const ua = navigator.userAgent || '';
    if (navigator.webdriver || /HeadlessChrome|PhantomJS/i.test(ua)) f.push('wd');
    const h = location.hostname;
    if (h === 'localhost' || h === '127.0.0.1' || h === '[::1]' || h.endsWith('.localhost') || h.endsWith('.test') || h.endsWith('.local')) f.push('dev');
  } catch {}
  return f;
}

/** Ids to attach to any request that should be tied to this user (e.g. chat). */
export function currentIds(): { anon_id: string; session_id: string; flags: string[] } {
  const i = getIds();
  return { anon_id: i.anonId, session_id: i.sessionId, flags: envelopeFlags() };
}

function send(batch: Queued[], beacon = false) {
  if (!batch.length) return;
  const { anonId, sessionId } = ids ?? getIds();
  const body = JSON.stringify({ anon_id: anonId, session_id: sessionId, flags: envelopeFlags(), path: location.pathname + location.search.slice(0, 40), events: batch });
  try {
    if (beacon && navigator.sendBeacon) {
      navigator.sendBeacon('/api/track', new Blob([body], { type: 'application/json' }));
    } else {
      fetch('/api/track', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body, keepalive: true }).catch(() => {});
    }
  } catch {}
}

export function flush(beacon = false) {
  if (timer) { clearTimeout(timer); timer = null; }
  const batch = queue.splice(0, 50);
  send(batch, beacon);
  if (queue.length) flush(beacon);
}

export function track(event: string, props: Props = {}) {
  if (typeof window === 'undefined') return;
  try {
    getIds();
    queue.push({ event, props, ts: new Date().toISOString(), eid: uuid().replace(/-/g, '').slice(0, 20) });
    if (queue.length >= 20) flush();
    else if (!timer) timer = setTimeout(() => flush(), FLUSH_MS);
  } catch {}
}

/** Call once on app start: session/page events, vitals, error capture, outbound-click capture. */
export function startAnalytics() {
  if (typeof window === 'undefined' || started) return;
  started = true;
  try {
    // /?internal=1 marks this browser as the owner's (events from it are labelled "internal" and left out of the real-user numbers); /?internal=0 undoes it
    try {
      const q = new URLSearchParams(location.search);
      if (q.has('internal')) {
        setInternal(q.get('internal') !== '0');
        q.delete('internal');
        history.replaceState(null, '', location.pathname + (q.toString() ? '?' + q : '') + location.hash);
      }
    } catch {}
    const { newSession, returning } = getIds();
    if (newSession) {
      const w = window.innerWidth;
      track('session_start', {
        returning,
        visit_number: getVisitInfo().visit_number,
        first_visit: getVisitInfo().first_visit,
        days_since_first: getVisitInfo().days_since_first,
        device: w < 640 ? 'mobile' : w < 1024 ? 'tablet' : 'desktop',
        viewport: `${w}x${window.innerHeight}`,
        referrer: document.referrer ? new URL(document.referrer).host : 'direct',
        lang: navigator.language,
        dark: window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false,
      });
    }
    track('page_view', { title: document.title });

    // Core web vitals-lite
    const vitals: Record<string, number> = {};
    try {
      new PerformanceObserver((l) => { const e = l.getEntries(); vitals.lcp = Math.round(e[e.length - 1].startTime); }).observe({ type: 'largest-contentful-paint', buffered: true });
      let cls = 0;
      new PerformanceObserver((l) => { for (const e of l.getEntries() as any[]) if (!e.hadRecentInput) cls += e.value; vitals.cls = Math.round(cls * 1000) / 1000; }).observe({ type: 'layout-shift', buffered: true });
    } catch {}
    window.addEventListener('load', () => setTimeout(() => {
      const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined;
      if (nav) track('perf_load', { dom_ms: Math.round(nav.domContentLoadedEventEnd), load_ms: Math.round(nav.loadEventEnd), ttfb_ms: Math.round(nav.responseStart) });
    }, 0));

    window.addEventListener('pagehide', () => {
      if (Object.keys(vitals).length) track('web_vitals', vitals);
      flush(true);
    });
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') flush(true); });

    window.addEventListener('error', (e) => track('client_error', { message: String(e.message).slice(0, 160), src: String(e.filename || '').slice(-60) }));
    window.addEventListener('unhandledrejection', (e) => track('client_error', { message: String((e.reason && e.reason.message) || e.reason).slice(0, 160), kind: 'promise' }));

    document.addEventListener('click', (ev) => {
      const a = (ev.target as HTMLElement | null)?.closest?.('a[href^="http"]') as HTMLAnchorElement | null;
      if (!a || a.dataset.tracked) return;
      try { track('outbound_click', { host: new URL(a.href).host, label: (a.textContent || '').trim().slice(0, 60) }); } catch {}
    }, true);
  } catch {}
}
