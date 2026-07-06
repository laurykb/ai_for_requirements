import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";

import { HeaderNav } from "@/components/header-nav";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

const jetbrains = JetBrains_Mono({
  variable: "--font-jetbrains",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "AI for SSH",
  description:
    "RAG documentaire et vérification d'exigences, 100 % local : " +
    "vos documents ne quittent pas cette machine.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="fr" className={`${inter.variable} ${jetbrains.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col">
        <header className="sticky top-0 z-40 border-b border-edge bg-background/80 backdrop-blur">
          <div className="mx-auto flex h-14 w-full max-w-6xl items-center justify-between px-6">
            <Link href="/" className="group flex items-center gap-3">
              {/* Logotype Thales (version négatif pour fond sombre). */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src="/thales.svg" alt="Thales" className="h-3.5 w-auto" />
              <span className="text-fg-faint">│</span>
              <span className="text-sm font-semibold tracking-wide">AI for SSH</span>
              <span className="hidden text-xs text-fg-faint transition-colors group-hover:text-fg-muted sm:inline">
                RAG documentaire · vérification d&apos;exigences
              </span>
            </Link>
            <HeaderNav />
          </div>
        </header>
        <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-8">{children}</main>
        <footer className="border-t border-edge py-4">
          <p className="mx-auto max-w-6xl px-6 text-xs text-fg-faint">
            100 % local — vos documents ne quittent pas cette machine.
          </p>
        </footer>
      </body>
    </html>
  );
}
