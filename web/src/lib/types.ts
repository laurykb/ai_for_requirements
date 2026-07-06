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

/** Trames SSE de POST /api/ask. */
export type AskEvent =
  | { type: "session"; id: string }
  | { type: "route"; mode: "rag" | "agent"; reason: string }
  | { type: "stage"; stage: "retrieve" | "generate" }
  | { type: "retrieved"; chunks: ChunkView[] }
  | { type: "thought"; text: string }
  | { type: "action"; text: string }
  | { type: "observation"; text: string }
  | { type: "token"; text: string }
  | { type: "sources"; citations: Citation[] }
  | { type: "eval"; faithfulness?: number; answer_relevance?: number;
      context_relevance?: number; issues?: string[] }
  | { type: "done"; found: boolean }
  | { type: "error"; message: string };

export type EvalResult = { faithfulness?: number | null; answer_relevance?: number | null;
                           context_relevance?: number | null; issues?: string[] };

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
};
