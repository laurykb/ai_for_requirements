"use client";

/** Navigation contextuelle : l'Outil RAG et AI for Requirements (LynX) sont
 * deux applications SÉPARÉES, choisies sur l'accueil (comme dans le
 * Streamlit). Une fois dans un outil, on ne voit que la navigation de cet
 * outil — et « ← Accueil » pour changer d'outil.
 * Observabilité : réservée au mode Expert (monde RAG). */

import Link from "next/link";
import { usePathname } from "next/navigation";

import { ExpertToggle, useExpert } from "@/components/expert-toggle";

// Monde RAG : le chat et ses vues d'opérateur (mêmes onglets que le Streamlit).
const RAG_LINKS = [
  { href: "/rag", label: "Chat" },
  { href: "/documents", label: "Documents" },
];
const RAG_EXPERT = [{ href: "/observability", label: "Observabilité" }];
const RAG_TAIL = [{ href: "/settings", label: "Paramètres" }];
const RAG_PREFIXES = ["/rag", "/documents", "/observability", "/settings"];

export function HeaderNav() {
  const pathname = usePathname();
  const expert = useExpert();
  if (pathname === "/") return null; // accueil : rien d'autre que les deux cartes

  const inLynx = pathname.startsWith("/requirements");
  const inRag = RAG_PREFIXES.some((p) => pathname.startsWith(p));
  const links = inRag
    ? [...RAG_LINKS, ...(expert ? RAG_EXPERT : []), ...RAG_TAIL]
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
