import Link from 'next/link';

export default function NotFound() {
  return (
    <div className="grid min-h-dvh place-items-center px-6">
      <div className="max-w-sm text-center">
        <h1 className="text-2xl font-bold tracking-tight text-brand-900">Page not found</h1>
        <p className="ink-2 mt-2 text-sm">That page doesn’t exist. The assistant, calendar and resources are on the home page.</p>
        <Link href="/" className="tap-box mt-5 rounded-full bg-brand-900 px-5 py-2.5 text-sm font-semibold text-white hover:bg-brand-700">Back to NuevAI</Link>
      </div>
    </div>
  );
}
