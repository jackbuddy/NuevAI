import './globals.css';
import type { Metadata, Viewport } from 'next';
import { Inter, Fraunces } from 'next/font/google';

const inter = Inter({ subsets: ['latin'], variable: '--font-inter', display: 'swap' });
const fraunces = Fraunces({ subsets: ['latin'], variable: '--font-fraunces', display: 'swap', axes: ['opsz'] });

export const metadata: Metadata = {
  title: 'NuevAI · Ask anything about Nueva',
  description: 'Courses, calendar, sports schedules and campus resources for The Nueva School community.',
};
export const viewport: Viewport = { themeColor: [{ media: '(prefers-color-scheme: light)', color: '#f6f9fc' }, { media: '(prefers-color-scheme: dark)', color: '#07111f' }], width: 'device-width', initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${fraunces.variable}`}>
      <body suppressHydrationWarning className="min-h-dvh antialiased">{children}</body>
    </html>
  );
}
