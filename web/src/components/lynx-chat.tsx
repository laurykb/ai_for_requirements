"use client";

/** Chat sur la baseline d'exigences (espace AI for Requirements).
 *
 * Même moteur que l'Outil RAG (routage, agent, synthèse, réponses sourcées,
 * boîte de verre) mais le périmètre documentaire est VERROUILLÉ sur la
 * matrice d'exigences de l'arbre : pas d'upload, pas de sélection de
 * document, et un bandeau d'état rappelle en permanence à qui l'on parle.
 *
 * La baseline est indexée à la demande (bouton Synchroniser) : l'empreinte
 * de l'arbre est comparée à celle de l'index pour signaler toute dérive. */

import { useCallback, useEffect, useRef, useState } from "react";

import { API_BASE, getJSON, type LynxChatStatus } from "@/lib/api";
import { Chat, type ChatScope } from "@/components/chat";
import { Banner, Dot, Hint, Spinner } from "@/components/ui";

const SCOPE_HINT =
  "Ce chat ne consulte QUE la baseline d'exigences chargée dans l'arbre " +
  "(onglet Matrice). Les documents de l'Outil RAG n'entrent jamais dans ses réponses.";

function scopeFor(status: LynxChatStatus): ChatScope {
  return {
    source: status.source,
    label: `Baseline d'exigences · ${status.n_exigences} exigence${status.n_exigences > 1 ? "s" : ""}`,
    hint: SCOPE_HINT,
    emptyTitle: "Posez une question sur la baseline d'exigences",
    emptyText:
      "Réponses sourcées citant les exigences de l'arbre — rien d'autre. " +
      "Tout reste sur cette machine.",
    examples: [
      "Quelles exigences couvrent la cybersécurité ?",
      "Y a-t-il des exigences redondantes sur l'alimentation ?",
      "Résume les exigences de niveau L1 et leurs dérivées.",
    ],
  };
}

export function LynxChat() {
  const [status, setStatus] = useState<LynxChatStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Verrou « déjà prêt » : une resynchronisation purge l'index avant de le
  // reconstruire (indexed_chunks retombe à 0 quelques secondes) — le chat en
  // cours ne doit pas être démonté (perte du fil affiché) pendant ce laps.
  const [everReady, setEverReady] = useState(false);

  // Auto-resynchronisation : dérive confirmée sur 2 polls consécutifs (~8 s,
  // laisse passer une rafale d'éditions de l'arbre) -> resync déclenchée
  // seule. Jamais après une erreur de sync (sync_error) : là, on laisse la
  // main à l'utilisateur (bouton) plutôt que de boucler sur un échec.
  const driftPolls = useRef(0);

  const refresh = useCallback(() => {
    getJSON<LynxChatStatus>("/api/lynx/chat/status")
      .then((s) => {
        setStatus(s);
        setError(null);
        if (s.indexed_chunks > 0) setEverReady(true);
        const drifting = s.available && s.n_exigences > 0 && s.indexed_chunks > 0
          && !s.in_sync && !s.syncing && !s.sync_error;
        driftPolls.current = drifting ? driftPolls.current + 1 : 0;
        if (driftPolls.current === 2) {
          driftPolls.current = 0;
          void fetch(`${API_BASE}/api/lynx/chat/sync`, { method: "POST" }).catch(() => null);
        }
      })
      .catch(() => setError("API locale injoignable — lancer : python serve.py"));
  }, []);

  // Poll léger : suit la synchronisation en cours et la dérive de la baseline
  // (une exigence modifiée dans l'onglet Matrice désynchronise l'index).
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 4000);
    return () => clearInterval(t);
  }, [refresh]);

  const sync = async () => {
    setError(null);
    const res = await fetch(`${API_BASE}/api/lynx/chat/sync`, { method: "POST" })
      .catch(() => null);
    if (!res) { setError("API locale injoignable."); return; }
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setError(String(body.detail ?? `HTTP ${res.status}`));
      return;
    }
    refresh();
  };

  if (!status) {
    return <p className="mt-6 flex items-center gap-2 text-xs text-fg-muted">
      <Spinner /> Interrogation de l&apos;index de la baseline…
    </p>;
  }

  const ready = status.indexed_chunks > 0 || (everReady && status.syncing);

  return (
    <div className="mt-4">
      {/* Bandeau d'état : à qui parle-t-on, et l'index est-il à jour ? */}
      <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-edge bg-surface px-3 py-2 text-xs">
        <span className="flex items-center gap-2 text-fg-muted">
          <Dot tone={ready ? (status.in_sync ? "good" : "warn") : "neutral"} />
          <span className="font-medium text-foreground">Baseline d&apos;exigences</span>
          {status.n_exigences} exigence{status.n_exigences > 1 ? "s" : ""} dans l&apos;arbre
          {ready && <>· {status.indexed_chunks} passage{status.indexed_chunks > 1 ? "s" : ""} indexé{status.indexed_chunks > 1 ? "s" : ""}</>}
        </span>
        {ready && !status.syncing && (status.in_sync
          ? <span className="text-good">index à jour</span>
          : <span className="text-warn">
              {status.sync_error
                ? "baseline modifiée — resynchronisation manuelle requise"
                : "baseline modifiée — resynchronisation automatique…"}
            </span>)}
        {status.syncing && (
          <span className="flex items-center gap-2 text-fg-muted">
            <Spinner /> Synchronisation… {status.sync_pct != null ? `${status.sync_pct} %` : ""}
          </span>
        )}
        <span className="ml-auto flex items-center gap-2">
          <Hint text={SCOPE_HINT} />
          {!status.syncing && status.n_exigences > 0 && (!ready || !status.in_sync) && (
            <button
              onClick={() => void sync()}
              className="cursor-pointer rounded-lg border border-accent/60 px-2.5 py-1 text-[11px] text-accent-bright transition-colors hover:bg-accent/10"
            >
              {ready ? "Resynchroniser" : "Synchroniser la baseline"}
            </button>
          )}
        </span>
      </div>

      {status.sync_error && (
        <Banner tone="bad">Synchronisation échouée : {status.sync_error}</Banner>
      )}
      {error && <Banner tone="bad">{error}</Banner>}
      {!status.available && (
        <Banner tone="warn">MongoDB injoignable — l&apos;état de l&apos;index est inconnu.</Banner>
      )}

      {status.n_exigences === 0 ? (
        <Banner tone="neutral">
          Baseline vide : chargez ou importez une matrice d&apos;exigences dans
          l&apos;onglet Matrice, puis synchronisez-la ici pour l&apos;interroger.
        </Banner>
      ) : !ready ? (
        <div className="rounded-xl border border-edge bg-surface p-6 text-center">
          <p className="text-sm font-medium text-foreground">
            La baseline n&apos;est pas encore indexée pour le chat
          </p>
          <p className="mx-auto mt-2 max-w-md text-xs leading-relaxed text-fg-muted">
            La synchronisation sérialise l&apos;arbre d&apos;exigences puis l&apos;indexe
            (découpage, embeddings, BM25). Quelques secondes suffisent ;
            à refaire après chaque évolution de la baseline.
          </p>
          {!status.syncing && (
            <button
              onClick={() => void sync()}
              className="mt-4 cursor-pointer rounded-lg bg-accent px-4 py-2 text-sm font-medium text-background transition-colors hover:bg-accent-bright"
            >
              Synchroniser la baseline
            </button>
          )}
        </div>
      ) : (
        <Chat scope={scopeFor(status)} />
      )}
    </div>
  );
}
