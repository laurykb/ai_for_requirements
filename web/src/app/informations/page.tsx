import { Panel, PanelTitle } from "@/components/ui";

/** Aide contextuelle du Chat : explique les compétences du système (ce que chacune
 * fait et quand elle se déclenche), y compris le mode Synthèse corpus. Statique. */

type Skill = { title: string; does: string; when: string };

const SKILLS: Skill[] = [
  {
    title: "Recherche hybride (sémantique + BM25)",
    does: "Combine une recherche vectorielle (sens) et une recherche par mots-clés (BM25), fusionnées par RRF, pour trouver les passages pertinents dans vos documents.",
    when: "À chaque question adressée au RAG.",
  },
  {
    title: "Reranking & plancher de couverture",
    does: "Un cross-encoder reclasse finement les passages ; un plancher par document garantit qu'un petit document pertinent n'est jamais écrasé par un gros, tout en protégeant les meilleurs passages.",
    when: "Après la recherche, quand le périmètre est « Tous les documents ».",
  },
  {
    title: "Détection d'intention & routage automatique",
    does: "Analyse la question (sans appel LLM) pour l'aiguiller : réponse directe (RAG), raisonnement multi-étapes (Agent) ou synthèse balayant tout le corpus.",
    when: "En mode Auto, pour chaque question.",
  },
  {
    title: "Agent planificateur (multi-étapes)",
    does: "Décompose une question complexe en sous-questions, cherche pour chacune, puis rédige une réponse de synthèse — la « boîte de verre » montre le plan en direct.",
    when: "En Auto sur une question de comparaison, relationnelle ou multi-document ; ou en mode Agent forcé.",
  },
  {
    title: "Synthèse corpus (map-reduce)",
    does: "Balaie chaque document du corpus (MAP : extraction de l'aspect demandé, par document), puis agrège et catégorise le tout (REDUCE) avec attribution des sources. Conçu pour rester fiable jusqu'à des dizaines de documents.",
    when: "Sur une demande d'agrégation : « catégorise / liste / recense toutes les X du corpus ».",
  },
  {
    title: "Auto-correction (Self-RAG)",
    does: "Évalue la réponse produite et la retente si elle est jugée insuffisante, pour améliorer la fiabilité.",
    when: "Quand l'option Self-RAG est active (réglable dans Réglages).",
  },
  {
    title: "Attribution par affirmation",
    does: "Après la réponse, relie chaque affirmation aux passages sources cités, pour rendre la traçabilité visible.",
    when: "Après chaque réponse sourcée.",
  },
  {
    title: "Abstention hors-scope",
    does: "Si aucun passage n'est réellement pertinent, le système s'abstient plutôt que d'inventer — sauf sur les questions exploratoires (« de quoi parle ce document », « c'est quoi… ») où il retourne le meilleur contexte disponible.",
    when: "À la fin de la recherche, selon le score de pertinence et le type de question.",
  },
];

export default function InformationsPage() {
  return (
    <div className="rise-in py-4">
      <div className="mx-auto max-w-3xl">
        <PanelTitle
          kicker="Chat documentaire"
          title="Aide et fonctionnement"
          hint="Ce que fait chaque brique, et quand elle se déclenche."
        />
        <div className="flex flex-col gap-3">
          {SKILLS.map((s) => (
            <Panel key={s.title}>
              <h3 className="mb-2 text-sm font-semibold text-foreground">{s.title}</h3>
              <p className="mb-2 text-[13px] leading-relaxed text-fg-muted">
                <span className="text-fg-faint">Ce que ça fait — </span>
                {s.does}
              </p>
              <p className="text-[13px] leading-relaxed text-fg-muted">
                <span className="text-fg-faint">Quand ça se déclenche — </span>
                {s.when}
              </p>
            </Panel>
          ))}
        </div>
      </div>
    </div>
  );
}
