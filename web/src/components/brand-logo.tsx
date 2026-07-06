"use client";

/** Logotype Thales du header — masqué sur l'accueil, où le logo trône déjà
 * centré en haut de page (perchoir de l'intro animée). */

import { usePathname } from "next/navigation";

export function BrandLogo() {
  const pathname = usePathname();
  if (pathname === "/") return null;
  return (
    <>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src="/thales.svg" alt="Thales" className="h-3.5 w-auto" />
      <span className="text-fg-faint">│</span>
    </>
  );
}
