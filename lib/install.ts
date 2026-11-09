'use client';

/**
 * Add-to-home-screen support. Chromium fires `beforeinstallprompt` once and early, so it is captured at module load
 * (Shell imports this) and held until the visitor has had enough value for the prompt to be offered (see ux-logic canShowInstall).
 * There is deliberately no service worker: nothing here caches pages or data, so it cannot serve stale answers or break a deploy.
 */
interface InstallEvent extends Event { prompt: () => Promise<void>; userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }> }

let deferred: InstallEvent | null = null;
const listeners = new Set<() => void>();
export const onInstallReady = (fn: () => void) => { listeners.add(fn); return () => { listeners.delete(fn); }; };

if (typeof window !== 'undefined') {
  window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); deferred = e as InstallEvent; listeners.forEach((f) => f()); });
  window.addEventListener('appinstalled', () => { deferred = null; listeners.forEach((f) => f()); });
}

export function isStandalone(): boolean {
  try { return matchMedia('(display-mode: standalone)').matches || (navigator as any).standalone === true; } catch { return false; }
}

export function installPlatform(): 'android' | 'ios' | null {
  if (typeof navigator === 'undefined') return null;
  if (deferred) {
    let phone = false;
    try { phone = matchMedia('(pointer: coarse)').matches || matchMedia('(max-width: 767px)').matches; } catch {}
    return phone ? 'android' : null;   // desktop Chrome can install too, but this is a phone-first product: do not nag there
  }
  const ua = navigator.userAgent;
  const iPadOS = /Macintosh/.test(ua) && (navigator.maxTouchPoints ?? 0) > 1;
  if (/iPhone|iPad|iPod/.test(ua) || iPadOS) return 'ios';
  return null;
}

export async function promptInstall(): Promise<'accepted' | 'dismissed' | 'unavailable'> {
  if (!deferred) return 'unavailable';
  const d = deferred;
  deferred = null;
  try { await d.prompt(); return (await d.userChoice).outcome; } catch { return 'unavailable'; }
}
