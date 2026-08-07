"use client";

/** Suivi des runs multi-agents (onglet « Suivi ») : historique des analyses
 * et audits avec, pour chacun, les métriques de pilotage (agents, appels LLM,
 * durée) et le CHEMIN DE PENSÉE complet (échanges LLM par agent, boîte de
 * verre) — le pendant LynX du Suivi technique du monde RAG. */

import { useEffect, useState } from "react";

import { getJSON } from "@/lib/api";
import { Banner, Spinner } from "@/components/ui";
import { GlassBox, type Exchange } from "@/components/requirements/blocks";

type RunSummary = {
  run_id: string; kind: string; t: number;
  action?: string; verdict?: string | null; score?: number; n?: number;
  agents_done?: number; llm_calls?: number; wall_s?: number;
};

function RunRow({ run }: { run: RunSummary }) {
  const [exchanges, setExchanges] = useState<Exchange[] | null>(null);
  const [loading, setLoading] = useState(false);

  const load = () => {
    if (exchanges || loading) return;
    setLoading(true);
    getJSON<{ exchanges: Exchange[] }>(`/api/lynx/runs/${run.run_id}`)
      .then((d) => setExchanges(d.exchanges ?? []))
      .catch(() => setExchanges([]))
      .finally(() => setLoading(false));
  };

  const when = new Date(run.t * 1000).toLocaleString("fr-FR");
  const what = run.kind === "audit"
    ? `Audit — score ${run.score ?? "?"}/100 (${run.n ?? "?"} exigences)`
    : `Analyse — ${run.action ?? "?"}${run.verdict ? ` → ${run.verdict}` : ""}`;

  return (
    <details className="chat-details !mt-0 bg-surface"
             onToggle={(e) => { if ((e.target as HTMLDetailsElement).open) load(); }}>
      <summary className="!text-xs">
        <span className="text-fg-faint">{when}</span>
        <span className="min-w-0 flex-1 truncate text-foreground">{what}</span>
        <span className="font-mono text-[10px] tabular-nums text-fg-faint">
          {run.agents_done != null && `${run.agents_done} agents · `}
          {run.llm_calls ?? 0} LLM · {run.wall_s ?? "?"} s
        </span>
      </summary>
      <div className="mt-2">
        {loading && <p className="flex items-center gap-2 text-xs text-fg-muted"><Spinner /> Chargement…</p>}
        {exchanges && (exchanges.length
          ? <GlassBox exchanges={exchanges} title="Chemin de pensée" />
          : <p className="text-xs text-fg-faint">Aucun échange LLM (agents déterministes seuls).</p>)}
      </div>
    </details>
  );
}

export function LynxRuns({ active = true }: { active?: boolean } = {}) {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [available, setAvailable] = useState(true);

  useEffect(() => {
    if (!active) return;
    const t = setTimeout(() => {
      getJSON<{ available: boolean; runs: RunSummary[] }>("/api/lynx/runs")
        .then((d) => { setRuns(d.runs); setAvailable(d.available); })
        .catch(() => { setRuns([]); setAvailable(false); });
    }, 0);
    return () => clearTimeout(t);
  }, [active]);

  if (!runs)
    return <p className="mt-4 flex items-center gap-2 text-xs text-fg-muted"><Spinner /> Chargement…</p>;
  return (
    <div className="mt-4 space-y-2">
      <p className="text-xs text-fg-muted">
        Chaque analyse et chaque audit laissent une trace : métriques de
        pilotage (complétude des agents, appels LLM, durée) et chemin de pensée
        complet. Les 200 derniers runs sont conservés.
      </p>
      {!available && <Banner tone="warn">MongoDB injoignable — historique indisponible.</Banner>}
      {runs.length === 0
        ? <Banner tone="neutral">Aucun run pour l&apos;instant : lancez une analyse ou un audit depuis la Matrice.</Banner>
        : <div className="space-y-1.5">{runs.map((r) => <RunRow key={r.run_id} run={r} />)}</div>}
    </div>
  );
}
