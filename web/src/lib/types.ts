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
    /** Baseline LynX : identité de l'exigence (citations -> arbre). */
    req_id?: string;
    req_niveau?: number;
    req_domaine?: string;
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

export type EvidenceDossier = { mode: string; contract: { id: string; label: string; sections: string[]; instructions: string }; points_to_cover: string[]; evidence_count: number };

export type AnswerValidation = { contract_id: string; contract_label: string; required_sections: string[]; present_sections: string[]; missing_sections: string[]; structure_complete: boolean; enforcement?: "enforced" | "shadow"; citation_count: number; invalid_citations: number[]; citation_coverage: number; evidence_count: number; completion: "complete" | "partial" };

export type EvidenceRow = { axis: string; category: string; element: string; characterization: string; target_objective: string; sources: string[]; evidence: { quote: string; source: string; verified: boolean }[]; status: "validated" | "to_review" };

export type AnalysisArtifact = { rows: EvidenceRow[]; candidates: number; consolidated: number; validated: number; to_review: number };

export type TaskProgress = { phase: string; label: string; status: "running" | "completed" | "partial" | "failed"; current?: number; total?: number; detail?: string | Record<string, unknown> };

export type TaskMetrics = { task_id: string; kind: string; status: "completed" | "partial" | "failed" | "stopped" | "budget_exceeded"; latency_s: number; outcome: { completion?: string; route?: string; found?: boolean; error?: string }; trajectory: { llm_calls: number; llm_calls_by_role: Record<string, number>; llm_calls_by_operation?: Record<string, number>; prompt_tokens: number; completion_tokens: number; llm_time_s: number; llm_share?: number | null; tool_calls: number; steps: number; replans: number }; efficiency: { total_tokens: number; llm_calls: number; llm_time_s: number; prompt_eval_s?: number; decode_s?: number; load_s?: number; latency_s: number }; quality: { semantic_judge: "not_run" | "completed" } };

/** Trames SSE de POST /api/ask. */
export type AskEvent =
  | { type: "task"; task_id: string; status: "running" }
  | { type: "task_metrics"; metrics: TaskMetrics }
  | { type: "answer_contract"; dossier: EvidenceDossier }
  | { type: "answer_validation"; validation: AnswerValidation }
  | { type: "analysis_artifact"; artifact: AnalysisArtifact }
  | ({ type: "task_progress" } & TaskProgress)
  | { type: "session"; id: string }
  | { type: "route"; mode: "rag" | "agent" | "synth"; reason: string }
  | { type: "strategy"; policy_version: string; query_type: string; intent: string;
      mode: "rag" | "agent" | "synth"; requested_mode: string; mode_source: string;
      signals: string[]; retrieval: { profile: string; parent_child: boolean;
      parent_child_source: string; self_rag: boolean; self_rag_source: string };
      verify: boolean; rationale: string[] }
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
  | { type: "coverage"; documents: DocumentCoverage; axes?: AxisCoverage }
  | ({ type: "attribution" } & Attribution)
  | { type: "eval"; faithfulness?: number; answer_relevance?: number;
      context_relevance?: number; issues?: string[];
      n_affirmations?: number; n_sourcees?: number; n_non_sourcees?: number }
  | { type: "done"; found: boolean }
  | { type: "error"; message: string };

export type DocumentCoverage = { attempted: number; with_evidence: number; ratio: number | null;
  documents_with_evidence: string[]; documents_without_evidence: string[] };
export type AxisCoverage = { checked: boolean; missing: string[]; repair_attempts: number };

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
  /** Vérification sémantique à la demande. */
  eval?: EvalResult;
  /** Attribution par affirmation (passe post-hoc, persistée en session). */
  attribution?: Attribution;
  documentCoverage?: DocumentCoverage;
  axisCoverage?: AxisCoverage;
  taskMetrics?: TaskMetrics;
  processingTrace?: TaskProgress[];
  evidenceDossier?: EvidenceDossier;
  answerValidation?: AnswerValidation;
  analysisArtifact?: AnalysisArtifact;
};
