"use client";

/** Composeur du chat : zone de saisie, pièce jointe (progression + erreurs),
 * périmètre documentaire, modèle de génération et mode de traitement.
 * Purement présentationnel : la logique (envoi, indexation) reste dans
 * index.tsx et arrive par props. */

import type { RefObject } from "react";

import { Dot, Hint } from "@/components/ui";

export type Mode = "auto" | "rag" | "agent" | "deep";

/** Indexation d'une pièce jointe en cours : { name, pct, step }. */
export type AttachState = { name: string; pct: number; step: string };

export function Composer({
  input, setInput, disabled, busy, attaching, attach, attachError, onDismissError,
  onAsk, onStop, fileRef, onAttachFiles, selected, setSelected, docs,
  models, genModel, onLoadModel, modelStatus, mode, setMode, expert,
}: {
  input: string;
  setInput: (v: string) => void;
  /** Moteur indisponible (Ollama/API arrêté) : envoi suspendu. */
  disabled?: boolean;
  busy: boolean;
  attaching: boolean;
  attach: AttachState | null;
  attachError: string | null;
  onDismissError: () => void;
  onAsk: (question: string) => void;
  onStop: () => void;
  fileRef: RefObject<HTMLInputElement | null>;
  onAttachFiles: (files: FileList | null) => void;
  selected: string;
  setSelected: (v: string) => void;
  docs: { name: string; chunks: number }[];
  models: string[];
  genModel: string;
  onLoadModel: (model: string) => void;
  modelStatus: string | null;
  mode: Mode;
  setMode: (m: Mode) => void;
  expert: boolean;
}) {
  return (
    <>
      {/* Pièce jointe en cours : barre de progression visible, envoi bloqué. */}
      {attach && (
        <div className="mb-2 rounded-xl border border-edge bg-surface-2 px-3 py-2">
          <div className="mb-1 flex items-baseline justify-between gap-2 text-xs">
            <span className="flex items-center gap-2 text-fg-muted">
              <Dot tone="accent" pulse /> Indexation de {attach.name}
            </span>
            <span className="font-mono tabular-nums text-fg-faint">{attach.pct} %</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-muted">
            <div className="h-full rounded-full bg-accent transition-[width] duration-500"
                 style={{ width: `${attach.pct}%` }} />
          </div>
          <p className="mt-1 text-[11px] text-fg-faint">
            {attach.step} — vous pourrez interroger ce document dès la fin de
            l&apos;indexation.
          </p>
        </div>
      )}
      {attachError && (
        <div className="mb-2 flex items-center justify-between gap-2 rounded-xl border border-bad/40 bg-surface-2 px-3 py-2 text-xs text-fg-muted">
          <span>{attachError}</span>
          <button onClick={onDismissError} aria-label="Fermer"
                  className="cursor-pointer text-fg-faint hover:text-foreground">✕</button>
        </div>
      )}

      {/* Composeur. */}
      <div className="rounded-2xl border border-edge bg-surface-2 p-2 focus-within:border-accent/60">
        <form
          onSubmit={(e) => { e.preventDefault(); onAsk(input); }}
        >
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (!attaching && !disabled) onAsk(input);
              }
            }}
            disabled={busy || disabled}
            rows={1}
            placeholder="Posez une question sur vos documents…"
            aria-label="Question"
            className="max-h-40 w-full resize-none bg-transparent px-2 py-1.5 text-sm text-foreground [field-sizing:content] placeholder:text-fg-faint focus:outline-none disabled:opacity-60"
          />
          <div className="mt-1 flex items-center gap-2">
            <input ref={fileRef} type="file" multiple hidden
                   accept=".pdf,.docx,.pptx,.xlsx,.html,.md"
                   onChange={(e) => onAttachFiles(e.target.files)} />
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              disabled={attaching}
              title="Joindre un document : indexé avec les réglages par défaut (réglages fins dans l'onglet Documents). L'envoi attend la fin de l'indexation."
              className="cursor-pointer rounded-lg p-1.5 text-fg-faint transition-colors hover:bg-muted hover:text-foreground disabled:cursor-default disabled:opacity-40"
              aria-label="Joindre un document"
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden>
                <path d="M21 12.5 12.6 21a5.6 5.6 0 0 1-8-8L13 4.5a3.7 3.7 0 0 1 5.3 5.3L10 18a1.9 1.9 0 0 1-2.7-2.7l7.6-7.5"
                      stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
              </svg>
            </button>
            <select
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
              disabled={busy}
              aria-label="Document interrogé"
              title="Périmètre : un document, ou tous."
              className="max-w-56 cursor-pointer rounded-lg border border-edge bg-surface px-2 py-1 text-[11px] text-fg-muted focus:outline-none"
            >
              <option value="">Tous les documents</option>
              {docs.map((d) => (
                <option key={d.name} value={d.name}>{d.name}</option>
              ))}
            </select>
            {selected !== "" && (
              <button
                type="button"
                onClick={() => setSelected("")}
                disabled={busy}
                title="Périmètre restreint à ce document — cliquer pour revenir à tous les documents"
                aria-label={`Périmètre : ${selected} — revenir à tous les documents`}
                className="inline-flex max-w-56 cursor-pointer items-center gap-1 truncate rounded-full border border-accent/40 bg-accent/10 px-2 py-0.5 text-[11px] text-fg-muted transition-colors hover:text-foreground disabled:cursor-default disabled:opacity-40"
              >
                <span className="truncate">Périmètre : {selected}</span>
                <span aria-hidden>✕</span>
              </button>
            )}
            {models.length > 0 && (
              <div className="flex items-center gap-1">
                <span className="text-[11px] text-fg-faint">Réponse finale</span>
                <Hint text="Choisit uniquement le modèle qui rédige la réponse finale. Les modèles d’orchestration, de recherche, d’extraction, de synthèse et d’évaluation restent ceux définis dans Réglages du chat." />
                <select
                  value={genModel}
                  onChange={(e) => onLoadModel(e.target.value)}
                  disabled={busy}
                  aria-label="Modèle de génération de la réponse finale"
                  title="Modèle de génération de la réponse finale : changé à chaud et épinglé en VRAM."
                  className="max-w-44 cursor-pointer rounded-lg border border-edge bg-surface px-2 py-1 font-mono text-[11px] text-fg-muted focus:outline-none"
                >
                  {(models.includes(genModel) ? models : [genModel, ...models]).map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              </div>
            )}
            {modelStatus && (
              <span className={`text-[11px] ${modelStatus === "chargé" ? "text-good"
                : modelStatus === "chargement…" ? "text-fg-faint" : "text-bad"}`}>
                {modelStatus}
              </span>
            )}
            {(
              <select
                value={mode}
                onChange={(e) => setMode(e.target.value as Mode)}
                disabled={busy}
                aria-label="Mode de traitement"
                title="Auto : stratégie équilibrée. Analyse profonde : couverture exhaustive et raisonnement long, potentiellement plusieurs minutes."
                className="cursor-pointer rounded-lg border border-edge bg-surface px-2 py-1 text-[11px] text-fg-muted focus:outline-none"
              >
                <option value="auto">Auto</option>
                <option value="deep">Analyse profonde</option>
                {expert && <option value="rag">RAG</option>}
                {expert && <option value="agent">Agent</option>}
              </select>
            )}
            {busy ? (
              <button
                type="button"
                onClick={onStop}
                title="Arrête la génération immédiatement (la réponse partielle est conservée)."
                className="ml-auto cursor-pointer rounded-xl border border-bad/40 px-3.5 py-1.5 text-sm font-medium text-bad transition-all duration-200 hover:bg-bad/15 active:scale-[0.97]"
                aria-label="Arrêter la génération"
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
                  <rect x="6" y="6" width="12" height="12" rx="1.5" />
                </svg>
              </button>
            ) : (
              <button
                type="submit"
                disabled={attaching || disabled || !input.trim()}
                className="ml-auto cursor-pointer rounded-xl bg-accent px-3.5 py-1.5 text-sm font-medium text-background transition-all duration-200 hover:bg-accent-bright active:scale-[0.97] disabled:cursor-default disabled:opacity-40"
                aria-label="Envoyer"
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden>
                  <path d="M4 12h14M13 6l6 6-6 6" stroke="currentColor" strokeWidth="2"
                        strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
            )}
          </div>
        </form>
      </div>
    </>
  );
}
