import './globals.css';
import type { Metadata, Viewport } from 'next';
import { Manrope } from 'next/font/google';

const manrope = Manrope({ subsets: ['latin'], variable: '--font-manrope', display: 'swap' });

export const metadata: Metadata = {
  metadataBase: new URL('https://nuev-ai.vercel.app'),
  title: 'Nueva AI',
  description: 'Courses, calendar, sports schedules and campus resources for The Nueva School community.',
  openGraph: {
    type: 'website',
    siteName: 'Nueva AI',
    url: '/',
    title: 'Nueva AI: ask anything about Nueva',
    description: 'Courses, calendar, sports schedules, lunch and campus resources for The Nueva School community.',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Nueva AI: ask anything about Nueva',
    description: 'Courses, calendar, sports schedules, lunch and campus resources for The Nueva School community.',
  },
  applicationName: 'Nueva AI',
  appleWebApp: { capable: true, title: 'Nueva AI', statusBarStyle: 'default' },
};
export const viewport: Viewport = { themeColor: '#ffffff', width: 'device-width', initialScale: 1, viewportFit: 'cover', interactiveWidget: 'resizes-content' };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={manrope.variable}>
      <body suppressHydrationWarning className="min-h-dvh antialiased">{children}</body>
    </html>
  );
}
