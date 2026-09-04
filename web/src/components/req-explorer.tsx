"use client";

import { memo, useEffect, useMemo, useState } from "react";

import { couleurNiveau } from "@/components/req-levels";
import { Spinner } from "@/components/ui";
import { getJSON } from "@/lib/api";

export type Req = {
  id: string;
  niveau: number;
  type?: string;
  domaine?: string;
  texte?: string;
  parent_id?: string | null;
  test_status?: string;
  verification?: string | null;
  source?: string | null;
  rationale?: string | null;
  criticite?: string | null;
  version?: string | null;
  root_declared?: boolean;
  occurrences?: { source?: string; document_type?: string; section?: string | null; section_title?: string | null; offset?: number; row?: number; column?: number }[];
  arbitration?: { decided_at?: number; decided_by?: string; rationale?: string; selected_source?: string };
  links?: { type: string; target: string }[];
};

export type ReqIssue = { code: string; priority: "critical" | "high" | "medium" | "low"; label: string; action: string };
export type ReqQueueItem = { req_id: string; priority: ReqIssue["priority"]; issues: ReqIssue[]; source?: string | null; niveau: number };

type View = "all" | "attention" | "roots" | "impacted";
type Facets = {
  total: number;
  levels: Record<string, number>;
  sources: Record<string, number>;
  domains: Record<string, number>;
  views: { attention: number; roots: number };
};
type Page = { total: number; page: number; page_size: number; items: Req[] };

const PAGE_SIZE = 75;

/** Catalogue paginé : aucune baseline complète n'est transférée au navigateur. */
export const ReqExplorer = memo(function ReqExplorer({ selected, onSelect, impactedIds = [], queueItems = [], refreshToken = 0, auditStates = {} }: {
  selected: string | null;
  onSelect: (id: string) => void;
  impactedIds?: string[];
  queueItems?: ReqQueueItem[];
  refreshToken?: number;
  auditStates?: Record<string, "audited" | "flagged" | "not_audited">;
}) {
  const [view, setView] = useState<View>("all");
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [level, setLevel] = useState("");
  const [domain, setDomain] = useState("");
  const [source, setSource] = useState("");
  const [page, setPage] = useState(1);
  const [rows, setRows] = useState<Req[]>([]);
  const [total, setTotal] = useState(0);
  const [facets, setFacets] = useState<Facets | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => { setDebouncedQuery(query.trim()); setPage(1); }, 220);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    let cancelled = false;
    getJSON<Facets>("/api/lynx/requirements/facets")
      .then((result) => { if (!cancelled) setFacets(result); })
      .catch(() => { if (!cancelled) setLoadError(true); });
    return () => { cancelled = true; };
  }, [refreshToken]);

  useEffect(() => {
    let cancelled = false;
    const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
    if (debouncedQuery) params.set("query", debouncedQuery);
    if (level) params.set("level", level);
    if (domain) params.set("domain", domain);
    if (source) params.set("source", source);
    if (view !== "all") params.set("status", view);
    const timer = setTimeout(() => {
      setLoading(true);
      setLoadError(false);
      getJSON<Page>("/api/lynx/requirements?" + params.toString())
        .then((result) => {
          if (cancelled) return;
          setRows(result.items);
          setTotal(result.total);
        })
        .catch(() => { if (!cancelled) setLoadError(true); })
        .finally(() => { if (!cancelled) setLoading(false); });
    }, 0);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [debouncedQuery, domain, level, page, refreshToken, source, view]);

  const queueById = useMemo(() => new Map(queueItems.map((item) => [item.req_id, item])), [queueItems]);
  const maxLevel = Math.max(1, ...Object.keys(facets?.levels ?? {}).map(Number));
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const changeView = (next: View) => { setView(next); setPage(1); };
  const counts = {
    all: facets?.total ?? total,
    attention: facets?.views.attention ?? queueItems.length,
    roots: facets?.views.roots ?? 0,
    impacted: impactedIds.length,
  };

  return (
    <div className="flex h-[calc(100vh-17rem)] min-h-[620px] flex-col overflow-hidden rounded-xl border border-edge bg-surface">
      <nav className="flex gap-1 overflow-x-auto border-b border-edge px-3 pt-3" aria-label="Vues des exigences">
        <ViewButton label="Toutes" count={counts.all} active={view === "all"} onClick={() => changeView("all")} />
        <ViewButton label="Anomalies structurelles" count={counts.attention} active={view === "attention"} onClick={() => changeView("attention")} />
        <ViewButton label="Racines" count={counts.roots} active={view === "roots"} onClick={() => changeView("roots")} />
        <ViewButton label="Impactées" count={counts.impacted} active={view === "impacted"} onClick={() => changeView("impacted")} />
      </nav>
      <div className="flex flex-wrap items-center gap-2 border-b border-edge p-3">
        <input value={query} onChange={(event) => setQuery(event.target.value)}
               placeholder="Identifiant ou contenu…" aria-label="Rechercher une exigence"
               className="min-w-52 flex-1 rounded-lg border border-edge bg-surface-2 px-3 py-1.5 text-xs text-foreground focus:border-accent focus:outline-none" />
        <select value={level} onChange={(event) => { setLevel(event.target.value); setPage(1); }}
                aria-label="Filtrer par niveau"
                className="rounded-lg border border-edge bg-surface-2 px-2 py-1.5 text-xs text-fg-muted">
          <option value="">Tous niveaux</option>
          {Object.keys(facets?.levels ?? {}).sort((a, b) => Number(a) - Number(b)).map((value) => <option key={value} value={value}>L{value}</option>)}
        </select>
        <select value={domain} onChange={(event) => { setDomain(event.target.value); setPage(1); }}
                aria-label="Filtrer par domaine"
                className="max-w-40 rounded-lg border border-edge bg-surface-2 px-2 py-1.5 text-xs text-fg-muted">
          <option value="">Tous domaines</option>
          {Object.keys(facets?.domains ?? {}).map((value) => <option key={value}>{value}</option>)}
        </select>
        <select value={source} onChange={(event) => { setSource(event.target.value); setPage(1); }}
                aria-label="Filtrer par source"
                className="max-w-44 rounded-lg border border-edge bg-surface-2 px-2 py-1.5 text-xs text-fg-muted">
          <option value="">Toutes sources</option>
          {Object.keys(facets?.sources ?? {}).map((value) => <option key={value}>{value}</option>)}
        </select>
        <span className="w-24 text-right font-mono text-[11px] text-fg-faint">{total} résultat(s)</span>
      </div>
      <div className="grid grid-cols-[10px_9rem_3rem_7rem_6rem_1fr] gap-2 border-b border-edge bg-surface-2 px-3 py-1.5 text-[9px] font-medium uppercase tracking-[0.12em] text-fg-faint">
        <span /> <span>Identifiant</span> <span>Niv.</span> <span>Source</span> <span>État</span> <span>Exigence</span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto" role="listbox" aria-busy={loading}>
        {loading && <p className="flex items-center justify-center gap-2 p-8 text-xs text-fg-faint"><Spinner /> Chargement…</p>}
        {!loading && rows.map((req) => (
          <button key={req.id} role="option" aria-selected={selected === req.id} onClick={() => onSelect(req.id)}
                  className={`grid min-h-12 w-full cursor-pointer grid-cols-[10px_9rem_3rem_7rem_6rem_1fr] items-start gap-2 border-b border-edge px-3 py-2 text-left ${selected === req.id ? "bg-accent/10" : "hover:bg-surface-2"}`}>
            <span className="mt-1 h-2 w-2 rounded-full" style={{ background: couleurNiveau(req.niveau, maxLevel) }} />
            <span className="font-mono text-[11px] text-foreground">{req.id}</span>
            <span className="text-[10px] text-fg-faint">L{req.niveau}</span>
            <span className="truncate text-[10px] text-fg-faint" title={req.source ?? ""}>{req.source?.split(".")[0] || "—"}</span>
            <RequirementState requirement={req} issue={queueById.get(req.id)?.issues[0]}
                              auditState={auditStates[req.id]} />
            <span className="line-clamp-2 text-xs leading-relaxed text-fg-muted">{req.texte || "(énoncé vide)"}</span>
          </button>
        ))}
        {!loading && !rows.length && <p className="p-8 text-center text-xs text-fg-faint">{loadError ? "Catalogue indisponible." : "Aucune exigence ne correspond aux filtres."}</p>}
      </div>
      <footer className="flex items-center justify-between border-t border-edge px-3 py-2 text-[11px] text-fg-faint">
        <span>{rows.length ? (page - 1) * PAGE_SIZE + 1 : 0}–{Math.min(page * PAGE_SIZE, total)} sur {total}</span>
        <div className="flex items-center gap-2">
          <button onClick={() => setPage((value) => Math.max(1, value - 1))} disabled={page <= 1 || loading} className="rounded border border-edge px-2 py-1 disabled:opacity-40">Précédent</button>
          <span className="font-mono">{page}/{pages}</span>
          <button onClick={() => setPage((value) => Math.min(pages, value + 1))} disabled={page >= pages || loading} className="rounded border border-edge px-2 py-1 disabled:opacity-40">Suivant</button>
        </div>
      </footer>
    </div>
  );
});

function ViewButton({ label, count, active, onClick }: { label: string; count: number; active: boolean; onClick: () => void }) {
  return <button onClick={onClick} className={active ? "whitespace-nowrap border-b-2 border-accent px-3 py-2 text-[11px] text-foreground" : "whitespace-nowrap border-b-2 border-transparent px-3 py-2 text-[11px] text-fg-faint hover:text-foreground"}>{label} <span className="ml-1 font-mono text-[10px]">{count}</span></button>;
}

function RequirementState({ requirement, issue, auditState }: {
  requirement: Req;
  issue?: ReqIssue;
  auditState?: "audited" | "flagged" | "not_audited";
}) {
  if (!requirement.texte?.trim()) return <span className="w-fit rounded bg-bad/15 px-1.5 py-0.5 text-[9px] text-bad">énoncé vide</span>;
  if (auditState === "flagged") return <span className="w-fit rounded bg-bad/15 px-1.5 py-0.5 text-[9px] text-bad">audit : constat</span>;
  if (auditState === "audited") return <span className="w-fit rounded bg-good/15 px-1.5 py-0.5 text-[9px] text-good">audit : sans constat</span>;
  if (auditState === "not_audited") return <span className="w-fit rounded bg-muted px-1.5 py-0.5 text-[9px] text-fg-muted">audit incomplet</span>;
  if (issue) return <span className="w-fit rounded bg-warn/15 px-1.5 py-0.5 text-[9px] text-warn" title={issue.label}>contrôle : signalée</span>;
  return <span className="w-fit rounded bg-muted px-1.5 py-0.5 text-[9px] text-fg-muted">non auditée</span>;
}
