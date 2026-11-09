'use client';

import { useEffect, useRef, useState } from 'react';
import { Share, X } from 'lucide-react';
import { Logo } from './Logo';
import { track } from '@/lib/analytics';
import { installPlatform, isStandalone, onInstallReady, promptInstall } from '@/lib/install';
import { readVisit, ux, updateUx } from '@/lib/visit';
import { canShowInstall } from '@/lib/ux-logic';

/**
 * Gentle add-to-home-screen card. Appears only on the idle home screen, a few seconds after load, after the visitor has
 * had real value (second visit or three answers), never mid-task, with long cooldowns (rules: lib/ux-logic.ts).
 */
export function InstallPrompt({ idle }: { idle: boolean }) {
  const [platform, setPlatform] = useState<'android' | 'ios' | null>(null);
  const [tick, setTick] = useState(0);
  const shownRef = useRef(false);

  useEffect(() => onInstallReady(() => setTick((t) => t + 1)), []);

  useEffect(() => {
    if (shownRef.current) return;
    if (!idle) { setPlatform(null); return; }
    const t = setTimeout(() => {
      try {
        const u = ux();
        const p = installPlatform();
        const v = readVisit().visit;
        const ok = canShowInstall({ platform: p, standalone: isStandalone(), visit: v, answers: u.answers, dismissed: u.install.dismissed, lastShown: u.install.lastShown, installed: u.install.installed, idle: true, now: Date.now() });
        if (!ok || !p) return;
        shownRef.current = true;
        updateUx((x) => { x.install.lastShown = Date.now(); });
        track('install_prompt_shown', { platform: p });
        setPlatform(p);
      } catch {}
    }, 3500);
    return () => clearTimeout(t);
  }, [idle, tick]);

  if (!platform || !idle) return null;

  const dismiss = () => { updateUx((u) => { u.install.dismissed = [...u.install.dismissed, Date.now()]; }); track('install_dismiss', { platform }); setPlatform(null); };
  const accept = async () => {
    const r = await promptInstall();
    if (r === 'accepted') { updateUx((u) => { u.install.installed = true; }); track('install_accept', { platform }); setPlatform(null); }
    else { updateUx((u) => { u.install.dismissed = [...u.install.dismissed, Date.now()]; }); track('install_dismiss', { platform, via: 'native' }); setPlatform(null); }
  };

  return (
    <aside aria-label="Add to home screen" className="surface shadow-lift animate-rise mt-8 flex items-start gap-3 rounded-2xl bg-white p-3.5">
      <Logo size={32} />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-brand-900">Keep NuevAI on your home screen</p>
        {platform === 'ios' ? (
          <p className="ink-2 mt-0.5 text-[13px] leading-snug">Tap <Share className="mx-0.5 inline h-3.5 w-3.5 -translate-y-px" aria-label="Share" /> in Safari, then <b className="font-semibold text-brand-800">Add to Home Screen</b>.</p>
        ) : (
          <>
            <p className="ink-2 mt-0.5 text-[13px] leading-snug">One tap to today’s lunch, games and answers.</p>
            <button type="button" onClick={accept} className="tap-box mt-2 rounded-full bg-brand-900 px-4 py-1.5 text-[13px] font-semibold text-white hover:bg-brand-700">Add to home screen</button>
          </>
        )}
      </div>
      <button type="button" onClick={dismiss} aria-label="Dismiss" className="tap-box -mr-1.5 -mt-1.5 h-11 w-11 shrink-0 rounded-full hover:bg-[var(--surface-2)]"><X className="h-4 w-4" aria-hidden /></button>
    </aside>
  );
}
