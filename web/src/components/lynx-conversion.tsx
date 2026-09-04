"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, getJSON } from "@/lib/api";
import { Banner, Spinner } from "@/components/ui";
import { btnGhost, btnPrimary } from "@/components/requirements/blocks";
import type { Req } from "@/components/req-explorer";

type Health = { score: number; n: number; broken_links_count: number; cycles_count: number;
  roots_count: number; empty_text_count: number;
  by_level: Record<string, number>; by_domain: Record<string, number> };
type Diff = { added: string[]; removed: string[]; modified: string[]; changed: string[]; impacted: string[] };
type ImportedReq = Req & { collision_variants?: { source?: string; texte?: string }[] };
type SourceBatch = { batch_id: string; source_names: string[]; created_at?: number };
type Draft = { draft_id: string; base_revision?: string; source_batches?: SourceBatch[]; exigences: ImportedReq[]; warnings: string[]; health: Health; diff: Diff };
type DraftSummary = { draft_id: string; created_at: number; source_names?: string[]; health?: Health };
type VersionSummary = { version_id: string; created_at: string; reason: string; n: number };

function Metric({ label, value, tone = "" }: { label: string; value: number; tone?: string }) {
  return <div className="rounded-lg border border-edge bg-surface-2 px-3 py-2"><p className={`font-mono text-lg ${tone}`}>{value}</p><p className="text-[10px] text-fg-faint">{label}</p></div>;
}

function RequirementChoice({ req, checked, busy, onToggle, onResolve }: { req: ImportedReq; checked: boolean; busy: boolean; onToggle: () => void; onResolve: (variant: { source?: string; texte?: string }) => void }) {
  const variants = req.collision_variants ?? [];
  return <article className="border-b border-edge p-3 last:border-b-0 hover:bg-surface-2"><div className="flex items-start gap-3"><input id={"include-" + req.id} aria-label={"Retenir " + req.id} type="checkbox" checked={checked} onChange={onToggle} className="mt-0.5 accent-(--accent)" /><div className="min-w-0 flex-1"><label htmlFor={"include-" + req.id} className="cursor-pointer font-mono text-[11px] text-foreground">{req.id}</label><p className="mt-1 text-xs text-fg-muted">{req.texte || "(énoncé vide)"}</p></div></div>{variants.length > 1 && <div className="ml-6 mt-3"><p className="text-[11px] font-medium text-warn">Choisissez la formulation à conserver</p><div className="mt-2 grid gap-2 lg:grid-cols-2">{variants.map((variant, index) => <section key={index} className="flex flex-col rounded-lg border border-warn/30 bg-warn/5 p-3"><p className="font-mono text-[10px] text-fg-faint">Source · {variant.source || "inconnue"}</p><p className="mt-2 flex-1 text-xs leading-relaxed text-fg-muted">{variant.texte || "(énoncé vide)"}</p><button disabled={busy || !variant.texte} onClick={() => onResolve(variant)} className={btnGhost + " mt-3 self-start"}>Choisir cette formulation</button></section>)}</div></div>}</article>;
}

/** Dépôt isolé, contrôle qualité, revue et activation explicite. */
export function LynxConversion({ active = true }: { active?: boolean }) {
  const [activeCorpus, setActiveCorpus] = useState<Req[] | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [included, setIncluded] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [onlyCollisions, setOnlyCollisions] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showActivation, setShowActivation] = useState(false);
  const [approver, setApprover] = useState("Ingénieur RPP");
  const [activationRationale, setActivationRationale] = useState("Revue RPP terminée");
  const [progress, setProgress] = useState<{ pct: number; message: string } | null>(null);
  const [savedDrafts, setSavedDrafts] = useState<DraftSummary[]>([]);
  const [versions, setVersions] = useState<VersionSummary[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);
  const refresh = useCallback(async () => {
    try {
      const [data, draftsData, versionsData] = await Promise.all([
        getJSON<{ exigences: Req[] }>("/api/lynx/corpus"),
        getJSON<{ drafts: DraftSummary[] }>("/api/lynx/corpus/drafts"),
        getJSON<{ versions: VersionSummary[] }>("/api/lynx/corpus/versions"),
      ]);
      setActiveCorpus(data.exigences); setSavedDrafts(draftsData.drafts); setVersions(versionsData.versions);
      setError(null);
    }
    catch { setError("API hors ligne — impossible de charger la baseline."); }
  }, []);
  useEffect(() => { if (!active) return; const timer = setTimeout(refresh, 0); return () => clearTimeout(timer); }, [active, refresh]);

  const upload = async (files: FileList | null) => {
    if (!files?.length) return; setBusy(true); setError(null); setMessage(null);
    setProgress({ pct: 2, message: "Envoi de la matrice…" });
    const body = new FormData(); Array.from(files).forEach((file) => body.append("files", file));
    if (draft) body.append("draft_id", draft.draft_id);
    try {
      const response = await apiFetch(`/api/lynx/corpus/preview/stream`, { method: "POST", body });
      if (!response.ok) {
        let detail = `HTTP ${response.status}`;
        try {
          const payload = await response.json() as { detail?: unknown };
          if (typeof payload.detail === "string") detail = payload.detail;
        } catch { /* réponse non JSON : conserver le statut HTTP */ }
        throw new Error(detail);
      }
      if (!response.body) throw new Error("Réponse de conversion vide.");
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
      for (;;) {
        const { done, value } = await reader.read(); if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n"); buffer = frames.pop() ?? "";
        for (const frame of frames) for (const line of frame.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          const event = JSON.parse(line.slice(6));
          if (event.type === "progress") setProgress({ pct: Number(event.pct), message: String(event.message) });
          else if (event.type === "result") {
            const next = event.draft as Draft; setDraft(next);
            setIncluded(new Set(next.exigences.map((req) => req.id)));
            setMessage("Brouillon créé et sauvegardé. La baseline active n’a pas été modifiée.");
          } else if (event.type === "error") throw new Error(String(event.message));
        }
      }
      await refresh();
    } catch (cause) { setError(`Conversion impossible : ${cause instanceof Error ? cause.message : String(cause)}`); }
    finally { setBusy(false); setProgress(null); if (fileRef.current) fileRef.current.value = ""; }
  };
  const resumeDraft = async (draftId: string) => {
    setBusy(true); setError(null);
    try { const loaded = await getJSON<Draft>(`/api/lynx/corpus/drafts/${draftId}`); setDraft(loaded); setIncluded(new Set(loaded.exigences.map((req) => req.id))); }
    catch (cause) { setError(`Reprise impossible : ${String(cause)}`); }
    finally { setBusy(false); }
  };
  const restoreVersion = async (versionId: string) => {
    if (!confirm("Restaurer cette version ? La baseline actuelle sera sauvegardée auparavant.")) return;
    setBusy(true); setError(null);
    try {
      const response = await apiFetch(`/api/lynx/corpus/versions/${versionId}/restore`, { method: "POST" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const result = await response.json(); setMessage(`Version restaurée : ${result.n} exigences.`); await refresh();
    } catch (cause) { setError(`Restauration impossible : ${String(cause)}`); }
    finally { setBusy(false); }
  };
  const deleteActiveBaseline = async () => {
    if (!activeCorpus?.length || !confirm("Supprimer la baseline active ? Une version restaurable sera créée avant suppression.")) return;
    setBusy(true); setError(null);
    try {
      const response = await apiFetch("/api/lynx/corpus", { method: "DELETE" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      setDraft(null); setIncluded(new Set());
      setMessage("Baseline active supprimée. Vous pouvez importer une nouvelle série sans comparaison avec l’ancienne.");
      await refresh();
    } catch (cause) { setError("Suppression impossible : " + String(cause)); }
    finally { setBusy(false); }
  };
  const discardDraft = async () => {
    if (!draft) return;
    setBusy(true); setError(null);
    try {
      const response = await apiFetch(`/api/lynx/corpus/drafts/${draft.draft_id}`, { method: "DELETE" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setDraft(null); setIncluded(new Set()); setMessage("Brouillon supprimé."); await refresh();
    } catch (cause) { setError(`Suppression impossible : ${String(cause)}`); }
    finally { setBusy(false); }
  };
  const removeSource = async (batchId: string) => {
    if (!draft || !confirm("Supprimer ce dépôt du brouillon ?")) return;
    setBusy(true); setError(null);
    try {
      const response = await apiFetch("/api/lynx/corpus/drafts/" + draft.draft_id + "/sources/" + batchId, { method: "DELETE" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const result = await response.json() as { draft: Draft | null };
      setDraft(result.draft);
      setIncluded(new Set(result.draft?.exigences.map((req) => req.id) ?? []));
      setMessage(result.draft ? "Dépôt supprimé ; la fusion a été recalculée." : "Tous les documents importés ont été supprimés.");
      await refresh();
    } catch (cause) { setError("Suppression impossible : " + String(cause)); }
    finally { setBusy(false); }
  };
  const resolveCollision = async (reqId: string, variant: { source?: string; texte?: string }) => {
    if (!draft || !variant.texte) return;
    setBusy(true); setError(null);
    try {
      const response = await apiFetch(`/api/lynx/corpus/drafts/${draft.draft_id}/collisions/${encodeURIComponent(reqId)}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ texte: variant.texte, source: variant.source ?? null }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setDraft(await response.json() as Draft);
      setMessage(`${reqId} : formulation de ${variant.source || "la source choisie"} retenue.`);
    } catch (cause) { setError(`Arbitrage impossible : ${String(cause)}`); }
    finally { setBusy(false); }
  };
  const toggle = (id: string) => setIncluded((current) => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const selectedReqs = (draft?.exigences ?? []).filter((req) => included.has(req.id));
  const exportBaseline = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify({ exigences: selectedReqs }, null, 2)], { type: "application/json" }));
    const link = document.createElement("a"); link.href = url; link.download = `baseline-${new Date().toISOString().slice(0, 10)}.json`; link.click(); URL.revokeObjectURL(url);
  };
  const activate = async () => {
    if (!draft || !included.size) return;
    setBusy(true); setError(null);
    try {
      const response = await apiFetch(`/api/lynx/corpus/drafts/${draft.draft_id}/activate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ included_ids: [...included], expected_revision: draft.base_revision, activation_id: crypto.randomUUID(), approver, rationale: activationRationale }),
      });
      if (!response.ok) { const detail = await response.json().catch(() => null); throw new Error(typeof detail?.detail === "object" ? (detail.detail.message ?? "activation impossible") : (detail?.detail ?? "activation impossible")); }
      const result = await response.json();
      setShowActivation(false); setMessage(`${result.n} exigences activées. ${result.diff.impacted.length} exigences dans le périmètre d’impact.`);
      setDraft(null); setIncluded(new Set()); await refresh();
    } catch (cause) { setError(`Activation impossible : ${cause instanceof Error ? cause.message : String(cause)}`); }
    finally { setBusy(false); }
  };

  if (!activeCorpus && !error) return <p className="flex items-center gap-2 text-xs text-fg-muted"><Spinner /> Chargement…</p>;
  // Toutes les exigences restent visibles : décocher ne doit jamais rendre
  // impossible un changement d'avis et une nouvelle sélection.
  const visible = (draft?.exigences ?? []).filter((req) => { const q = query.trim().toLowerCase(); const matches = !q || req.id.toLowerCase().includes(q) || (req.texte ?? "").toLowerCase().includes(q); return matches && (!onlyCollisions || (req.collision_variants?.length ?? 0) > 1); });
  const collisionCount = (draft?.exigences ?? []).filter((req) => (req.collision_variants?.length ?? 0) > 1).length;
  const currentStep = !draft ? 1 : collisionCount > 0 ? 3 : 4;
  const steps = ["Importer les sources", "Vérifier la sélection", "Arbitrer les écarts", "Activer la version"];
  return <div className="space-y-4 py-3">
    {error && <Banner tone="bad">{error}</Banner>}{message && <Banner tone="neutral">{message}</Banner>}
    <nav aria-label="Progression de la préparation" className="grid grid-cols-2 gap-2 rounded-xl border border-edge bg-surface p-3 sm:grid-cols-4">{steps.map((label, index) => { const number = index + 1; const done = number < currentStep; const current = number === currentStep; return <div key={label} aria-current={current ? "step" : undefined} className={"flex items-center gap-2 rounded-lg px-2 py-2 text-[11px] " + (current ? "bg-accent/10 text-accent-bright" : done ? "text-good" : "text-fg-faint")}><span className="font-mono">{done ? "✓" : number}</span><span>{label}</span></div>; })}</nav>
    <section className="rounded-xl border border-edge bg-surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-3"><div><h1 className="text-base font-medium text-foreground">Importer et vérifier des exigences</h1><p className="mt-1 max-w-3xl text-xs leading-relaxed text-fg-muted">Les fichiers sont préparés dans un brouillon séparé. Votre baseline active reste inchangée jusqu’à la validation finale.</p></div><div className="flex items-center gap-2"><span className="rounded-full border border-edge px-2 py-1 font-mono text-[10px] text-fg-faint">Baseline active protégée · {activeCorpus?.length ?? 0}</span>{!!activeCorpus?.length && <button className={btnGhost} disabled={busy} onClick={() => void deleteActiveBaseline()}>Supprimer la baseline</button>}</div></div>
      <input ref={fileRef} hidden type="file" accept=".xls,.xlsx,.json,.doc,.docx,.odt,.txt,application/json" multiple onChange={(event) => upload(event.target.files)} />
      <button className={`${btnPrimary} mt-4`} disabled={busy} onClick={() => fileRef.current?.click()}>{busy ? "Import en cours…" : draft ? "Ajouter d’autres documents" : "Choisir les fichiers à vérifier"}</button>
      <p className="mt-2 text-[11px] text-fg-faint">Formats acceptés : DJEM XLS/XLSX, baseline JSON et documents DOC/DOCX/ODT/TXT contenant des marqueurs [*-REQ-*]. Plusieurs sources peuvent être chargées ensemble.</p>
      {progress && <div className="mt-3"><p className="text-[11px] text-fg-muted">{progress.message}</p><div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${progress.pct}%` }} /></div></div>}
    </section>
    {(savedDrafts.length > 0 || versions.length > 0) && <details className="chat-details rounded-xl border border-edge bg-surface p-4"><summary>Historique et reprises</summary><section className="mt-3 grid gap-4 lg:grid-cols-2">
      <div className="rounded-xl border border-edge bg-surface p-4"><h2 className="text-sm font-medium text-foreground">Brouillons sauvegardés</h2><div className="mt-2 max-h-40 space-y-1 overflow-y-auto">{savedDrafts.length ? savedDrafts.map((item) => <button key={item.draft_id} onClick={() => resumeDraft(item.draft_id)} className="flex w-full cursor-pointer items-center justify-between rounded-lg border border-edge px-3 py-2 text-left text-[11px] hover:bg-surface-2"><span><span className="font-mono text-foreground">{item.source_names?.join(", ") || item.draft_id.slice(0, 8)}</span><br/><span className="text-fg-faint">{new Date(item.created_at * 1000).toLocaleString("fr-FR")}</span></span><span className="text-accent-bright">Reprendre →</span></button>) : <p className="text-[11px] text-fg-faint">Aucun brouillon.</p>}</div></div>
      <div className="rounded-xl border border-edge bg-surface p-4"><h2 className="text-sm font-medium text-foreground">Versions restaurables</h2><div className="mt-2 max-h-40 space-y-1 overflow-y-auto">{versions.length ? versions.map((item) => <div key={item.version_id} className="flex items-center justify-between gap-2 rounded-lg border border-edge px-3 py-2 text-[11px]"><span><span className="text-foreground">{item.n} exigences</span><br/><span className="text-fg-faint">{new Date(item.created_at).toLocaleString("fr-FR")} · {item.reason}</span></span><button onClick={() => restoreVersion(item.version_id)} className="cursor-pointer text-accent-bright">Restaurer</button></div>) : <p className="text-[11px] text-fg-faint">Une version sera créée avant chaque activation.</p>}</div></div>
    </section></details>}
    {draft && <>
      <section className="rounded-xl border border-edge bg-surface p-4"><div className="flex items-center justify-between"><div><h2 className="text-sm font-medium text-foreground">Résultat de l’import</h2><p className="text-[11px] text-fg-faint">Les décisions à prendre avant activation</p></div><span className={`font-mono text-2xl ${draft.health.score >= 80 ? "text-good" : draft.health.score >= 60 ? "text-warn" : "text-bad"}`}>{draft.health.score}/100</span></div><div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4"><Metric label="exigences importées" value={draft.exigences.length} /><Metric label="arbitrages" value={collisionCount} tone={collisionCount ? "text-warn" : "text-good"} /><Metric label="liens à corriger" value={draft.health.broken_links_count} tone={draft.health.broken_links_count ? "text-bad" : "text-good"} /><Metric label="énoncés vides" value={draft.health.empty_text_count} tone={draft.health.empty_text_count ? "text-bad" : "text-good"} /></div>
        <div className="mt-3 flex flex-wrap gap-2 text-[11px]"><span className="rounded-full bg-good/10 px-2 py-1 text-good">+{draft.diff.added.length} ajoutées</span><span className="rounded-full bg-warn/10 px-2 py-1 text-warn">{draft.diff.modified.length} modifiées</span><span className="rounded-full bg-bad/10 px-2 py-1 text-bad">−{draft.diff.removed.length} supprimées</span><span className="rounded-full border border-edge px-2 py-1 text-fg-muted">{draft.diff.impacted.length} impactées</span></div>
        {!!draft.source_batches?.length && <div className="mt-3 rounded-lg border border-edge p-3"><div className="flex items-center justify-between"><p className="text-[11px] font-medium text-foreground">Documents importés · {draft.source_batches.length} dépôt(s)</p><button className={btnGhost} disabled={busy} onClick={discardDraft}>Tout supprimer</button></div><div className="mt-2 space-y-1">{draft.source_batches.map((batch) => <div key={batch.batch_id} className="flex items-center justify-between gap-2 rounded-md bg-surface-2 px-2 py-1.5 text-[11px]"><span className="truncate text-fg-muted">{batch.source_names.join(", ")}</span><button className="shrink-0 cursor-pointer text-bad hover:underline" disabled={busy} onClick={() => void removeSource(batch.batch_id)}>Supprimer</button></div>)}</div></div>}
        <details className="chat-details mt-3"><summary>Rapport technique</summary><div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3"><Metric label="cycles" value={draft.health.cycles_count} tone={draft.health.cycles_count ? "text-bad" : "text-good"} /><Metric label="racines" value={draft.health.roots_count} /><Metric label="avertissements" value={draft.warnings.length} tone={draft.warnings.length ? "text-warn" : "text-good"} /></div>{!!draft.warnings.length && <><p className="mt-3 text-[11px] text-fg-faint">Les 20 premiers avertissements sont affichés.</p><ul className="mt-2 space-y-1 text-[11px] text-warn">{draft.warnings.slice(0, 20).map((warning, i) => <li key={i}>{warning}</li>)}</ul></>}</details>
      </section>
      <section className="rounded-xl border border-edge bg-surface p-4"><div className="flex flex-wrap items-center gap-2"><div><h2 className="text-sm font-medium text-foreground">Vérifier la sélection</h2><p className="text-[11px] text-fg-faint">{included.size} sur {draft.exigences.length} exigences retenues</p></div><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Rechercher…" className="ml-auto w-56 rounded-lg border border-edge bg-surface-2 px-3 py-1.5 text-xs text-foreground focus:border-accent focus:outline-none" /><button aria-pressed={onlyCollisions} className={onlyCollisions ? btnPrimary : btnGhost} onClick={() => setOnlyCollisions((value) => !value)}>À arbitrer ({collisionCount})</button><button className={btnGhost} onClick={() => setIncluded(new Set(draft.exigences.map((r) => r.id)))}>Tout sélectionner</button><button className={btnGhost} onClick={() => setIncluded(new Set())}>Tout désélectionner</button></div>
        {collisionCount > 0 && <Banner tone="warn">{collisionCount} collision(s) de formulation à arbitrer avant activation.</Banner>}
        <div className="mt-3 max-h-[52vh] overflow-y-auto rounded-lg border border-edge">{visible.length === 0 && <p className="p-8 text-center text-xs text-fg-faint">Aucune exigence ne correspond aux filtres.</p>}{visible.map((req) => <RequirementChoice key={req.id} req={req} checked={included.has(req.id)} busy={busy} onToggle={() => toggle(req.id)} onResolve={(variant) => void resolveCollision(req.id, variant)} />)}</div>
        <div className="sticky bottom-3 z-20 mt-4 flex flex-wrap justify-end gap-2 rounded-xl border border-accent/30 bg-surface/95 p-3 shadow-lg backdrop-blur"><p className="mr-auto self-center text-[11px] text-fg-muted">{collisionCount > 0 ? <>Activation bloquée : {collisionCount} arbitrage(s) restant(s).</> : <>{included.size} exigences prêtes · la version actuelle sera sauvegardée.</>}</p><button className={btnGhost} disabled={busy} onClick={discardDraft}>Abandonner</button><button className={btnGhost} disabled={!included.size} onClick={exportBaseline}>Exporter en JSON</button><button className={btnPrimary} disabled={busy || !included.size || collisionCount > 0} onClick={() => setShowActivation(true)}>{busy ? "Activation…" : collisionCount > 0 ? `Arbitrer ${collisionCount} collision(s)` : "Valider et activer"}</button></div>
      </section>
      {showActivation && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" role="presentation"><section role="dialog" aria-modal="true" aria-labelledby="activation-title" className="w-full max-w-lg rounded-xl border border-edge bg-surface p-5 shadow-2xl"><h2 id="activation-title" className="text-base font-medium text-foreground">Valider et activer cette version</h2><p className="mt-2 text-xs text-fg-muted">La baseline active de {activeCorpus?.length ?? 0} exigences sera sauvegardée avant son remplacement par les {included.size} exigences retenues.</p><label className="mt-4 block text-xs text-fg-muted">Approbateur<input value={approver} onChange={(event) => setApprover(event.target.value)} className="mt-1 w-full rounded-lg border border-edge bg-surface-2 px-3 py-2 text-foreground" /></label><label className="mt-3 block text-xs text-fg-muted">Motif de validation<textarea value={activationRationale} onChange={(event) => setActivationRationale(event.target.value)} rows={3} className="mt-1 w-full rounded-lg border border-edge bg-surface-2 px-3 py-2 text-foreground" /></label><div className="mt-4 flex justify-end gap-2"><button className={btnGhost} onClick={() => setShowActivation(false)}>Annuler</button><button className={btnPrimary} disabled={busy || !approver.trim() || !activationRationale.trim()} onClick={activate}>Confirmer l’activation</button></div></section></div>}
    </>}
  </div>;
}
