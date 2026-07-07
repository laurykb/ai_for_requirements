/** Types partagés du front (miroir des payloads de l'API locale). */

export type Citation = {
  idx: number;
  source: string;
  page?: number | null;
  section?: number | null;
  heading?: string | null;
  breadcrumb?: string | null;
  chunk_type?: string | null;
};

/** Passage récupéré, réduit aux champs d'affichage (boîte de verre). */
export type ChunkView = {
  doc: string;
  ce_score?: number | null;
  meta: {
    source?: string;
    page_number?: number;
    heading?: string;
    breadcrumb?: string;
    section_idx?: number;
    chunk_type?: string;
    keywords_str?: string;
    questions_str?: string;
    entities_str?: string;
    summary_num_chunks?: number;
  };
};

/** Étape du plan de l'agent (planificateur-exécuteur multi-hop). */
export type PlanStep = { sous_question: string; but?: string };

/** Affirmation factuelle de la réponse, rattachée à ses passages sources
 * par la passe post-hoc d'attribution (core/attribution.py). */
export type Affirmation = {
  texte: string;
  passages: number[];
  statut: "sourcee" | "completee" | "non_sourcee";
};

/** Résultat de la passe d'attribution (ok=false : échec/timeout — la réponse
 * garde ses marqueurs inline, rien n'est bloqué). */
export type Attribution = {
  ok: boolean;
  affirmations?: Affirmation[];
  n_affirmations?: number;
  n_sourcees?: number;
  n_completees?: number;
  n_non_sourcees?: number;
  error?: string | null;
};

/** Trames SSE de POST /api/ask. */
export type AskEvent =
  | { type: "session"; id: string }
  | { type: "route"; mode: "rag" | "agent"; reason: string }
  | { type: "stage"; stage: "retrieve" | "generate" }
  | { type: "retrieved"; chunks: ChunkView[] }
  | { type: "plan"; steps: PlanStep[] }
  | { type: "step_start"; index: number; total: number; text: string }
  | { type: "step_done"; index: number; text: string; hors_scope?: boolean }
  | { type: "replan"; index: number; steps: PlanStep[] }
  | { type: "thought"; text: string }
  | { type: "action"; text: string }
  | { type: "observation"; text: string }
  | { type: "token"; text: string }
  | { type: "sources"; citations: Citation[] }
  | ({ type: "attribution" } & Attribution)
  | { type: "eval"; faithfulness?: number; answer_relevance?: number;
      context_relevance?: number; issues?: string[];
      n_affirmations?: number; n_sourcees?: number; n_non_sourcees?: number }
  | { type: "done"; found: boolean }
  | { type: "error"; message: string };

export type EvalResult = { faithfulness?: number | null; answer_relevance?: number | null;
                           context_relevance?: number | null; issues?: string[];
                           n_affirmations?: number | null; n_sourcees?: number | null;
                           n_non_sourcees?: number | null };

export type SessionInfo = { id: string; title: string; updated_at: string;
                            source_filter: string | null };

export type ChatRole = "user" | "assistant";

export type ChatMessage = {
  role: ChatRole;
  content: string;
  citations?: Citation[];
  chunks?: ChunkView[];
  /** Réponse interrompue (flux coupé, erreur) — affichée avec une bannière. */
  error?: string;
  /** Génération arrêtée volontairement (bouton Stop) — réponse partielle. */
  stopped?: boolean;
  /** Raisonnement de l'agent ReAct (pensées/recherches), replié. */
  reasoning?: string | null;
  /** Routage affiché (mode Auto) : « RAG — raison ». */
  route?: string;
  /** Vérification automatique (question à enjeu). */
  eval?: EvalResult;
  /** Attribution par affirmation (passe post-hoc, persistée en session). */
  attribution?: Attribution;
};
