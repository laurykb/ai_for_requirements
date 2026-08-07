"use client";

/** Blocs d'affichage d'une réponse de l'assistant (boîte de verre) :
 * sources citées, passages récupérés (cochables), vérification automatique
 * et message assistant complet. Le clic sur un marqueur [n] du texte ouvre
 * le bloc des passages et surligne le passage n. Aucune logique réseau ici. */

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

import { fmt } from "@/lib/format";
import type { AnalysisArtifact, AnswerValidation, Attribution, ChatMessage, ChunkView, Citation, EvalResult, TaskProgress } from "@/lib/types";
import { Banner, Dot, Hint } from "@/components/ui";
import { AnswerMarkdown } from "@/components/chat/markdown";

/** Cible d'un clic sur un marqueur [n] (ts force l'effet à chaque clic). */
export type CiteFocus = { idx: number; ts: number };

export const NOT_FOUND_MESSAGE =
  "Je n'ai pas trouvé d'information sur ce sujet dans vos documents. " +
  "Essayez de reformuler, ou choisissez un autre document à interroger.";

/** Libellé d'un passage : source – section – page – score. */
export function chunkLabel(c: ChunkView, i: number): string {
  const m = c.meta;
  const loc =
    m.heading ?? m.breadcrumb ?? (m.section_idx != null ? `section ${m.section_idx}` : "");
  const page = m.page_number ? ` – p. ${m.page_number}` : "";
  const score = typeof c.ce_score === "number" ? ` – score ${fmt(c.ce_score)}` : "";
  return `[${i + 1}] ${m.source ?? "document"}${loc ? ` – ${loc}` : ""}${page}${score}`;
}

export function SourcesBlock({ citations }: { citations: Citation[] }) {
  if (!citations.length) return null;
  return (
    <details className="chat-details">
      <summary>Sources ({citations.length})</summary>
      <ul className="mt-2 space-y-1 text-xs text-fg-muted">
        {citations.map((c) => (
          <li key={c.idx}>
            <span className="font-mono text-fg-faint">[{c.idx}]</span> {c.source}
            {" – "}
            {c.heading ?? c.breadcrumb ?? `section ${c.section ?? "?"}`}
            {c.page ? ` – p. ${c.page}` : ""}
          </li>
        ))}
      </ul>
    </details>
  );
}

/** Passages récupérés (boîte de verre). Sur la DERNIÈRE réponse : cases à
 * cocher + « Régénérer avec la sélection ». `focus` (clic sur un marqueur
 * [n] du texte) ouvre le bloc, déplie le passage n, le fait défiler et le
 * surligne temporairement. */
export function ChunksBlock({ chunks, canRegenerate, onRegenerate, focus }: {
  chunks: ChunkView[];
  canRegenerate?: boolean;
  onRegenerate?: (selected: ChunkView[]) => void;
  focus?: CiteFocus | null;
}) {
  const [checked, setChecked] = useState<boolean[]>(() => chunks.map(() => true));
  const rootRef = useRef<HTMLDetailsElement>(null);
  const itemRefs = useRef<(HTMLDetailsElement | null)[]>([]);
  const rowRefs = useRef<(HTMLDivElement | null)[]>([]);

  // Synchronisation DOM (ouverture des <details>, défilement, surlignage
  // animé) : manipulation directe d'éléments — pas d'état React ici.
  useEffect(() => {
    if (!focus) return;
    const i = focus.idx - 1;
    if (i < 0 || i >= chunks.length) return;
    if (rootRef.current) rootRef.current.open = true;
    const el = itemRefs.current[i];
    if (el) {
      el.open = true;
      el.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    const row = rowRefs.current[i];
    if (row) {
      row.classList.remove("chunk-flash");
      void row.offsetWidth; // force le reflow -> l'animation redémarre
      row.classList.add("chunk-flash");
    }
    const t = setTimeout(() => row?.classList.remove("chunk-flash"), 1900);
    return () => clearTimeout(t);
  }, [focus, chunks.length]);

  if (!chunks.length) return null;
  return (
    <details className="chat-details" ref={rootRef}>
      <summary>
        Passages récupérés ({chunks.length})
        <Hint text="Le contenu exact que le moteur de recherche a retrouvé et fourni au modèle pour répondre. Un marqueur [n] de la réponse renvoie au passage n." />
      </summary>
      <div className="mt-2 space-y-2">
        {chunks.map((c, i) => (
          <div key={i} ref={(el) => { rowRefs.current[i] = el; }}
               className="flex items-start gap-2 rounded-md">
            {canRegenerate && (
              <input
                type="checkbox"
                checked={checked[i] ?? true}
                onChange={(e) =>
                  setChecked((cs) => cs.map((v, j) => (j === i ? e.target.checked : v)))}
                className="mt-2.5 accent-(--accent)"
                aria-label={`garder le passage ${i + 1}`}
              />
            )}
            <details className="chat-details min-w-0 flex-1"
                     ref={(el) => { itemRefs.current[i] = el; }}>
              <summary className="text-xs">{chunkLabel(c, i)}</summary>
              <div className="chat-md mt-2 max-h-72 overflow-y-auto text-xs text-fg-muted">
                <ReactMarkdown>{c.doc}</ReactMarkdown>
              </div>
              {(c.meta.keywords_str || c.meta.entities_str) && (
                <p className="mt-2 border-t border-edge pt-2 text-[11px] text-fg-faint">
                  {c.meta.keywords_str && <>Mots-clés – {c.meta.keywords_str}</>}
                  {c.meta.keywords_str && c.meta.entities_str && <br />}
                  {c.meta.entities_str && <>Entités – {c.meta.entities_str}</>}
                </p>
              )}
            </details>
          </div>
        ))}
        {canRegenerate && onRegenerate && (
          <button
            onClick={() => onRegenerate(chunks.filter((_, i) => checked[i] ?? true))}
            className="cursor-pointer rounded-md border border-accent/50 px-2 py-1 text-[11px] text-accent-bright transition-colors hover:bg-accent/10"
          >
            Régénérer avec la sélection
          </button>
        )}
      </div>
    </details>
  );
}

export function EvalBlock({ e, attribution }: { e: EvalResult; attribution?: Attribution }) {
  // Compteurs d'attribution : depuis la passe post-hoc si disponible, sinon
  // depuis la trame `eval` enrichie (rechargement de session).
  const nAff = attribution?.ok ? attribution.n_affirmations : e.n_affirmations;
  const nNon = (attribution?.ok ? attribution.n_non_sourcees : e.n_non_sourcees) ?? 0;
  const axes = [e.faithfulness, e.answer_relevance, e.context_relevance]
    .filter((v): v is number => typeof v === "number");
  const overall = axes.length ? axes.reduce((a, b) => a + b, 0) / axes.length : 0;
  const verdict = overall >= 0.8 ? "Bonne" : overall >= 0.6 ? "À vérifier" : "Fragile";
  const verdictCls = overall >= 0.8 ? "text-good" : overall >= 0.6 ? "text-warn" : "text-bad";
  return (
    <div className="mt-2 rounded-lg border border-edge bg-surface-2 px-3 py-2 text-xs">
      <p className="flex items-center gap-1.5 text-[11px] uppercase tracking-[0.14em] text-fg-faint">
        Qualité estimée · <span className={verdictCls}>{verdict} {Math.round(overall * 100)} %</span>
        <Hint text="Évaluation locale sans réponse de référence : fidélité aux preuves, pertinence de la réponse et pertinence du contexte (0 → 1). Ce score est un indicateur, pas une garantie de vérité." />
      </p>
      <div className="mt-1.5 flex flex-wrap gap-x-5 gap-y-1 font-mono tabular-nums text-fg-muted">
        <span>Fidélité {fmt(e.faithfulness ?? 0)}</span>
        <span>Pertinence réponse {fmt(e.answer_relevance ?? 0)}</span>
        <span>Pertinence contexte {fmt(e.context_relevance ?? 0)}</span>
        {nAff != null && nAff > 0 && (
          <span>
            Affirmations sourcées {nAff - nNon}/{nAff}
            {nNon > 0 && <span className="text-warn"> · {nNon} non sourcée{nNon > 1 ? "s" : ""}</span>}
          </span>
        )}
      </div>
      {(e.issues?.length ?? 0) > 0 && (
        <ul className="mt-1.5 list-disc pl-4 text-fg-faint">
          {e.issues!.map((it, i) => <li key={i}>{it}</li>)}
        </ul>
      )}
    </div>
  );
}

export function AnswerValidationBlock({ validation }: { validation: AnswerValidation }) {
  const complete = validation.completion === "complete";
  const shadow = validation.enforcement === "shadow";
  return (
    <details className="chat-details mt-2">
      <summary className="flex items-center gap-2">
        <Dot tone={complete ? "good" : "warn"} />
        Format détecté : {validation.contract_label} · {validation.citation_count} citation(s)
      </summary>
      <div className="mt-2 grid gap-2 text-xs text-fg-muted sm:grid-cols-2">
        <span>{shadow ? "Format observé, non imposé" : validation.structure_complete ? "Structure complète" : "Sections manquantes : " + validation.missing_sections.join(", ")}</span>
        <span>Couverture citationnelle observable : {Math.round(validation.citation_coverage * 100)} %</span>
        {validation.invalid_citations.length > 0 && (
          <span className="text-warn">Marqueurs invalides : {validation.invalid_citations.join(", ")}</span>
        )}
        <span>Preuves disponibles : {validation.evidence_count}</span>
      </div>
    </details>
  );
}

export function AnalysisResultsBlock({ artifact }: { artifact: AnalysisArtifact }) {
  if (!artifact.rows.length) return null;
  const downloadCsv = () => {
    const cells = (values: string[]) => values.map((v) => "\"" + v.replaceAll("\"", "\"\"") + "\"").join(",");
    const header = ["Axe", "Catégorie", "Élément", "Caractérisation", "Objectif visé", "Sources", "Statut"];
    const rows = artifact.rows.map((r) => [r.axis, r.category, r.element, r.characterization, r.target_objective, r.sources.join("; "), r.status === "validated" ? "Validé" : "À revoir"]);
    const csv = [header, ...rows].map(cells).join("\n");
    const url = URL.createObjectURL(new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a"); link.href = url; link.download = "resultats-analyse.csv"; link.click(); URL.revokeObjectURL(url);
  };
  return <details className="chat-details mt-2"><summary>Résultats détaillés · {artifact.consolidated} élément(s) · {artifact.validated} validé(s) · {artifact.to_review} à revoir</summary><div className="mt-2 flex justify-end"><button onClick={downloadCsv} className="cursor-pointer rounded-md border border-edge px-2 py-1 text-[11px] text-fg-muted">Exporter en CSV</button></div><div className="mt-2 max-h-96 overflow-auto rounded-lg border border-edge"><table className="min-w-full text-left text-xs"><thead className="sticky top-0 bg-surface-2 text-fg-faint"><tr>{["Axe", "Catégorie", "Élément", "Caractérisation", "Objectif visé", "Sources", "Statut"].map((h) => <th key={h} className="px-2 py-2">{h}</th>)}</tr></thead><tbody className="divide-y divide-edge">{artifact.rows.map((r, i) => <tr key={i} className="align-top text-fg-muted"><td className="px-2 py-2">{r.axis}</td><td className="px-2 py-2">{r.category}</td><td className="px-2 py-2 text-foreground">{r.element}</td><td className="px-2 py-2">{r.characterization}</td><td className="px-2 py-2">{r.target_objective}</td><td className="px-2 py-2">{r.sources.join(", ")}</td><td className={r.status === "validated" ? "px-2 py-2 text-good" : "px-2 py-2 text-warn"}>{r.status === "validated" ? "Validé" : "À revoir"}</td></tr>)}</tbody></table></div><p className="mt-2 text-[11px] text-fg-faint">Validé signifie qu’une citation exacte a été retrouvée dans le document source.</p></details>;
}

export function ProcessingTraceBlock({ trace, route, live = false }: {
  trace: TaskProgress[]; route?: string; live?: boolean;
}) {
  if (!trace.length) return null;
  const agent = route?.toLowerCase().includes("agent") ??
    trace.some((step) => step.phase === "agent_step" || step.phase === "plan");
  const synth = route?.toLowerCase().includes("synth") ??
    trace.some((step) => ["prefilter", "map_complete", "reduce", "coverage", "repair"].includes(step.phase));
  const title = agent ? "Progression de l’agent"
    : synth ? "Progression de la synthèse" : "Traitement de la réponse";
  const content = (
    <ol className="mt-2 space-y-1 text-xs text-fg-muted">
      {trace.map((step, i) => (
        <li key={`${step.phase}-${i}`} className="flex items-baseline gap-2">
          <Dot
            tone={step.status === "failed" ? "bad" : step.status === "partial" ? "warn"
              : step.status === "completed" ? "good" : "accent"}
            pulse={live && step.status === "running" && i === trace.length - 1}
          />
          <span>
            {step.label}
            {typeof step.detail === "string" ? " · " + step.detail : ""}
            {step.current != null && step.total ? ` · ${step.current}/${step.total}` : ""}
          </span>
        </li>
      ))}
    </ol>
  );
  return live ? (
    <div className="mb-2 rounded-lg border border-edge bg-surface-2 px-3 py-2">
      <p className="text-[11px] uppercase tracking-[0.14em] text-fg-faint">{title}</p>
      {content}
    </div>
  ) : (
    <details className="chat-details mb-2">
      <summary>{title}</summary>
      {content}
    </details>
  );
}

export function AssistantMessage({ m, expert, canRegenerate, onRegenerate }: {
  m: ChatMessage; expert: boolean;
  canRegenerate?: boolean;
  onRegenerate?: (selected: ChunkView[]) => void;
}) {
  /** Clic sur un marqueur [n] du texte → ouvre le bloc des passages et
   * surligne le passage n (ts : re-déclenche l'effet à chaque clic). */
  const [focus, setFocus] = useState<CiteFocus | null>(null);
  const nPassages = m.chunks?.length ?? m.citations?.length ?? 0;
  return (
    <div className="min-w-0">
      {m.route && expert && (
        <p className="mb-1 text-[11px] text-fg-faint">Routage : {m.route}</p>
      )}
      {m.processingTrace && (
        <ProcessingTraceBlock trace={m.processingTrace} route={m.route ?? undefined} />
      )}
      {m.reasoning && (
        <details className="chat-details mb-2">
          <summary>Raisonnement de l&apos;agent</summary>
          <div className="chat-md mt-2 text-xs text-fg-muted">
            <ReactMarkdown>{m.reasoning}</ReactMarkdown>
          </div>
        </details>
      )}
      <div className="chat-md text-sm leading-relaxed">
        <AnswerMarkdown
          content={m.content}
          maxCite={nPassages}
          affirmations={m.attribution?.ok ? m.attribution.affirmations : undefined}
          onCiteClick={(n) => setFocus({ idx: n, ts: Date.now() })}
        />
      </div>
      {m.stopped && (
        <p className="mt-2 flex items-center gap-2 text-xs text-fg-faint">
          <Dot tone="warn" /> Génération arrêtée — réponse partielle.
        </p>
      )}
      {m.error && (
        <div className="mt-2">
          <Banner tone="bad">{m.error}</Banner>
        </div>
      )}
      {m.answerValidation && <AnswerValidationBlock validation={m.answerValidation} />}
      {m.analysisArtifact && <AnalysisResultsBlock artifact={m.analysisArtifact} />}
      {m.citations && <SourcesBlock citations={m.citations} />}
      {/* Passages récupérés : boîte de verre pour TOUS les modes (cocher/
          décocher + régénérer sur la dernière réponse). key : remonte le bloc
          quand la liste change (régénération) pour réaligner les cases. */}
      {m.chunks && (
        <ChunksBlock key={m.chunks.length} chunks={m.chunks}
                     canRegenerate={canRegenerate} onRegenerate={onRegenerate}
                     focus={focus} />
      )}
      {m.eval && <EvalBlock e={m.eval} attribution={m.attribution} />}
      {m.taskMetrics && <details className="chat-details mt-2"><summary>Exécution {m.taskMetrics.status === "completed" ? "terminée" : m.taskMetrics.status === "budget_exceeded" ? "arrêtée par le budget" : m.taskMetrics.status} · {m.taskMetrics.trajectory.llm_calls} appel(s) LLM · {m.taskMetrics.latency_s}s</summary><div className="mt-2 grid gap-2 text-xs text-fg-muted sm:grid-cols-3"><span>Trajectoire : {m.taskMetrics.trajectory.steps} étape(s), {m.taskMetrics.trajectory.tool_calls} outil(s), {m.taskMetrics.trajectory.replans} replan</span><span>Calcul : {m.taskMetrics.efficiency.total_tokens} tokens, {m.taskMetrics.trajectory.llm_time_s}s LLM</span><span>Charge LLM relative : {Math.round((m.taskMetrics.trajectory.llm_share ?? 0) * 100)} % du temps mur (peut dépasser 100 % en parallèle)</span></div></details>}
    </div>
  );
}
