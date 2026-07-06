"use client";

/** Navigation du haut : les deux outils.
 * Masquée sur l'accueil (`/`) — on entre par les cartes, sans détour. */

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/rag", label: "Outil RAG" },
  { href: "/requirements", label: "AI for Requirements" },
];

export function HeaderNav() {
  const pathname = usePathname();
  if (pathname === "/") return null; // accueil : rien d'autre que les deux cartes

  return (
    <nav className="flex items-center gap-5 text-sm text-fg-muted">
      {LINKS.map((l) => (
        <Link
          key={l.href}
          href={l.href}
          aria-current={pathname.startsWith(l.href) ? "page" : undefined}
          className={`transition-colors hover:text-foreground ${
            pathname.startsWith(l.href) ? "text-accent-bright" : ""
          }`}
        >
          {l.label}
        </Link>
      ))}
    </nav>
  );
}
