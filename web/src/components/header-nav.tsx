"use client";

/** Navigation contextuelle : l'Outil RAG et AI for Requirements (LynX) sont
 * deux applications SÉPARÉES, choisies sur l'accueil (comme dans le
 * Streamlit). Une fois dans un outil, on ne voit que la navigation de cet
 * outil — et « ← Accueil » pour changer d'outil.
 * Prompts métier et Suivi technique : réservés au mode Expert (monde RAG). */

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";

import { ExpertToggle, useExpert } from "@/components/expert-toggle";

// Monde RAG : le chat et ses vues d'opérateur.
const RAG_LINKS = [
  { href: "/rag", label: "Chat" },
  { href: "/documents", label: "Documents" },
];
// L'Aide sort de la nav principale (essentiel seulement) : icône « ? » à droite.
const RAG_TAIL = [
  { href: "/settings", label: "Réglages" },
];
const RAG_EXPERT = [
  { href: "/agents", label: "Prompts métier" },
  { href: "/observability", label: "Suivi technique" },
];
const RAG_PREFIXES = [
  "/rag", "/documents", "/agents", "/informations", "/observability", "/settings",
];

// Monde LynX : les onglets de l'outil, en barre de menu (liens ?tab=).
const LYNX_TABS = [
  { tab: "matrice", label: "Matrice" },
  { tab: "exigences", label: "Exigences" },
  { tab: "chat", label: "Chat" },
  { tab: "parametres", label: "Paramètres" },
];

export function HeaderNav() {
  const pathname = usePathname();
  const params = useSearchParams();
  const expert = useExpert();
  if (pathname === "/") return null; // accueil : rien d'autre que les deux cartes

  const inLynx = pathname.startsWith("/requirements");
  const inRag = RAG_PREFIXES.some((p) => pathname.startsWith(p));
  const links = inRag
    ? [...RAG_LINKS, ...RAG_TAIL, ...(expert ? RAG_EXPERT : [])]
    : [];
  const activeTab = inLynx ? (params.get("tab") ?? "matrice") : null;

  return (
    <nav className="flex items-center gap-5 text-sm text-fg-muted">
      <Link href="/" className="text-fg-faint transition-colors hover:text-foreground">
        ← Accueil
      </Link>
      {inLynx && LYNX_TABS.map((t) => (
        <Link
          key={t.tab}
          href={t.tab === "matrice" ? "/requirements" : `/requirements?tab=${t.tab}`}
          replace scroll={false}
          aria-current={activeTab === t.tab ? "page" : undefined}
          className={`transition-colors hover:text-foreground ${
            activeTab === t.tab ? "text-accent-bright" : ""
          }`}
        >
          {t.label}
        </Link>
      ))}
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
      {inRag && (
        <Link
          href="/informations"
          title="Aide : ce que fait chaque compétence du moteur, et quand."
          aria-label="Aide"
          aria-current={pathname.startsWith("/informations") ? "page" : undefined}
          className={`flex h-5 w-5 items-center justify-center rounded-full border text-[11px] transition-colors hover:border-accent/60 hover:text-foreground ${
            pathname.startsWith("/informations")
              ? "border-accent/60 text-accent-bright" : "border-edge text-fg-faint"
          }`}
        >
          ?
        </Link>
      )}
      {inRag && <ExpertToggle />}
    </nav>
  );
}
