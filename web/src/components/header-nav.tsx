"use client";

/** Navigation du haut : les deux outils + vues d'opérateur.
 * Masquée sur l'accueil (`/`) — on entre par les cartes, sans détour.
 * Observabilité : réservée au mode Expert pour garder l'interface simple. */

import Link from "next/link";
import { usePathname } from "next/navigation";

import { ExpertToggle, useExpert } from "@/components/expert-toggle";

const LINKS = [
  { href: "/rag", label: "Outil RAG" },
  { href: "/documents", label: "Documents" },
  { href: "/requirements", label: "AI for Requirements" },
];
const EXPERT_LINKS = [{ href: "/observability", label: "Observabilité" }];
const TAIL_LINKS = [{ href: "/settings", label: "Paramètres" }];

export function HeaderNav() {
  const pathname = usePathname();
  const expert = useExpert();
  if (pathname === "/") return null; // accueil : rien d'autre que les deux cartes

  const links = [...LINKS, ...(expert ? EXPERT_LINKS : []), ...TAIL_LINKS];
  return (
    <nav className="flex items-center gap-5 text-sm text-fg-muted">
      {links.map((l) => (
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
      <ExpertToggle />
    </nav>
  );
}
