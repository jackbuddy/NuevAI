import './globals.css';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'NuevAI Assistant',
  description: 'AI Assistant for The Nueva School',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body suppressHydrationWarning className="bg-[#F8FAFC] text-[#0F172A] antialiased">
        {children}
      </body>
    </html>
  );
}
