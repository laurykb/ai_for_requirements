"use client";

/** Navigation contextuelle : l'Outil RAG et AI for Requirements (LynX) sont
 * deux applications SÉPARÉES, choisies sur l'accueil (comme dans le
 * Streamlit). Une fois dans un outil, on ne voit que la navigation de cet
 * outil — et « ← Accueil » pour changer d'outil.
 * Prompts métier et Suivi technique : réservés au mode Expert (monde RAG). */

import Link from "next/link";
import { usePathname } from "next/navigation";

import { ExpertToggle, useExpert } from "@/components/expert-toggle";

// Monde RAG : le chat et ses vues d'opérateur.
const RAG_LINKS = [
  { href: "/rag", label: "Chat" },
  { href: "/documents", label: "Documents" },
];
const RAG_TAIL = [
  { href: "/informations", label: "Aide" },
  { href: "/settings", label: "Réglages" },
];
const RAG_EXPERT = [
  { href: "/agents", label: "Prompts métier" },
  { href: "/observability", label: "Suivi technique" },
];
const RAG_PREFIXES = [
  "/rag", "/documents", "/agents", "/informations", "/observability", "/settings",
];

export function HeaderNav() {
  const pathname = usePathname();
  const expert = useExpert();
  if (pathname === "/") return null; // accueil : rien d'autre que les deux cartes

  const inLynx = pathname.startsWith("/requirements");
  const inRag = RAG_PREFIXES.some((p) => pathname.startsWith(p));
  const links = inRag
    ? [...RAG_LINKS, ...RAG_TAIL, ...(expert ? RAG_EXPERT : [])]
    : [];

  return (
    <nav className="flex items-center gap-5 text-sm text-fg-muted">
      <Link href="/" className="text-fg-faint transition-colors hover:text-foreground">
        ← Accueil
      </Link>
      {inLynx && <span className="text-foreground">AI for Requirements</span>}
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
      {inRag && <ExpertToggle />}
    </nav>
  );
}
