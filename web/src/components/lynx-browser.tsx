"use client";

/** Navigateur d'exigences (onglet « Exigences ») : TOUTE la baseline en liste
 * déroulante consultable — lire le contenu des exigences sans cliquer nœud
 * par nœud dans l'arbre. Groupée par domaine, triée niveau puis identifiant,
 * filtrable (texte, domaine, niveau, sans vérification). Chaque fiche se
 * déplie : énoncé complet, attributs, liens — et passe la main à la Matrice
 * ou au Chat via les ponts LynxNav. */

import { useEffect, useMemo, useState } from "react";

import { getJSON } from "@/lib/api";
import { useLynxNav } from "@/components/lynx-nav";
import { Banner, Dot, Hint, Spinner } from "@/components/ui";

type BrowserReq = {
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
  links?: { type: string; target?: string; target_id?: string }[];
};

const STATUS_TONE: Record<string, "good" | "warn" | "bad" | "neutral"> = {
  OK: "good", KO: "bad", PENDING: "neutral",
};

function ReqCard({ req, childrenIds }: { req: BrowserReq; childrenIds: string[] }) {
  const lynxNav = useLynxNav();
  const links = (req.links ?? [])
    .map((l) => ({ type: l.type, cible: l.target_id ?? l.target }))
    .filter((l) => l.cible);
  return (
    <details className="chat-details !mt-0 bg-surface">
      <summary className="!text-[13px]">
        <span className="font-mono text-foreground">{req.id}</span>
        <span className="rounded-full border border-edge px-1.5 py-0.5 text-[10px] text-fg-faint">
          L{req.niveau}
        </span>
        <Dot tone={STATUS_TONE[req.test_status ?? "PENDING"] ?? "neutral"} />
        <span className="min-w-0 flex-1 truncate text-fg-muted">{req.texte}</span>
      </summary>
      <div className="mt-2 space-y-2 pl-1 text-xs">
        <p className="leading-relaxed text-foreground">{req.texte || "(énoncé vide)"}</p>
        <p className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-fg-faint">
          <span>{req.type ?? "Exigence"} · {req.domaine ?? "Général"} · L{req.niveau}</span>
          {req.parent_id && <span>Dérivée de : <span className="font-mono">{req.parent_id}</span></span>}
          {childrenIds.length > 0 && (
            <span>Dérivées : <span className="font-mono">{childrenIds.join(", ")}</span></span>
          )}
          <span>Vérification : {req.verification ?? "—"}</span>
          <span>Statut : {req.test_status ?? "PENDING"}</span>
          {req.source && <span>Origine : {req.source}</span>}
        </p>
        {links.length > 0 && (
          <p className="text-[11px] text-fg-faint">
            Liens : {links.map((l, i) => (
              <span key={i}>{i > 0 && ", "}{l.type} → <span className="font-mono">{l.cible}</span></span>
            ))}
          </p>
        )}
        {req.rationale && (
          <p className="text-[11px] leading-relaxed text-fg-muted">Justification : {req.rationale}</p>
        )}
        {lynxNav && (
          <div className="flex flex-wrap gap-2 pt-1">
            <button onClick={() => lynxNav.openRequirement(req.id)}
                    className="cursor-pointer rounded-md border border-accent/50 px-2 py-1 text-[11px] text-accent-bright transition-colors hover:bg-accent/10">
              Ouvrir dans la Matrice →
            </button>
            <button onClick={() => lynxNav.askAboutRequirement(req.id)}
                    className="cursor-pointer rounded-md border border-edge px-2 py-1 text-[11px] text-fg-muted transition-colors hover:border-accent/50 hover:text-foreground">
              Interroger le chat…
            </button>
          </div>
        )}
      </div>
    </details>
  );
}

export function LynxBrowser({ active = true }: {
  /** Onglet visible : re-charge le corpus à chaque retour (la Matrice a pu
   * appliquer des actions entre-temps — le composant reste monté). */
  active?: boolean;
} = {}) {
  const [corpus, setCorpus] = useState<BrowserReq[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [domaine, setDomaine] = useState("");
  const [niveau, setNiveau] = useState("");
  const [sansVerif, setSansVerif] = useState(false);

  useEffect(() => {
    if (!active) return;
    const t = setTimeout(() => {
      getJSON<{ exigences: BrowserReq[] }>("/api/lynx/corpus")
        .then((d) => { setCorpus(d.exigences); setError(null); })
        .catch(() => setError("API hors ligne — lancer python serve.py"));
    }, 0);
    return () => clearTimeout(t);
  }, [active]);

  const domaines = useMemo(
    () => [...new Set((corpus ?? []).map((r) => r.domaine ?? "Général"))].sort(),
    [corpus]);
  const niveaux = useMemo(
    () => [...new Set((corpus ?? []).map((r) => r.niveau))].sort((a, b) => a - b),
    [corpus]);
  const childrenOf = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const r of corpus ?? []) {
      if (r.parent_id) m.set(r.parent_id, [...(m.get(r.parent_id) ?? []), r.id]);
    }
    return m;
  }, [corpus]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (corpus ?? []).filter((r) =>
      (!q || r.id.toLowerCase().includes(q) || (r.texte ?? "").toLowerCase().includes(q))
      && (!domaine || (r.domaine ?? "Général") === domaine)
      && (niveau === "" || r.niveau === Number(niveau))
      && (!sansVerif || !r.verification));
  }, [corpus, search, domaine, niveau, sansVerif]);

  const grouped = useMemo(() => {
    const m = new Map<string, BrowserReq[]>();
    for (const r of [...filtered].sort((a, b) =>
        (a.domaine ?? "Général").localeCompare(b.domaine ?? "Général")
        || a.niveau - b.niveau || a.id.localeCompare(b.id))) {
      const d = r.domaine ?? "Général";
      m.set(d, [...(m.get(d) ?? []), r]);
    }
    return m;
  }, [filtered]);

  if (error) return <Banner tone="bad">{error}</Banner>;
  if (!corpus)
    return <p className="mt-4 flex items-center gap-2 text-xs text-fg-muted"><Spinner /> Chargement…</p>;

  return (
    <div className="mt-4 space-y-3">
      {/* Barre de filtres : une ligne, tout au même endroit. */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Rechercher (identifiant ou texte)…"
          aria-label="Rechercher une exigence"
          className="w-64 rounded-lg border border-edge bg-surface px-3 py-1.5 text-xs text-foreground placeholder:text-fg-faint focus:border-accent focus:outline-none"
        />
        <select value={domaine} onChange={(e) => setDomaine(e.target.value)}
                aria-label="Filtrer par domaine"
                className="cursor-pointer rounded-lg border border-edge bg-surface px-2 py-1.5 text-[11px] text-fg-muted focus:outline-none">
          <option value="">Tous les domaines</option>
          {domaines.map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
        <select value={niveau} onChange={(e) => setNiveau(e.target.value)}
                aria-label="Filtrer par niveau"
                className="cursor-pointer rounded-lg border border-edge bg-surface px-2 py-1.5 text-[11px] text-fg-muted focus:outline-none">
          <option value="">Tous niveaux</option>
          {niveaux.map((n) => <option key={n} value={n}>L{n}</option>)}
        </select>
        <label className="flex cursor-pointer items-center gap-1.5 text-[11px] text-fg-muted">
          <input type="checkbox" checked={sansVerif}
                 onChange={(e) => setSansVerif(e.target.checked)}
                 className="accent-(--accent)" />
          sans vérification
          <Hint text="Exigences sans méthode de vérification IADT (Inspection, Analyse, Démonstration, Test) — candidates à compléter." />
        </label>
        <span className="ml-auto font-mono text-[11px] tabular-nums text-fg-faint">
          {filtered.length}/{corpus.length}
        </span>
      </div>

      {filtered.length === 0 ? (
        <Banner tone="neutral">Aucune exigence ne correspond aux filtres.</Banner>
      ) : (
        [...grouped.entries()].map(([dom, reqs]) => (
          <section key={dom}>
            <h3 className="mb-1.5 text-[11px] font-medium uppercase tracking-[0.14em] text-fg-faint">
              {dom} <span className="font-mono">({reqs.length})</span>
            </h3>
            <div className="space-y-1.5">
              {reqs.map((r) => (
                <ReqCard key={r.id} req={r} childrenIds={childrenOf.get(r.id) ?? []} />
              ))}
            </div>
          </section>
        ))
      )}
    </div>
  );
}
