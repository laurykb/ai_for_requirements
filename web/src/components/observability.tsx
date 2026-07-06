"use client";

/** Observabilité : performance d'inférence (stats exactes Ollama, VRAM) +
 * traces des requêtes (chaque span chronométré : retrieval, rerank, génération). */

import { useCallback, useEffect, useState } from "react";

import { getJSON } from "@/lib/api";
import { Banner, Spinner } from "@/components/ui";

type PerfRow = { model: string; gen_tokens: number | null; tok_per_s: number | null;
                 ttft_s: number | null; total_s: number | null };
type Perf = {
  gpu: { used: number; total: number }[];
  aggregate: { tok_per_s: number | null; ttft_s: number | null; total_s: number | null;
               count: number } | null;
  rows: PerfRow[];
};
type Span = { name: string; duration_ms: number | null; metadata: Record<string, unknown>;
              children: Span[] };
type Trace = { timestamp: string; duration_ms: number | null; query: string;
               hors_scope: boolean; spans: Span[] };

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-edge bg-surface px-4 py-3">
      <p className="text-[11px] uppercase tracking-[0.14em] text-fg-faint">{label}</p>
      <p className="mt-1 font-mono text-lg tabular-nums text-foreground">{value}</p>
    </div>
  );
}

function SpanRow({ s, total, depth = 0 }: { s: Span; total: number; depth?: number }) {
  const dur = s.duration_ms ?? 0;
  const pct = total ? Math.min(100, (100 * dur) / total) : 0;
  const meta = Object.entries(s.metadata ?? {}).filter(
    ([k]) => !["query", "source", "mode"].includes(k),
  );
  return (
    <>
      <div className="flex items-center gap-2 text-xs" style={{ paddingLeft: depth * 16 }}>
        <code className="font-mono text-fg-muted">{s.name}</code>
        <span className="font-mono tabular-nums text-fg-faint">{dur.toFixed(0)} ms</span>
        <span className="h-1 rounded-full bg-accent/70" style={{ width: `${pct * 1.6}px` }} />
        {meta.length > 0 && (
          <span className="text-[11px] text-fg-faint">
            {meta.map(([k, v]) => `${k}=${String(v)}`).join(" · ")}
          </span>
        )}
      </div>
      {s.children?.map((c, i) => (
        <SpanRow key={i} s={c} total={total} depth={depth + 1} />
      ))}
    </>
  );
}

export function Observability() {
  const [perf, setPerf] = useState<Perf | null>(null);
  const [traces, setTraces] = useState<Trace[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [p, t] = await Promise.all([
        getJSON<Perf>("/api/perf"),
        getJSON<{ available: boolean; traces: Trace[] }>("/api/traces"),
      ]);
      setPerf(p);
      setTraces(t.available ? t.traces : []);
      setError(null);
    } catch {
      setError("API hors ligne — lancer python serve.py --web");
    }
  }, []);

  useEffect(() => {
    const t = setTimeout(load, 0);
    return () => clearTimeout(t);
  }, [load]);

  if (error) return <Banner tone="bad">{error}</Banner>;
  if (!perf || traces === null)
    return (
      <p className="flex items-center gap-2 text-xs text-fg-muted">
        <Spinner /> Chargement…
      </p>
    );

  const durs = traces.map((t) => t.duration_ms ?? 0).sort((a, b) => a - b);
  const med = durs.length ? durs[Math.floor(durs.length / 2)] : 0;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <section>
        <h3 className="text-sm font-semibold text-foreground">Performance d&apos;inférence</h3>
        {perf.gpu.length > 0 && (
          <p className="mt-1 text-xs text-fg-faint">
            {perf.gpu.map((g, i) => `GPU${i} VRAM ${g.used}/${g.total} Mo`).join(" · ")}
          </p>
        )}
        {perf.aggregate ? (
          <>
            <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Stat label="Tokens/s (méd.)" value={String(perf.aggregate.tok_per_s ?? "–")} />
              <Stat label="1er token (méd.)"
                    value={perf.aggregate.ttft_s ? `${perf.aggregate.ttft_s.toFixed(2)} s` : "–"} />
              <Stat label="Latence (méd.)"
                    value={perf.aggregate.total_s ? `${perf.aggregate.total_s.toFixed(1)} s` : "–"} />
              <Stat label="Générations" value={String(perf.aggregate.count)} />
            </div>
            <details className="chat-details">
              <summary>Détail des dernières générations ({perf.rows.length})</summary>
              <table className="mt-2 w-full text-left text-xs text-fg-muted">
                <thead>
                  <tr className="text-[11px] uppercase tracking-wide text-fg-faint">
                    <th className="py-1 pr-2">modèle</th><th className="pr-2">tokens</th>
                    <th className="pr-2">tok/s</th><th className="pr-2">1er token</th>
                    <th>latence</th>
                  </tr>
                </thead>
                <tbody className="font-mono tabular-nums">
                  {perf.rows.map((r, i) => (
                    <tr key={i} className="border-t border-edge">
                      <td className="py-1 pr-2 font-sans">{r.model}</td>
                      <td className="pr-2">{r.gen_tokens ?? "–"}</td>
                      <td className="pr-2">{r.tok_per_s ?? "–"}</td>
                      <td className="pr-2">{r.ttft_s ? `${r.ttft_s} s` : "–"}</td>
                      <td>{r.total_s ? `${r.total_s} s` : "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          </>
        ) : (
          <p className="mt-2 text-xs text-fg-muted">
            Aucune génération mesurée. Posez une question dans l&apos;Outil RAG.
          </p>
        )}
      </section>

      <section>
        <h3 className="text-sm font-semibold text-foreground">Traces des requêtes</h3>
        <p className="mt-1 text-xs text-fg-muted">
          Chaque span chronométré (retrieval, rerank, génération).
        </p>
        {traces.length === 0 ? (
          <p className="mt-2 text-xs text-fg-muted">Aucune trace pour l&apos;instant.</p>
        ) : (
          <>
            <div className="mt-3 grid grid-cols-3 gap-3">
              <Stat label="Requêtes tracées" value={String(traces.length)} />
              <Stat label="Latence médiane" value={`${med.toFixed(0)} ms`} />
              <Stat label="Latence max" value={`${(durs.at(-1) ?? 0).toFixed(0)} ms`} />
            </div>
            <div className="mt-3 space-y-2">
              {traces.map((t, i) => (
                <details key={i} className="chat-details">
                  <summary className="text-xs">
                    {t.timestamp} — {(t.duration_ms ?? 0).toFixed(0)} ms — {t.query.slice(0, 60)}
                    {t.hors_scope ? " — hors-scope" : ""}
                  </summary>
                  <div className="mt-2 space-y-1 overflow-x-auto">
                    {t.spans.map((s, j) => (
                      <SpanRow key={j} s={s} total={t.duration_ms ?? 1} />
                    ))}
                  </div>
                </details>
              ))}
            </div>
          </>
        )}
      </section>
    </div>
  );
}
