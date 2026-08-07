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
import { useLynxNav } from "@/components/lynx-nav";
import { Banner, Dot, Hint, Spinner } from "@/components/ui";

type Coverage = {
  available: boolean;
  n_exigences: number;
  n_cited: number;
  domains: { domaine: string; total: number; cited: number }[];
  never_cited: { id: string; domaine: string; niveau: number }[];
  n_never: number;
  top: { id: string; n: number }[];
};

/** Couverture de la baseline par les conversations : citées / jamais citées.
 * Chargée à l'ouverture du panneau (et à chaque réouverture — les chiffres
 * bougent au fil des échanges). */
function CoveragePanel() {
  const [cov, setCov] = useState<Coverage | null>(null);
  const lynxNav = useLynxNav();

  const load = () => {
    getJSON<Coverage>("/api/lynx/chat/coverage")
      .then(setCov).catch(() => setCov(null));
  };

  return (
    <details className="chat-details mb-3 rounded-xl border border-edge bg-surface px-3 py-2"
             onToggle={(e) => { if ((e.target as HTMLDetailsElement).open) load(); }}>
      <summary className="text-xs">
        Couverture de la baseline
        <Hint text="Quelles exigences ont déjà fondé une réponse dans vos conversations, lesquelles jamais. Un domaine peu couvert = angle mort de la baseline… ou questions jamais posées." />
      </summary>
      {!cov ? (
        <p className="mt-2 flex items-center gap-2 text-xs text-fg-muted"><Spinner /> Calcul…</p>
      ) : (
        <div className="mt-2 space-y-2 text-xs text-fg-muted">
          <p>
            <span className="font-mono tabular-nums text-foreground">
              {cov.n_cited}/{cov.n_exigences}
            </span>{" "}
            exigences déjà citées dans les réponses.
          </p>
          <div className="grid gap-1.5 sm:grid-cols-2">
            {cov.domains.map((d) => (
              <div key={d.domaine} className="flex items-center gap-2">
                <span className="w-40 truncate" title={d.domaine}>{d.domaine}</span>
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                  <div className="h-full rounded-full bg-accent"
                       style={{ width: `${(100 * d.cited) / (d.total || 1)}%` }} />
                </div>
                <span className="font-mono text-[10px] tabular-nums text-fg-faint">
                  {d.cited}/{d.total}
                </span>
              </div>
            ))}
          </div>
          {cov.n_never > 0 && (
            <p className="leading-relaxed">
              Jamais citées ({cov.n_never}) :{" "}
              {cov.never_cited.map((r, i) => (
                <span key={r.id}>
                  {i > 0 && ", "}
                  {lynxNav ? (
                    <button onClick={() => lynxNav.openRequirement(r.id)}
                            title={`${r.domaine} · L${r.niveau} — ouvrir dans la Matrice`}
                            className="req-link">{r.id}</button>
                  ) : r.id}
                </span>
              ))}
              {cov.n_never > cov.never_cited.length && "…"}
            </p>
          )}
        </div>
      )}
    </details>
  );
}

const SCOPE_HINT =
  "Ce chat ne consulte QUE la baseline d'exigences chargée dans l'arbre " +
  "(onglet Matrice). Les documents de l'Outil RAG n'entrent jamais dans ses réponses.";

const STATIC_EXAMPLES = [
  "Quelles exigences couvrent la cybersécurité ?",
  "Y a-t-il des exigences redondantes sur l'alimentation ?",
  "Résume les exigences de niveau L1 et leurs dérivées.",
];

function scopeFor(status: LynxChatStatus, examples: string[]): ChatScope {
  return {
    source: status.source,
    label: `Baseline d'exigences · ${status.n_exigences} exigence${status.n_exigences > 1 ? "s" : ""}`,
    hint: SCOPE_HINT,
    emptyTitle: "Posez une question sur la baseline d'exigences",
    emptyText:
      "Réponses sourcées citant les exigences de l'arbre — rien d'autre. " +
      "Tout reste sur cette machine.",
    // Suggestions VIVANTES : questions HyPE générées depuis la baseline
    // elle-même (repli statique si HyPE coupé ou index pas encore prêt).
    examples: examples.length ? examples : STATIC_EXAMPLES,
  };
}

export function LynxChat({ prefill }: {
  /** Question pré-remplie (pont Matrice -> Chat) — identité d'objet = déclencheur. */
  prefill?: { text: string } | null;
} = {}) {
  const [status, setStatus] = useState<LynxChatStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [examples, setExamples] = useState<string[]>([]);
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

  // Suggestions vivantes, une fois par montage (nouveau tirage à chaque
  // visite de l'onglet — la variété fait partie du geste).
  useEffect(() => {
    getJSON<{ examples: string[] }>("/api/lynx/chat/examples")
      .then((d) => setExamples(d.examples ?? []))
      .catch(() => setExamples([]));
  }, []);

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
              {status.diff && (
                <span
                  className="ml-1.5 font-mono tabular-nums"
                  title={[
                    status.diff.n_added ? `Ajoutées : ${status.diff.added.join(", ")}` : "",
                    status.diff.n_changed ? `Modifiées : ${status.diff.changed.join(", ")}` : "",
                    status.diff.n_removed ? `Supprimées : ${status.diff.removed.join(", ")}` : "",
                  ].filter(Boolean).join("\n")}
                >
                  {[status.diff.n_added ? `+${status.diff.n_added}` : "",
                    status.diff.n_changed ? `~${status.diff.n_changed}` : "",
                    status.diff.n_removed ? `−${status.diff.n_removed}` : "",
                  ].filter(Boolean).join(" ")}
                </span>
              )}
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

      {ready && <CoveragePanel />}

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
        <Chat scope={scopeFor(status, examples)} prefill={prefill} />
      )}
    </div>
  );
}
