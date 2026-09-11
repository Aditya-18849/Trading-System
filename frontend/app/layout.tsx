import type { Metadata } from 'next';
import { Inter, JetBrains_Mono } from 'next/font/google';
import './globals.css';

const inter = Inter({ subsets: ['latin'], variable: '--font-inter' });
const jetbrainsMono = JetBrains_Mono({ subsets: ['latin'], variable: '--font-jetbrains-mono' });

import { AppShell } from '@/components/AppShell';

export const metadata: Metadata = {
  title: 'Trading Dashboard | SEBI-Compliant Algo Trading System',
  description: 'Institutional-grade algorithmic trading dashboard and execution engine',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body
        className={`${inter.variable} ${jetbrainsMono.variable} font-sans antialiased bg-slate-950 text-slate-100`}
      >
        <AppShell>
          {children}
        </AppShell>
      </body>
    </html>
  );
}