import './globals.css';
import type { Metadata, Viewport } from 'next';
import { Manrope } from 'next/font/google';

const manrope = Manrope({ subsets: ['latin'], variable: '--font-manrope', display: 'swap' });

export const metadata: Metadata = {
  title: 'NuevAI',
  description: 'Courses, calendar, sports schedules and campus resources for The Nueva School community.',
  applicationName: 'NuevAI',
  appleWebApp: { capable: true, title: 'NuevAI', statusBarStyle: 'default' },
};
export const viewport: Viewport = { themeColor: '#ffffff', width: 'device-width', initialScale: 1, viewportFit: 'cover', interactiveWidget: 'resizes-content' };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={manrope.variable}>
      <body suppressHydrationWarning className="min-h-dvh antialiased">{children}</body>
    </html>
  );
}
