'use client';

import { useEffect } from 'react';
import { RotateCw } from 'lucide-react';

// Last-resort screen if something in the page throws while rendering (this Next.js version passes `retry`, not `reset`).
export default function Error({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  useEffect(() => { console.error(error); }, [error]);
  return (
    <div className="grid min-h-dvh place-items-center px-6">
      <div role="alert" className="max-w-sm text-center">
        <h1 className="text-2xl font-bold tracking-tight text-brand-900">Something went wrong</h1>
        <p className="ink-2 mt-2 text-sm">The page hit an unexpected problem. Your chat is saved in this browser tab.</p>
        <button onClick={() => retry()} className="tap-box mt-5 gap-1.5 rounded-full bg-brand-900 px-5 py-2.5 text-sm font-semibold text-white hover:bg-brand-700">
          <RotateCw className="h-4 w-4" aria-hidden />Try again
        </button>
      </div>
    </div>
  );
}
