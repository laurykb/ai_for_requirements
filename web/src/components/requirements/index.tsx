"use client";

/** LynX — AI for Requirements. L’explorateur occupe l’espace principal,
 * l’inspecteur d’exigence vit à sa droite, puis viennent le verdict et l’audit. Boîte de verre partout, signal > bruit, un seul CTA par zone.
 *
 * Ce fichier porte l'état et l'orchestration (analyse SSE, apply, audit) ;
 * l'affichage est découpé : blocks.tsx (types, boîte de verre, styles),
 * panels.tsx (liens, création fille, panneau d'audit). */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

import { apiFetch, getJSON } from "@/lib/api";
import { useLynxNav } from "@/components/lynx-nav";
import { Banner, Dot, Hint, Spinner } from "@/components/ui";
import { ReqExplorer, type Req, type ReqQueueItem } from "@/components/req-explorer";
import { couleurNiveau } from "@/components/req-levels";
import {
  DebateBadge, GlassBox, RoleChip, SEV_TONE, btnDanger, btnGhost, btnPrimary, inputCls, streamPost,
  type AuditReport, type Exchange, type Finding, type FixProgress, type FixRecap,
  type Suggestion, type Verdict,
} from "@/components/requirements/blocks";
import {
  AuditPanel, CreateChildForm, LinkForm,
} from "@/components/requirements/panels";
import { TraceabilityPanel, type Traceability } from "@/components/requirements/traceability-panel";

const EMPTY_IDS = new Set<string>();
const VERDICT_COLOR: Record<string, string> = {
  VALIDE: "var(--good)", ATTENTION: "var(--warn)", BLOQUANT: "var(--bad)" };

export function Requirements({ focusReq, active = true, section = "catalogue" }: {
  /** Exigence à ouvrir depuis l'extérieur (citation du chat baseline) —
   * objet recréé à chaque demande : son identité déclenche la sélection.
   * `edit` : focalise aussi l'éditeur (préparer une modification). */
  focusReq?: { id: string; edit?: boolean } | null;
  /** Recharge le corpus à chaque retour depuis Conversion. */
  active?: boolean;
  section?: "catalogue" | "quality";
} = {}) {
  const [ready, setReady] = useState(false);
  const [total, setTotal] = useState(0);
  const [catalogRevision, setCatalogRevision] = useState(0);
  const [sel, setSel] = useState<Req | null>(null);
  const [traceability, setTraceability] = useState<Traceability | null>(null);
  const [selectionLoading, setSelectionLoading] = useState(false);
  const [llmOk, setLlmOk] = useState(false);
  const [model, setModel] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [editing, setEditing] = useState(false);
  const [semantic, setSemantic] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [running, setRunning] = useState(false);
  const [agents, setAgents] = useState<{ label: string; done: boolean }[]>([]);
  // Barre de progression de l'analyse (même geste que l'audit) : total
  // d'agents annoncé par le backend, avancement = agents terminés.
  const [agentsTotal, setAgentsTotal] = useState<number | null>(null);
  // Doctrine multi-agent : complétude, appels LLM, coût vs latence — la ligne
  // de métriques de chaque analyse rend ces axes visibles en permanence.
  const [metrics, setMetrics] = useState<{ agents_done: number; llm_calls: number; wall_s: number } | null>(null);
  const [applying, setApplying] = useState(false);
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [pendingAction, setPendingAction] = useState<Record<string, unknown> | null>(null);
  const [rationale, setRationale] = useState("");
  const [feedbackDone, setFeedbackDone] = useState(false);

  const [auditRunning, setAuditRunning] = useState(false);
  const [auditProgress, setAuditProgress] = useState<[number, number] | null>(null);
  const [audit, setAudit] = useState<AuditReport | null>(null);
  const [deep, setDeep] = useState(true);
  const [impactScope, setImpactScope] = useState<string[]>([]);
  const [queueItems, setQueueItems] = useState<ReqQueueItem[]>([]);
  const [auditTargeted, setAuditTargeted] = useState(true);
  const auditStates = useMemo(() => {
    const states: Record<string, "audited" | "flagged" | "not_audited"> = {};
    for (const id of audit?.audited_ids ?? []) states[id] = "audited";
    for (const id of audit?.non_audited_ids ?? []) states[id] = "not_audited";
    for (const id of audit?.flagged_ids ?? []) states[id] = "flagged";
    return states;
  }, [audit]);

  const [fixing, setFixing] = useState(false);
  const [fixProgress, setFixProgress] = useState<FixProgress | null>(null);
  const [fixRecap, setFixRecap] = useState<FixRecap | null>(null);
  const fixAbort = useRef<AbortController | null>(null);

  const [suggestion, setSuggestion] = useState<Suggestion | null>(null);
  const [suggesting, setSuggesting] = useState(false);

  const verdictRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    try {
      const c = await getJSON<{ n: number; llm: { available: boolean; model: string } }>("/api/lynx/corpus/status");
      setTotal(c.n);
      setLlmOk(c.llm.available);
      setModel(c.llm.model);
      setReady(true);
      setCatalogRevision((value) => value + 1);
      setError(null);
      getJSON<{ impacted: string[] }>("/api/lynx/corpus/impact")
        .then((impact) => setImpactScope(impact.impacted ?? [])).catch(() => setImpactScope([]));
      getJSON<{ items: ReqQueueItem[] }>("/api/lynx/corpus/issues")
        .then((queue) => setQueueItems(queue.items ?? [])).catch(() => setQueueItems([]));
    } catch {
      setError("API hors ligne — lancer python serve.py --web");
    }
  }, []);

  useEffect(() => {
    if (!active) return;
    const t = setTimeout(() => {
      refresh();
      getJSON<{ models: string[] }>("/api/models")
        .then((m) => setModels(m.models)).catch(() => setModels([]));
    }, 0);
    return () => clearTimeout(t);
  }, [refresh, active]);

  const selectedQueue = queueItems.find((item) => item.req_id === selected) ?? null;
  const selectionRequest = useRef(0);
  const select = useCallback(async (id: string) => {
    const request = ++selectionRequest.current;
    setSelected(id);
    setSelectionLoading(true);
    setTraceability(null);
    setSuggestion(null);
    try {
      const [requirement, relations] = await Promise.all([
        getJSON<Req>("/api/lynx/requirements/" + encodeURIComponent(id)),
        getJSON<Traceability & { requirement: Req }>("/api/lynx/requirements/" + encodeURIComponent(id) + "/relations"),
      ]);
      if (request !== selectionRequest.current) return;
      setSel(requirement);
      setTraceability({ upstream: relations.upstream, downstream: relations.downstream, path: relations.path });
      setEditText(requirement.texte ?? "");
      setEditing(false);
      setError(null);
    } catch {
      if (request === selectionRequest.current) {
        setSel(null);
        setError(`Exigence ${id} introuvable.`);
      }
    } finally {
      if (request === selectionRequest.current) setSelectionLoading(false);
    }
  }, []);

  // Ouverture externe (citation du chat baseline -> arbre) : consommée une
  // seule fois par objet focusReq, dès que le corpus est chargé. Sélection
  // différée d'un tick (règle set-state-in-effect).
  // Ponts Matrice ↔ Chat (contexte fourni par la page requirements).
  const lynxNav = useLynxNav();
  const consumedFocus = useRef<object | null>(null);
  const editRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (!focusReq || consumedFocus.current === focusReq || !ready) return;
    consumedFocus.current = focusReq;
    const t = setTimeout(() => {
      select(focusReq.id);
      if (focusReq.edit) {
        setEditing(true);
        // Après le rendu du panneau de la sélection : éditeur prêt à taper.
        setTimeout(() => editRef.current?.focus(), 80);
      }
    }, 0);
    return () => clearTimeout(t);
  }, [focusReq, ready, select]);

  // Les exigences citées ne sont recalculées qu’après la fin du streaming.
  /** Exigences CITÉES par la synthèse LLM — calculées seulement une fois le
   * flux terminé (identité stable pendant le streaming). */
  const synthesis = running ? "" : (verdict?.message ?? "");
  const mentioned = useMemo(() => {
    if (!synthesis) return EMPTY_IDS;
    return new Set(synthesis.match(/\b[A-Z0-9]+(?:[-_.][A-Z0-9]+){2,}\b/g) ?? []);
  }, [synthesis]);

  const analyze = useCallback(async (action: Record<string, unknown>) => {
    if (running) return;
    setRunning(true);
    setAgents([]);
    setAgentsTotal(null);
    setMetrics(null);
    setVerdict(null);
    setPendingAction(action);
    setRationale("");
    setFeedbackDone(false);
    const v: Verdict = { verdict: "", message: "", findings: [], impacted: [], exchanges: [] };
    try {
      await streamPost("/api/lynx/analyze", { action, semantic }, (ev) => {
        if (ev.type === "agents_total") {
          setAgentsTotal(Number(ev.n) || null);
        } else if (ev.type === "metrics") {
          setMetrics({ agents_done: Number(ev.agents_done), llm_calls: Number(ev.llm_calls),
                       wall_s: Number(ev.wall_s) });
        } else if (ev.type === "agent") {
          const label = String(ev.label);
          setAgents((a) => ev.kind === "start"
            ? [...a, { label, done: false }]
            : a.map((x) => (x.label === label ? { ...x, done: true } : x)));
        } else if (ev.type === "report") {
          v.verdict = String(ev.verdict);
          v.findings = ev.findings as Finding[];
          v.impacted = ev.impacted as string[];
          setVerdict({ ...v });
        } else if (ev.type === "token") {
          v.message += String(ev.text);
          setVerdict({ ...v });
        } else if (ev.type === "exchanges") {
          v.exchanges = ev.exchanges as Exchange[];
          setVerdict({ ...v });
        } else if (ev.type === "error") {
          setError(`Analyse : ${String(ev.message)}`);
        }
      });
    } catch (e) {
      setError(`API injoignable (${String(e)})`);
    }
    setRunning(false);
  }, [running, semantic]);

  const apply = async () => {
    if (!pendingAction || applying) return;
    const actionType = String(pendingAction.action_type);
    const selectedBeforeApply = selected;
    setApplying(true);
    setError(null);
    try {
      const response = await apiFetch("/api/lynx/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: pendingAction, rationale }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(String(body.detail ?? "HTTP " + response.status));
      }
      setVerdict(null);
      setPendingAction(null);
      setAudit(null);
      setFixRecap(null);
      await refresh();
      if (actionType === "DELETE") {
        setSelected(null); setSel(null); setTraceability(null);
      } else if (selectedBeforeApply) {
        await select(selectedBeforeApply);
      }
    } catch (cause) {
      setError("Application impossible : " + (cause instanceof Error ? cause.message : String(cause)));
    } finally {
      setApplying(false);
    }
  };

  const setRootStatus = async (declared: boolean) => {
    if (!sel) return;
    const path = "/api/lynx/requirements/" + encodeURIComponent(sel.id) + "/root-status";
    const response = await apiFetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ declared, rationale: declared ? "Racine métier confirmée par la revue RPP" : "Rattachement à revoir" }) });
    if (!response.ok) { setError("Qualification de la racine impossible."); return; }
    await refresh();
    await select(sel.id);
  };

  const sendFeedback = async (correct: boolean) => {
    if (!verdict || !pendingAction || feedbackDone) return;
    const response = await apiFetch("/api/lynx/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action_type: String(pendingAction.action_type),
        target_id: String(pendingAction.target_id),
        verdict: verdict.verdict, message: verdict.message, correct,
      }),
    }).catch(() => null);
    if (!response?.ok) {
      setError("Envoi du retour impossible : " + (response ? "HTTP " + response.status : "API indisponible"));
      return;
    }
    setFeedbackDone(true);
  };

  const runAudit = async () => {
    if (auditRunning || fixing) return;
    setAuditRunning(true);
    setAudit(null);
    setAuditProgress(null);
    setFixRecap(null);
    try {
      await streamPost("/api/lynx/audit", {
        deep, req_ids: auditTargeted && impactScope.length ? impactScope : null,
      }, (ev) => {
        if (ev.type === "progress") setAuditProgress([Number(ev.done), Number(ev.total)]);
        else if (ev.type === "report") setAudit(ev as unknown as AuditReport);
        else if (ev.type === "error") setError(`Audit : ${String(ev.message)}`);
      });
    } catch (e) {
      setError(`API injoignable (${String(e)})`);
    }
    setAuditRunning(false);
  };

  /** Correction en lot : corriger → ré-auditer (3 passes max), sur une copie
   * côté API — rien n'est appliqué avant la validation sélective du récap. */
  const runBatchFix = async () => {
    if (!audit || fixing || auditRunning) return;
    setFixing(true);
    setFixRecap(null);
    setFixProgress(null);
    const ctl = new AbortController();
    fixAbort.current = ctl;
    try {
      await streamPost("/api/lynx/audit/fix", { findings: audit.findings, deep }, (ev) => {
        if (ev.type === "progress") setFixProgress(ev as unknown as FixProgress);
        else if (ev.type === "result") setFixRecap(ev as unknown as FixRecap);
        else if (ev.type === "error") setError(`Correction en lot : ${String(ev.message)}`);
      }, ctl.signal);
    } catch (e) {
      if (!ctl.signal.aborted) setError(`API injoignable (${String(e)})`);
    }
    fixAbort.current = null;
    setFixProgress(null);
    setFixing(false);
  };

  const cancelBatchFix = () => fixAbort.current?.abort();

  /** Applique les corrections cochées à la matrice réelle. */
  const applyBatchFix = async (items: { req_id: string; texte: string }[]) => {
    const res = await apiFetch(`/api/lynx/audit/fix/apply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
    }).catch(() => null);
    if (!res?.ok) {
      setError("Application des corrections impossible.");
      return;
    }
    setFixRecap(null);
    setAudit(null); // la matrice a changé : l'audit précédent ne vaut plus
    await refresh();
    if (selected) await select(selected);
  };

  /** Génération descendante : l'agent propose des filles auto-auditées sur
   * une copie — rien n'est créé avant la validation sélective du récap. */
  const suggest = async () => {
    if (!sel || suggesting) return;
    setSuggesting(true);
    setSuggestion(null);
    const problems = [
      ...(audit?.findings.filter((f) => f.req_id === sel.id).map((f) => f.message) ?? []),
      ...(verdict?.findings.map((f) => f.msg) ?? []),
    ];
    try {
      const res = await apiFetch(`/api/lynx/correct`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ req_id: sel.id, problems }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(String(body.detail ?? "HTTP " + res.status));
      setSuggestion(body);
    } catch (e) {
      setSuggestion({ error: String(e) });
    }
    setSuggesting(false);
  };

  if (error && !ready) return <Banner tone="bad">{error}</Banner>;
  if (!ready)
    return (
      <p className="flex items-center gap-2 text-xs text-fg-muted">
        <Spinner /> Chargement de la matrice…
      </p>
    );

  const blocked = verdict?.verdict === "BLOQUANT";

  return (
    <div className="flex flex-col gap-5">
      {error && <Banner tone="bad">{error}</Banner>}

      <div className={section === "catalogue" ? "contents" : "hidden"}>
      {/* Barre d'état : santé du moteur et profondeur d'analyse. */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-fg-muted">
        <span className="flex items-center gap-1.5">
          <Dot tone={llmOk ? "good" : "warn"} />
          <span className="font-mono tabular-nums">{total}</span> exigences
          <span className="text-fg-faint">·</span>
          {llmOk ? "agents IA prêts" : "LLM indisponible — règles seules"}
        </span>
        {models.length > 0 && (
          <label className="flex items-center gap-1.5">
            <span className="text-fg-faint">modèle</span>
            <select
              value={model}
              onChange={async (e) => {
                const previous = model;
                const next = e.target.value;
                const response = await apiFetch("/api/lynx/model", {
                  method: "POST", headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ model: next }),
                }).catch(() => null);
                if (!response?.ok) {
                  setModel(previous);
                  setError("Changement de modèle impossible : " + (response ? "HTTP " + response.status : "API indisponible"));
                  return;
                }
                setModel(next);
              }}
              className="rounded-md border border-edge bg-surface-2 px-2 py-1 font-mono text-[11px] text-foreground focus:outline-none"
            >
              {(models.includes(model) ? models : [model, ...models]).map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </label>
        )}
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={semantic} onChange={(e) => setSemantic(e.target.checked)}
                 className="accent-(--accent)" />
          Analyse approfondie (IA)
          <Hint text="Décoché : seules les règles déterministes tournent (instantané, sans LLM)." />
        </label>
      </div>

      {/* Vue fonctionnelle unifiée : catalogue rapide + inspecteur complet. */}
      <div className="grid gap-4 xl:grid-cols-[5fr_2fr]">
        <ReqExplorer selected={selected} onSelect={select} impactedIds={impactScope}
                     queueItems={queueItems} refreshToken={catalogRevision}
                     auditStates={auditStates} />

        <aside className="rounded-xl border border-edge bg-surface p-4 xl:max-h-[calc(100vh-17rem)] xl:min-h-[620px] xl:overflow-y-auto">
          {selectionLoading ? (
            <div className="flex h-full min-h-40 items-center justify-center gap-2 text-xs text-fg-muted">
              <Spinner /> Chargement de la fiche…
            </div>
          ) : !sel ? (
            <div className="flex h-full min-h-40 flex-col items-center justify-center gap-2 text-center">
              <svg width="36" height="36" viewBox="0 0 24 24" fill="none" aria-hidden
                   className="text-fg-faint">
                <circle cx="12" cy="5" r="2.2" stroke="currentColor" strokeWidth="1.4" />
                <circle cx="6" cy="18" r="2.2" stroke="currentColor" strokeWidth="1.4" />
                <circle cx="18" cy="18" r="2.2" stroke="currentColor" strokeWidth="1.4" />
                <path d="M11 7 7 16M13 7l4 9" stroke="currentColor" strokeWidth="1.2" />
              </svg>
              <p className="text-xs text-fg-muted">
                Sélectionnez une exigence dans le catalogue
              </p>
              <p className="text-[11px] text-fg-faint">
                pour l&apos;inspecter, la modifier, la relier ou la corriger.
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              <div>
                <p className="text-[10px] font-medium uppercase tracking-[0.18em] text-fg-faint">
                  Exigence
                </p>
                <p className="mt-0.5 font-mono text-base text-foreground">{sel.id}</p>
                <p className="mt-1 flex items-center gap-1.5 text-[11px] text-fg-faint">
                  <span className="inline-block h-1.5 w-1.5 rounded-full"
                        style={{ background: couleurNiveau(sel.niveau, 5) }} />
                  L{sel.niveau} · {sel.domaine ?? "Général"} · test {sel.test_status ?? "PENDING"}
                </p>
                <p className="mt-1 text-[10px] text-fg-faint">Source : <span className="text-fg-muted">{sel.source || "non renseignée"}</span></p>
              {!!sel.occurrences?.length && <details className="chat-details mt-2"><summary>Provenance · {sel.occurrences.length} occurrence(s)</summary><ul className="mt-2 space-y-1 text-[11px] text-fg-muted">{sel.occurrences.map((occurrence, index) => <li key={index} className="rounded border border-edge p-2"><span className="font-mono text-foreground">{occurrence.source || "source inconnue"}</span>{occurrence.section && <span> · section {occurrence.section}{occurrence.section_title ? " — " + occurrence.section_title : ""}</span>}{occurrence.row && <span> · ligne {occurrence.row}{occurrence.column ? ", colonne " + occurrence.column : ""}</span>}</li>)}</ul></details>}{sel.arbitration && <p className="mt-2 rounded border border-good/30 bg-good/5 p-2 text-[10px] text-fg-muted">Formulation arbitrée par {sel.arbitration.decided_by || "un réviseur"} · source retenue : {sel.arbitration.selected_source || sel.source || "inconnue"} · {sel.arbitration.rationale}</p>}
              {!sel.parent_id && <button className={(sel.root_declared ? btnGhost : btnPrimary) + " mt-2"} onClick={() => void setRootStatus(!sel.root_declared)}>{sel.root_declared ? "Rouvrir le rattachement" : "Confirmer comme racine métier"}</button>}
              </div>
              <TraceabilityPanel selected={sel} traceability={traceability}
                                 loading={selectionLoading} onSelect={select} />
              {selectedQueue && (
                <section className="rounded-lg border border-warn/30 bg-warn/5 p-3">
                  <div className="flex items-center justify-between">
                    <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-warn">À traiter</p>
                    <span className="font-mono text-[10px] text-fg-faint">{selectedQueue.issues.length} constat(s)</span>
                  </div>
                  <div className="mt-2 space-y-2">
                    {selectedQueue.issues.map((issue) => (
                      <div key={issue.code}>
                        <p className="text-xs font-medium text-foreground">{issue.label}</p>
                        <p className="mt-0.5 text-[11px] leading-relaxed text-fg-muted">{issue.action}</p>
                      </div>
                    ))}
                  </div>
                </section>
              )}
              {editing ? (
                <div className="rounded-lg border border-accent/30 bg-accent/5 p-3">
                  <p className="mb-2 text-[10px] font-medium uppercase tracking-[0.14em] text-accent-bright">Modification en préparation</p>
                  <textarea ref={editRef} value={editText} onChange={(e) => setEditText(e.target.value)}
                            rows={6} className={inputCls + " w-full leading-relaxed"} />
                  <p className="mt-1 text-[10px] text-fg-faint">La baseline ne change qu’après analyse du verdict et confirmation.</p>
                  <div className="mt-3 flex gap-2">
                    <button onClick={() => { setEditing(false); setEditText(sel.texte ?? ""); }}
                            disabled={running} className={btnGhost + " flex-1"}>Annuler</button>
                    <button onClick={() => analyze({ action_type: "UPDATE", target_id: sel.id, new_text: editText })}
                            disabled={running || editText.trim() === (sel.texte ?? "").trim()}
                            className={btnPrimary + " flex-1"}>
                      {running ? "Analyse…" : "Analyser"}
                    </button>
                  </div>
                </div>
              ) : (
                <div className="rounded-lg border border-edge bg-surface-2 p-3">
                  <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">{sel.texte || "(énoncé vide)"}</p>
                  <button onClick={() => { setEditing(true); setTimeout(() => editRef.current?.focus(), 50); }}
                          className={btnPrimary + " mt-3 w-full"}>Modifier cette exigence</button>
                </div>
              )}
              {(running || verdict) && pendingAction?.target_id === sel.id && (
                <div className="rounded-lg border border-edge bg-surface-2 p-3">
                  <div className="flex items-center gap-2">
                    {running ? <Spinner /> : <Dot tone={verdict?.verdict === "VALIDE" ? "good" : verdict?.verdict === "BLOQUANT" ? "bad" : "warn"} />}
                    <span className="text-xs font-semibold" style={{ color: verdict?.verdict ? VERDICT_COLOR[verdict.verdict] : undefined }}>
                      {running ? "Analyse d’impact en cours" : verdict?.verdict}
                    </span>
                    {!!verdict?.impacted.length && <span className="ml-auto font-mono text-[10px] text-fg-faint">{verdict.impacted.length} impactée(s)</span>}
                  </div>
                  {!running && verdict?.findings.slice(0, 2).map((finding, index) => (
                    <p key={index} className="mt-2 text-[11px] leading-relaxed text-fg-muted">{finding.msg}</p>
                  ))}
                  {!running && verdict && (
                    <div className="mt-3 flex gap-2">
                      <button onClick={() => { setVerdict(null); setPendingAction(null); }} className={btnGhost + " flex-1"}>Annuler</button>
                      <button onClick={apply} disabled={applying || (blocked && !rationale.trim())} className={btnPrimary + " flex-1"}>
                        {applying ? "Application…" : blocked ? "Justifier plus bas" : "Appliquer"}
                      </button>
                    </div>
                  )}
                </div>
              )}
              <div className="flex gap-2">
                <button
                  onClick={() => analyze({ action_type: "DELETE", target_id: sel.id })}
                  disabled={running}
                  title="Analyse l'impact d'une suppression avant de l'appliquer."
                  className={`${btnDanger} flex-1`}
                >
                  Supprimer…
                </button>
                <button
                  onClick={suggest}
                  disabled={suggesting || !llmOk}
                  title="Demande à l'agent de rédaction une version corrigée (1 appel LLM)."
                  className={`${btnGhost} flex-1`}
                >
                  {suggesting ? "Suggestion…" : "Corriger"}
                </button>
              </div>
              {lynxNav && (
                <button
                  onClick={() => lynxNav.askAboutRequirement(sel.id)}
                  title="Ouvre le Chat avec une question pré-remplie sur cette exigence (rôle, dérivations, vérification)."
                  className={`${btnGhost} w-full`}
                >
                  Interroger la baseline sur {sel.id} →
                </button>
              )}

              {suggestion && (
                <div className="rounded-lg border border-edge bg-surface-2 px-3 py-2.5 text-xs">
                  {suggestion.error ? (
                    <p className="text-bad">Suggestion impossible : {suggestion.error}</p>
                  ) : (
                    <>
                      <p className="flex items-center gap-2">
                        <RoleChip role="IA" />
                        <span className="text-[11px] uppercase tracking-[0.14em] text-fg-faint">
                          Agent rédaction
                        </span>
                      </p>
                      <p className="mt-1.5 leading-relaxed text-foreground">{suggestion.texte}</p>
                      {suggestion.justification && (
                        <p className="mt-1.5 text-[11px] leading-relaxed text-fg-faint">
                          {suggestion.justification}
                        </p>
                      )}
                      <button
                        onClick={() => { setEditText(suggestion.texte ?? editText);
                                         setSuggestion(null); }}
                        className="mt-2 cursor-pointer rounded-md border border-accent/50 px-2 py-1 text-[11px] text-accent-bright transition-colors hover:bg-accent/10"
                      >
                        Reprendre ce texte
                      </button>
                    </>
                  )}
                </div>
              )}

              {/* Liens (remap DERIVE + retrait). */}
              <details className="chat-details">
                <summary>
                  Liens ({(sel.links ?? []).length + (sel.parent_id ? 1 : 0)})
                  <Hint text="DERIVE — se décline de (décomposition). Le remap ne crée que des liens entre niveaux adjacents." />
                </summary>
                <div className="mt-2 space-y-1.5 text-xs text-fg-muted">
                  {sel.parent_id && (
                    <p className="flex items-center gap-2">
                      <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px]">mère</span>
                      {sel.parent_id}
                    </p>
                  )}
                  {(sel.links ?? []).map((lk, i) => (
                    <p key={i} className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-2">
                        <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px]">
                          {lk.type}
                        </span>
                        → {lk.target}
                      </span>
                      <button
                        onClick={() => analyze({ action_type: "UNLINK", target_id: sel.id,
                                                 link_target: lk.target, link_type: lk.type })}
                        className="cursor-pointer text-[11px] text-fg-faint transition-colors hover:text-bad"
                      >
                        retirer
                      </button>
                    </p>
                  ))}
                  <LinkForm sel={sel} disabled={running} onLink={analyze} />
                </div>
              </details>

              {/* Créer une exigence fille (parité Streamlit). */}
              <CreateChildForm key={sel.id} sel={sel} disabled={running} onCreate={analyze} />

            </div>
          )}
        </aside>
      </div>

      {/* Le théâtre du verdict. */}
      <div ref={verdictRef}>
        {(running || verdict) && (
          <div className="rise-in rounded-xl border border-edge bg-surface px-5 py-4">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-fg-faint">
              {agents.map((a) => (
                <span key={a.label} className="flex items-center gap-1.5">
                  <Dot tone={a.done ? "good" : "accent"} pulse={!a.done} />
                  {a.label}
                </span>
              ))}
              {running && !agents.length && <><Spinner /> Analyse…</>}
            </div>
            {running && agentsTotal != null && agentsTotal > 0 && (
              <div className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-muted">
                <div className="h-full rounded-full bg-accent transition-[width] duration-500"
                     style={{ width: `${(100 * agents.filter((a) => a.done).length) / agentsTotal}%` }} />
              </div>
            )}
            {!running && metrics && (
              <p className="mt-2 font-mono text-[11px] tabular-nums text-fg-faint"
                 title="Complétude des agents · appels LLM réels (hors cache) · durée totale — les axes de pilotage du système multi-agent.">
                {metrics.agents_done}{agentsTotal ? `/${agentsTotal}` : ""} agents ·{" "}
                {metrics.llm_calls} appel{metrics.llm_calls > 1 ? "s" : ""} LLM · {metrics.wall_s} s
              </p>
            )}
            {verdict?.verdict && (
              <div className="verdict-banner mt-3 space-y-2.5"
                   style={{ "--fg": VERDICT_COLOR[verdict.verdict] } as React.CSSProperties}>
                <p className="flex items-baseline gap-3">
                  <span className="text-base font-bold tracking-wide"
                        style={{ color: VERDICT_COLOR[verdict.verdict] }}>
                    {verdict.verdict}
                  </span>
                  {verdict.impacted.length > 0 && (
                    <span className="text-[11px] text-fg-faint">
                      <span className="font-mono tabular-nums">{verdict.impacted.length}</span>{" "}
                      exigence(s) impactée(s)
                    </span>
                  )}
                  {!running && !feedbackDone && (
                    <span className="ml-auto flex items-center gap-1 text-[11px] text-fg-faint">
                      verdict correct ?
                      <button onClick={() => sendFeedback(true)}
                              className="cursor-pointer rounded px-1.5 py-0.5 transition-colors hover:bg-good/15 hover:text-good">
                        oui
                      </button>
                      <span>·</span>
                      <button onClick={() => sendFeedback(false)}
                              className="cursor-pointer rounded px-1.5 py-0.5 transition-colors hover:bg-bad/15 hover:text-bad">
                        non
                      </button>
                    </span>
                  )}
                  {feedbackDone && (
                    <span className="ml-auto text-[11px] text-fg-faint">merci — noté.</span>
                  )}
                </p>
                {/* La synthèse de l'ingénieur IA : LE texte à lire — mis en
                    valeur (carte élevée, texte plus grand et plus clair). */}
                {verdict.message && (
                  <div className="rounded-lg border border-edge bg-surface-2 px-4 py-3">
                    <p className="mb-1.5 flex items-center gap-2">
                      <RoleChip role="synthèse" />
                      <span className="text-[10px] uppercase tracking-[0.18em] text-fg-faint">
                        Synthèse de l&apos;ingénieur IA
                      </span>
                    </p>
                    <div className={`chat-md text-sm leading-relaxed text-foreground ${
                      running ? "stream-caret" : ""}`}>
                      <ReactMarkdown>{verdict.message}</ReactMarkdown>
                    </div>
                    {!running && mentioned.size > 0 && (
                      <p className="mt-2 border-t border-edge pt-2 text-[11px] text-fg-faint">
                        Exigences citées — surlignées en blanc dans le graphe :{" "}
                        {[...mentioned].map((id) => (
                          <button key={id} onClick={() => select(id)}
                                  className="mr-1.5 cursor-pointer font-mono text-foreground transition-colors hover:text-accent-bright">
                            {id}
                          </button>
                        ))}
                      </p>
                    )}
                  </div>
                )}
                {verdict.findings.length > 0 && (
                  <ul className="space-y-1 text-xs text-fg-muted">
                    {verdict.findings.map((f, i) => (
                      <li key={i} className="flex items-start gap-2">
                        <span className="mt-1"><Dot tone={SEV_TONE[f.sev] ?? "neutral"} /></span>
                        <span>
                          <span className="rounded bg-muted px-1 py-px font-mono text-[10px] text-fg-faint">
                            {f.scope}
                          </span>{" "}
                          {f.msg}
                          <DebateBadge debate={f.debate} />
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
                <GlassBox exchanges={verdict.exchanges}
                          title="Comment LynX a raisonné — boîte de verre" />
                {!running && pendingAction && (
                  <div className="flex flex-wrap items-center gap-2 border-t border-edge pt-3">
                    {blocked && (
                      <input
                        value={rationale}
                        onChange={(e) => setRationale(e.target.value)}
                        placeholder="Justification du passage en force (obligatoire)…"
                        className={`${inputCls} flex-1 border-bad/40`}
                      />
                    )}
                    <button onClick={apply}
                            disabled={applying || (blocked && !rationale.trim())}
                            className={btnPrimary}>
                      {applying ? "Application…"
                        : blocked ? "Appliquer malgré le blocage" : "Appliquer à la matrice"}
                    </button>
                    <button onClick={() => { setVerdict(null); setPendingAction(null); }}
                            disabled={applying} className={btnGhost}>
                      Annuler
                    </button>
                    {applying && <Spinner />}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
      </div>

      {section === "quality" && <>
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[11px] uppercase tracking-[0.16em] text-fg-faint">Contrôle démontrable</p>
          <h1 className="text-xl font-semibold text-foreground">Audit de la baseline</h1>
          <p className="mt-1 max-w-3xl text-xs text-fg-muted">Les contrôles structurels ne valent pas audit sémantique. Une exigence reste « non auditée » tant qu’un audit explicite n’a pas abouti.</p>
        </div>
        <span className="font-mono text-xs text-fg-faint">{total} exigences actives</span>
      </header>

      {/* L'audit : constats démontrés et exigences explicitement non auditées. */}
      {impactScope.length > 0 && <div className="flex flex-wrap items-center gap-3 rounded-lg border border-accent/30 bg-accent/5 px-4 py-2 text-xs text-fg-muted"><span><span className="font-mono text-accent-bright">{impactScope.length}</span> exigences dans le dernier périmètre d’impact</span><label className="ml-auto flex cursor-pointer items-center gap-1.5"><input type="checkbox" checked={auditTargeted} onChange={(e) => setAuditTargeted(e.target.checked)} className="accent-(--accent)" />Limiter le prochain audit à ce périmètre et son voisinage</label></div>}
      <AuditPanel
        auditRunning={auditRunning}
        auditProgress={auditProgress}
        audit={audit}
        deep={deep}
        setDeep={setDeep}
        onRun={runAudit}
        onSelect={(id) => lynxNav ? lynxNav.openRequirement(id) : void select(id)}
        llmOk={llmOk}
        fixing={fixing}
        fixProgress={fixProgress}
        fixRecap={fixRecap}
        onFix={runBatchFix}
        onCancelFix={cancelBatchFix}
        onApplyFix={applyBatchFix}
        onCloseRecap={() => setFixRecap(null)}
      />
      </>}
    </div>
  );
}
