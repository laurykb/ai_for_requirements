import Link from "next/link";

import { Intro } from "@/components/intro";

/** Accueil : intro Thales (une fois par session) puis hero typographique +
 * deux cartes d'entrée, une par outil. */

const TOOLS = [
  {
    href: "/rag",
    kicker: "RAG documentaire",
    title: "Outil RAG",
    description:
      "Posez vos questions sur vos documents ingérés. Réponses sourcées, " +
      "raisonnement visible, tout se passe sur cette machine.",
  },
  {
    href: "/requirements",
    kicker: "Vérification d'exigences",
    title: "AI for Requirements",
    description:
      "Faites relire votre matrice d'exigences par des agents spécialisés : " +
      "verdicts argumentés, corrections suggérées, boîte de verre.",
  },
];

export default function HomePage() {
  return (
    <div className="home-root relative flex min-h-[calc(100vh-9rem)] flex-col items-center justify-center gap-12 py-8 text-center">
      <Intro />
      {/* Le perchoir du logo : l'intro vient s'y loger — invisible tant que
          l'animation joue (le glissement doit arriver sur une zone vide). */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        id="home-thales"
        src="/thales.svg"
        alt="Thales"
        className="absolute left-1/2 top-12 h-9 w-auto -translate-x-1/2"
      />
      <div className="rise-in">
        <p className="text-[11px] font-medium uppercase tracking-[0.3em] text-fg-faint">
          AI for SSH
        </p>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight sm:text-5xl">
          Vos documents, vos exigences,{" "}
          <span className="text-accent-bright">votre machine</span>.
        </h1>
        <p className="mx-auto mt-4 max-w-xl text-sm leading-relaxed text-fg-muted">
          Deux outils d&apos;ingénierie assistée par IA, entièrement locaux : un RAG pour
          interroger vos documents, un vérificateur pour auditer vos exigences.
        </p>
      </div>

      <div className="grid w-full max-w-3xl gap-4 sm:grid-cols-2">
        {TOOLS.map((tool, i) => (
          <Link
            key={tool.href}
            href={tool.href}
            className={`group rounded-xl border border-edge bg-surface p-6 text-left transition-all hover:-translate-y-0.5 hover:border-accent/60 hover:bg-surface-2 ${
              i === 0 ? "rise-in-2" : "rise-in-3"
            }`}
          >
            <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-fg-faint">
              {tool.kicker}
            </p>
            <h2 className="mt-2 text-lg font-semibold text-foreground">{tool.title}</h2>
            <p className="mt-2 text-sm leading-relaxed text-fg-muted">{tool.description}</p>
            <p className="mt-4 text-sm text-accent-bright transition-transform group-hover:translate-x-0.5">
              Entrer →
            </p>
          </Link>
        ))}
      </div>
    </div>
  );
}
