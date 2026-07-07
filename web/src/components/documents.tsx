"use client";

/** Vue Documents : dépôt d'un lot (options choisies AU MOMENT de l'upload,
 * règle produit), file d'ingestion séquentielle en direct, documents indexés
 * (exploration des passages = boîte de verre de l'indexation, suppression). */

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

import { API_BASE, getJSON, type SourcesResponse } from "@/lib/api";
import { Banner, Dot, Hint, Spinner, type Tone } from "@/components/ui";

type Job = {
  id: number;
  name: string;
  status: "queued" | "running" | "success" | "error";
  pct: number;
  step: string;
  elapsed: number | null;
  num_chunks: number | null;
  message: string | null;
};

type IngestDefaults = {
  params: { nkw: number; nq: number; mode: string; raptor: boolean; enh_model: string };
  upload_types: string[];
};

type ChunkRow = {
  content: string;
  heading?: string | null;
  breadcrumb?: string | null;
  section_idx?: number | null;
  page_number?: number | null;
  chunk_type?: string | null;
  keywords_str?: string | null;
  questions_str?: string | null;
  entities_str?: string | null;
};

const JOB_TONE: Record<Job["status"], Tone> = {
  queued: "neutral",
  running: "accent",
  success: "good",
  error: "bad",
};
const JOB_LABEL: Record<Job["status"], string> = {
  queued: "En file",
  running: "En cours",
  success: "Indexé",
  error: "Échec",
};

function JobBar({ j }: { j: Job }) {
  const pct = j.status === "queued" ? 0 : j.status === "running" ? j.pct : 100;
  return (
    <div className="rounded-lg border border-edge bg-surface-2 px-3 py-2">
      <div className="mb-1 flex items-baseline justify-between gap-2 text-xs">
        <span className="flex items-center gap-2 text-fg-muted">
          <Dot tone={JOB_TONE[j.status]} pulse={j.status === "running"} />
          {j.name}
          <span className="text-fg-faint">— {JOB_LABEL[j.status]}</span>
        </span>
        <span className="font-mono tabular-nums text-fg-faint">
          {j.status === "running" ? `${j.pct} %` : ""}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className={`h-full rounded-full transition-[width] duration-500 ease-out ${
            j.status === "error" ? "bg-bad" : j.status === "success" ? "bg-good" : "bg-accent"
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="mt-1 text-[11px] text-fg-faint">
        {j.status === "running" && `${j.step}${j.elapsed ? ` — ${j.elapsed}s écoulées` : ""}`}
        {j.status === "success" &&
          `${j.num_chunks ?? "?"} passages indexés${j.elapsed ? ` en ${j.elapsed}s` : ""}`}
        {j.status === "error" && (j.message ?? "Erreur d'ingestion.")}
        {j.status === "queued" && "En file d'attente…"}
      </p>
    </div>
  );
}

/** Exploration des passages d'un document (repliée par défaut). */
function DocExplorer({ name, onView }: { name: string; onView: (content: string) => void }) {
  const [search, setSearch] = useState("");
  const [type, setType] = useState("tous");
  const [rows, setRows] = useState<ChunkRow[] | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const p = new URLSearchParams({ search, chunk_type: type });
      const r = await getJSON<{ total: number; chunks: ChunkRow[] }>(
        `/api/documents/${encodeURIComponent(name)}/chunks?${p}`,
      );
      setRows(r.chunks);
    } catch {
      setRows([]);
    }
    setLoading(false);
  }, [name, search, type]);

  return (
    <details className="chat-details" onToggle={(e) => e.currentTarget.open && !rows && load()}>
      <summary>
        Explorer les passages
        <Hint text="Le contenu exact que l'indexation a mis en base pour ce document." />
      </summary>
      <div className="mt-2 flex gap-2">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && load()}
          placeholder="Rechercher dans le texte…"
          className="flex-1 rounded-lg border border-edge bg-surface px-3 py-1.5 text-xs text-foreground placeholder:text-fg-faint focus:border-accent focus:outline-none"
        />
        <select
          value={type}
          onChange={(e) => setType(e.target.value)}
          className="rounded-lg border border-edge bg-surface px-2 py-1.5 text-xs text-foreground focus:outline-none"
        >
          <option value="tous">Tous</option>
          <option value="texte">Texte</option>
          <option value="tabfig">Tableaux / figures</option>
          <option value="resumes">Résumés</option>
        </select>
        <button
          onClick={load}
          className="cursor-pointer rounded-lg border border-edge bg-surface px-3 py-1.5 text-xs text-fg-muted transition-colors hover:border-accent/60 hover:text-foreground"
        >
          Filtrer
        </button>
      </div>
      <div className="mt-2 space-y-2">
        {loading && (
          <p className="flex items-center gap-2 text-xs text-fg-muted">
            <Spinner /> Chargement…
          </p>
        )}
        {rows && !loading && (
          <>
            <p className="text-[11px] text-fg-faint">{rows.length} passage(s)</p>
            {rows.map((c, i) => {
              const tag =
                { summary: "Résumé — ", table: "Tableau — ", figure: "Figure — ",
                  mixed: "Tab+Fig — " }[c.chunk_type ?? ""] ?? "";
              const loc = c.heading ?? c.breadcrumb ?? `Section ${c.section_idx ?? "?"}`;
              return (
                <details key={i} className="chat-details">
                  <summary className="text-xs">
                    {tag}
                    {loc.slice(0, 70)}
                    {c.page_number ? ` — p. ${c.page_number}` : ""}
                  </summary>
                  <div className="mt-2 max-h-64 overflow-y-auto whitespace-pre-wrap text-xs leading-relaxed text-fg-muted">
                    {c.content}
                  </div>
                  <button
                    onClick={() => onView(c.content)}
                    title="Voir ce passage surligné dans le document entier."
                    className="mt-2 cursor-pointer rounded-md border border-edge px-2 py-0.5 text-[11px] text-fg-faint transition-colors hover:border-accent/60 hover:text-foreground"
                  >
                    Voir dans le document
                  </button>
                  {(c.keywords_str || c.questions_str || c.entities_str) && (
                    <p className="mt-2 border-t border-edge pt-2 text-[11px] text-fg-faint">
                      {c.keywords_str && <>Mots-clés — {c.keywords_str}<br /></>}
                      {c.questions_str && <>Questions — {c.questions_str}<br /></>}
                      {c.entities_str && <>Entités — {c.entities_str}</>}
                    </p>
                  )}
                </details>
              );
            })}
          </>
        )}
      </div>
    </details>
  );
}

/** Résumé global du document (réutilise les résumés de section RAPTOR). */
function DocSummary({ name }: { name: string }) {
  const [state, setState] = useState<null | "loading" | { summary?: string; status?: string;
                                                          n?: number; basis?: string }>(null);
  return (
    <div className="mt-2">
      {!state && (
        <button
          onClick={async () => {
            setState("loading");
            const res = await fetch(`${API_BASE}/api/documents/${encodeURIComponent(name)}/summary`,
                                    { method: "POST" }).catch(() => null);
            setState(res?.ok ? await res.json() : { status: "error" });
          }}
          title="Résumé global généré à partir des résumés de section."
          className="cursor-pointer rounded-md border border-edge px-2 py-1 text-xs text-fg-faint transition-colors hover:border-accent/60 hover:text-foreground"
        >
          Résumer ce document
        </button>
      )}
      {state === "loading" && (
        <p className="flex items-center gap-2 text-xs text-fg-muted"><Spinner /> Résumé…</p>
      )}
      {state && state !== "loading" && (
        state.status === "success" ? (
          <div className="rounded-lg border border-edge bg-surface-2 px-3 py-2">
            <p className="text-[11px] uppercase tracking-[0.14em] text-fg-faint">
              Résumé — basé sur {state.n} {state.basis}
            </p>
            <p className="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-fg-muted">
              {state.summary}
            </p>
          </div>
        ) : (
          <p className="text-xs text-fg-faint">
            {state.status === "empty" ? "Matériau insuffisant pour résumer ce document."
              : "Échec du résumé."}
          </p>
        )
      )}
    </div>
  );
}

/** Visionneuse « page blanche » : le document entier, avec surlignage d'un
 * passage. Rendue dans une surcouche pour rester lisible (fond clair). */
function DocViewer({ name, highlight, onClose }: {
  name: string; highlight: string | null; onClose: () => void;
}) {
  const [md, setMd] = useState<string | null>(null);
  useEffect(() => {
    getJSON<{ markdown: string }>(`/api/documents/${encodeURIComponent(name)}/markdown`)
      .then((d) => setMd(d.markdown))
      .catch(() => setMd("_Markdown source introuvable._"));
  }, [name]);
  useEffect(() => {
    if (md && highlight) {
      const t = setTimeout(() =>
        document.getElementById("doc-hl")?.scrollIntoView({ block: "center" }), 120);
      return () => clearTimeout(t);
    }
  }, [md, highlight]);

  // Découpe autour du passage surligné (correspondance exacte des 120 premiers chars).
  const probe = (highlight ?? "").replace(/^\[[^\]]*\]\s*/, "").slice(0, 120);
  const idx = md && probe.length > 20 ? md.indexOf(probe) : -1;

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-black/60 p-4 backdrop-blur-sm sm:p-8"
         onClick={onClose} role="presentation">
      <div className="mx-auto flex h-full w-full max-w-4xl flex-col overflow-hidden rounded-xl bg-(--doc-bg) shadow-2xl"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-black/10 px-5 py-2.5">
          <p className="text-sm font-medium text-neutral-800">{name}</p>
          <button onClick={onClose} aria-label="Fermer"
                  className="cursor-pointer rounded-md px-2 py-1 text-sm text-neutral-500 hover:bg-black/5 hover:text-neutral-900">
            ✕
          </button>
        </div>
        <div className="doc-page min-h-0 flex-1 overflow-y-auto bg-white px-8 py-6 sm:px-12">
          {md === null ? (
            <p className="text-sm text-neutral-500">Chargement…</p>
          ) : idx >= 0 ? (
            <>
              <ReactMarkdown>{md.slice(0, idx)}</ReactMarkdown>
              <div id="doc-hl" className="doc-highlight">
                <ReactMarkdown>{md.slice(idx, idx + probe.length)}</ReactMarkdown>
              </div>
              <ReactMarkdown>{md.slice(idx + probe.length)}</ReactMarkdown>
            </>
          ) : (
            <ReactMarkdown>{md}</ReactMarkdown>
          )}
        </div>
      </div>
    </div>
  );
}

function DocRow({ d, onDeleted }: { d: { name: string; chunks: number }; onDeleted: () => void }) {
  const [confirm, setConfirm] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [viewer, setViewer] = useState<null | { highlight: string | null }>(null);
  return (
    <div className="rounded-xl border border-edge bg-surface px-4 py-3">
      {viewer && (
        <DocViewer name={d.name} highlight={viewer.highlight} onClose={() => setViewer(null)} />
      )}
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-foreground">
          {d.name} <span className="text-xs text-fg-faint">· {d.chunks} passages</span>
        </p>
        {!confirm && (
          <button
            onClick={() => setViewer({ highlight: null })}
            title="Ouvrir le document entier (page blanche)."
            className="cursor-pointer rounded-md border border-edge px-2 py-1 text-xs text-fg-faint transition-colors hover:border-accent/60 hover:text-foreground"
          >
            Ouvrir
          </button>
        )}
        {confirm ? (
          <span className="flex items-center gap-2 text-xs">
            <span className="text-fg-muted">Supprimer de l&apos;index ?</span>
            <button
              disabled={deleting}
              onClick={async () => {
                setDeleting(true);
                await fetch(`${API_BASE}/api/documents/${encodeURIComponent(d.name)}`, {
                  method: "DELETE",
                }).catch(() => null);
                onDeleted();
              }}
              className="cursor-pointer rounded-md bg-bad/20 px-2 py-1 text-bad transition-colors hover:bg-bad/30"
            >
              {deleting ? "…" : "Confirmer"}
            </button>
            <button
              onClick={() => setConfirm(false)}
              className="cursor-pointer rounded-md border border-edge px-2 py-1 text-fg-muted"
            >
              Annuler
            </button>
          </span>
        ) : (
          <button
            onClick={() => setConfirm(true)}
            title="Supprime les passages de ce document du store. Ré-ingérer pour reconstruire l'index complet."
            className="cursor-pointer rounded-md border border-edge px-2 py-1 text-xs text-fg-faint transition-colors hover:border-bad/50 hover:text-bad"
          >
            Supprimer
          </button>
        )}
      </div>
      <DocSummary name={d.name} />
      <DocExplorer name={d.name} onView={(content) => setViewer({ highlight: content })} />
    </div>
  );
}

export function Documents() {
  const [defaults, setDefaults] = useState<IngestDefaults | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [params, setParams] = useState({ nkw: 0, nq: 0, mode: "technical",
                                         raptor: true, enh_model: "" });
  const [jobs, setJobs] = useState<Job[]>([]);
  const [active, setActive] = useState(false);
  const [docs, setDocs] = useState<{ name: string; chunks: number }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const refreshDocs = useCallback(() => {
    getJSON<SourcesResponse>("/api/sources")
      .then((s) => setDocs(s.sources))
      .catch(() => setDocs([]));
  }, []);

  const refreshStatus = useCallback(async () => {
    try {
      const s = await getJSON<{ active: boolean; jobs: Job[] }>("/api/ingest/status");
      setJobs(s.jobs);
      setActive(s.active);
      setError(null);
    } catch {
      setError("API hors ligne — lancer python serve.py --web");
    }
  }, []);

  useEffect(() => {
    getJSON<IngestDefaults>("/api/ingest/defaults")
      .then((d) => {
        setDefaults(d);
        setParams(d.params);
      })
      .catch(() => setError("API hors ligne — lancer python serve.py --web"));
    // Chargement initial différé d'un tick : uniquement des setState post-await,
    // mais la règle set-state-in-effect ne sait pas l'analyser.
    const t = setTimeout(() => {
      refreshStatus();
      refreshDocs();
    }, 0);
    return () => clearTimeout(t);
  }, [refreshStatus, refreshDocs]);

  // La file se rafraîchit toutes les 1,5 s tant qu'un job est actif.
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => {
      refreshStatus();
      refreshDocs();
    }, 1500);
    return () => clearInterval(t);
  }, [active, refreshStatus, refreshDocs]);

  const submit = async () => {
    if (!files.length || submitting) return;
    setSubmitting(true);
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    fd.append("nkw", String(params.nkw));
    fd.append("nq", String(params.nq));
    fd.append("mode", params.mode);
    fd.append("raptor", String(params.raptor));
    fd.append("enh_model", params.enh_model);
    try {
      const res = await fetch(`${API_BASE}/api/documents`, { method: "POST", body: fd });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setFiles([]);
      if (fileRef.current) fileRef.current.value = "";
      await refreshStatus();
    } catch (e) {
      setError(`Dépôt impossible : ${String(e)}`);
    }
    setSubmitting(false);
  };

  const accept = defaults?.upload_types.map((t) => `.${t}`).join(",") ?? ".pdf,.md";
  const finished = jobs.some((j) => j.status === "success" || j.status === "error");

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      {error && <Banner tone="bad">{error}</Banner>}

      {/* Dépôt d'un lot. */}
      <section>
        <h3 className="text-sm font-semibold text-foreground">Ajouter des documents</h3>
        <p className="mt-1 text-xs text-fg-muted">
          PDF, Word, PowerPoint, HTML ou Markdown. Plusieurs fichiers acceptés : indexés
          l&apos;un après l&apos;autre en arrière-plan — le chat reste disponible.
        </p>
        <input
          ref={fileRef}
          type="file"
          multiple
          accept={accept}
          onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
          className="mt-3 block w-full cursor-pointer rounded-lg border border-edge bg-surface-2 px-3 py-2 text-xs text-fg-muted file:mr-3 file:cursor-pointer file:rounded-md file:border-0 file:bg-accent file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-background"
        />
        <details className="chat-details">
          <summary>
            Options avancées (facultatif)
            <Hint text="Choisies au moment de l'upload et partagées par tout le lot. Par défaut : les réglages du .env." />
          </summary>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <label className="text-xs text-fg-muted">
              Découpage du texte{" "}
              <Hint text="technical : suit la hiérarchie normative (ANSSI/CC). naive : découpe par titres." />
              <select
                value={params.mode}
                onChange={(e) => setParams({ ...params, mode: e.target.value })}
                className="mt-1 w-full rounded-lg border border-edge bg-surface px-2 py-1.5 text-xs text-foreground"
              >
                <option value="technical">technical</option>
                <option value="naive">naive</option>
              </select>
            </label>
            <label className="flex items-end gap-2 pb-1.5 text-xs text-fg-muted">
              <input
                type="checkbox"
                checked={params.raptor}
                onChange={(e) => setParams({ ...params, raptor: e.target.checked })}
                className="accent-(--accent)"
              />
              Résumés par section (RAPTOR)
              <Hint text="Ajoute des appels LLM à l'ingestion : meilleure couverture des questions générales, indexation plus lente." />
            </label>
            <label className="text-xs text-fg-muted">
              Mots-clés / passage
              <input
                type="number"
                min={0}
                max={10}
                value={params.nkw}
                onChange={(e) => setParams({ ...params, nkw: Number(e.target.value) })}
                className="mt-1 w-full rounded-lg border border-edge bg-surface px-2 py-1.5 text-xs text-foreground"
              />
            </label>
            <label className="text-xs text-fg-muted">
              Questions / passage
              <input
                type="number"
                min={0}
                max={10}
                value={params.nq}
                onChange={(e) => setParams({ ...params, nq: Number(e.target.value) })}
                className="mt-1 w-full rounded-lg border border-edge bg-surface px-2 py-1.5 text-xs text-foreground"
              />
            </label>
            <label className="text-xs text-fg-muted sm:col-span-2">
              Modèle d&apos;enrichissement (vide = défaut){" "}
              <Hint text="L'enrichissement (mots-clés / questions / résumés) est le maillon le plus lent (~85 % du temps). Un modèle léger comme llama3.2:3b accélère nettement." />
              <input
                value={params.enh_model}
                onChange={(e) => setParams({ ...params, enh_model: e.target.value })}
                placeholder="ex : llama3.2:3b"
                className="mt-1 w-full rounded-lg border border-edge bg-surface px-2 py-1.5 text-xs text-foreground placeholder:text-fg-faint"
              />
            </label>
          </div>
        </details>
        <button
          onClick={submit}
          disabled={!files.length || submitting}
          className="mt-3 cursor-pointer rounded-xl bg-accent px-4 py-2 text-sm font-medium text-background transition-colors hover:bg-accent-bright disabled:cursor-default disabled:opacity-40"
        >
          {submitting
            ? "Dépôt…"
            : files.length
              ? `Ajouter (${files.length} document${files.length > 1 ? "s" : ""})`
              : "Ajouter les documents"}
        </button>
      </section>

      {/* File d'ingestion. */}
      {jobs.length > 0 && (
        <section>
          <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
            File d&apos;ingestion
            {active && <Spinner />}
          </h3>
          <div className="mt-2 space-y-2">
            {jobs.map((j) => (
              <JobBar key={j.id} j={j} />
            ))}
          </div>
          {finished && (
            <button
              onClick={async () => {
                await fetch(`${API_BASE}/api/ingest/clear`, { method: "POST" }).catch(() => null);
                refreshStatus();
              }}
              className="mt-2 cursor-pointer rounded-md border border-edge px-2 py-1 text-xs text-fg-faint transition-colors hover:text-foreground"
            >
              Effacer les terminés
            </button>
          )}
        </section>
      )}

      {/* Documents indexés. */}
      <section>
        <h3 className="text-sm font-semibold text-foreground">
          Documents indexés{" "}
          <span className="text-xs font-normal text-fg-faint">· {docs.length}</span>
        </h3>
        <div className="mt-2 space-y-2">
          {docs.length === 0 && (
            <p className="text-xs text-fg-muted">
              Aucun document en base. Déposez-en un ci-dessus.
            </p>
          )}
          {docs.map((d) => (
            <DocRow key={d.name} d={d} onDeleted={refreshDocs} />
          ))}
        </div>
      </section>
    </div>
  );
}
