import type { Metadata } from "next";
import { Suspense } from "react";
import { Inter, JetBrains_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";

import { BrandLogo } from "@/components/brand-logo";
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
            <Link href="/" aria-label="Ouvrir l’accueil AI for SSH"
              className="group flex items-center gap-3">
              <BrandLogo />
              <span className="text-sm font-semibold tracking-wide">AI for SSH</span>
            </Link>
            {/* Suspense : la nav lit les query params (onglets LynX). */}
            <Suspense fallback={null}>
              <HeaderNav />
            </Suspense>
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
