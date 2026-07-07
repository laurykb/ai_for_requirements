"use client";

/** LynX — AI for Requirements. La matrice est la scène : le graphe occupe
 * l'espace, l'inspecteur d'exigence vit à sa droite, le verdict se joue en
 * dessous (bannière colorée + boîte de verre), l'audit ferme la page avec sa
 * jauge. Boîte de verre partout, signal > bruit, un seul CTA par zone.
 *
 * Ce fichier porte l'état et l'orchestration (analyse SSE, apply, audit) ;
 * l'affichage est découpé : blocks.tsx (types, boîte de verre, styles),
 * panels.tsx (liens, création fille, panneau d'audit). */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import ReactMarkdown from "react-markdown";

import { API_BASE, getJSON } from "@/lib/api";
import { Banner, Dot, Hint, Spinner } from "@/components/ui";
import { type Req } from "@/components/req-graph";
import { couleurNiveau, maxNiveau } from "@/components/req-levels";
import {
  DebateBadge, GlassBox, RoleChip, SEV_TONE, btnDanger, btnGhost, btnPrimary, inputCls, streamPost,
  type AuditReport, type Exchange, type Finding, type FixProgress, type FixRecap,
  type Suggestion, type Verdict,
} from "@/components/requirements/blocks";
import {
  AuditPanel, CreateChildForm, GenerateChildrenBlock, LinkForm,
  type GenProgress, type GenRecap,
} from "@/components/requirements/panels";

const ReqGraph = dynamic(() => import("@/components/req-graph").then((m) => m.ReqGraph), {
  ssr: false,
  loading: () => <div className="h-[520px] rounded-xl border border-edge bg-surface" />,
});
// Vue 3D en bascule : three.js n'est chargé que si l'utilisateur l'active.
const ReqGraph3D = dynamic(() => import("@/components/req-graph-3d").then((m) => m.ReqGraph3D), {
  ssr: false,
  loading: () => <div className="h-[520px] rounded-xl border border-edge bg-surface" />,
});

const EMPTY_IDS = new Set<string>();
const VERDICT_COLOR: Record<string, string> = {
  VALIDE: "var(--good)", ATTENTION: "var(--warn)", BLOQUANT: "var(--bad)" };

export function Requirements() {
  const [corpus, setCorpus] = useState<Req[] | null>(null);
  const [llmOk, setLlmOk] = useState(false);
  const [model, setModel] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [semantic, setSemantic] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [running, setRunning] = useState(false);
  const [agents, setAgents] = useState<{ label: string; done: boolean }[]>([]);
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [pendingAction, setPendingAction] = useState<Record<string, unknown> | null>(null);
  const [rationale, setRationale] = useState("");
  const [feedbackDone, setFeedbackDone] = useState(false);

  const [auditRunning, setAuditRunning] = useState(false);
  const [auditProgress, setAuditProgress] = useState<[number, number] | null>(null);
  const [audit, setAudit] = useState<AuditReport | null>(null);
  const [deep, setDeep] = useState(true);

  const [fixing, setFixing] = useState(false);
  const [fixProgress, setFixProgress] = useState<FixProgress | null>(null);
  const [fixRecap, setFixRecap] = useState<FixRecap | null>(null);
  const fixAbort = useRef<AbortController | null>(null);

  const [suggestion, setSuggestion] = useState<Suggestion | null>(null);
  const [suggesting, setSuggesting] = useState(false);

  // Vue du graphe : 2D par niveaux (défaut, scannable) ou 3D en couches.
  const [graphView, setGraphView] = useState<"2d" | "3d">("2d");

  const [genRunning, setGenRunning] = useState(false);
  const [genProgress, setGenProgress] = useState<GenProgress | null>(null);
  const [genRecap, setGenRecap] = useState<GenRecap | null>(null);
  const [genError, setGenError] = useState<string | null>(null);
  const genAbort = useRef<AbortController | null>(null);

  const fileRef = useRef<HTMLInputElement>(null);
  const verdictRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    try {
      const c = await getJSON<{ n: number; exigences: Req[];
                                llm: { available: boolean; model: string } }>("/api/lynx/corpus");
      setCorpus(c.exigences);
      setLlmOk(c.llm.available);
      setModel(c.llm.model);
      setError(null);
    } catch {
      setError("API hors ligne — lancer python serve.py --web");
    }
  }, []);

  useEffect(() => {
    const t = setTimeout(() => {
      refresh();
      getJSON<{ models: string[] }>("/api/models")
        .then((m) => setModels(m.models)).catch(() => setModels([]));
    }, 0);
    return () => clearTimeout(t);
  }, [refresh]);

  const sel = corpus?.find((r) => r.id === selected) ?? null;

  // Référence vivante du corpus : `select` garde une identité stable (le
  // graphe memoïsé ne se reconstruit pas à chaque rendu).
  const corpusRef = useRef<Req[] | null>(null);
  useEffect(() => {
    corpusRef.current = corpus;
  }, [corpus]);
  const select = useCallback((id: string) => {
    setSelected(id);
    const r = corpusRef.current?.find((x) => x.id === id);
    setEditText(r?.texte ?? "");
    setSuggestion(null);
  }, []);

  // Identités STABLES pendant le streaming (sinon le graphe se reconstruit à
  // chaque token et React Flow devient instable).
  const impacted = useMemo(() => new Set(verdict?.impacted ?? []), [verdict?.impacted]);
  /** Signalées par l'audit, avec leur pire sévérité (rouge/ambre). */
  const flaggedSev = useMemo(() => {
    const m = new Map<string, "bad" | "warn">();
    for (const f of audit?.findings ?? []) {
      if (f.severity === "BLOQUANT") m.set(f.req_id, "bad");
      else if (m.get(f.req_id) !== "bad") m.set(f.req_id, "warn");
    }
    return m;
  }, [audit?.findings]);
  /** Exigences CITÉES par la synthèse LLM — calculées seulement une fois le
   * flux terminé (identité stable pendant le streaming). */
  const synthesis = running ? "" : (verdict?.message ?? "");
  const mentioned = useMemo(() => {
    if (!synthesis || !corpus) return EMPTY_IDS;
    const s = new Set<string>();
    for (const r of corpus) if (synthesis.includes(r.id)) s.add(r.id);
    return s;
  }, [synthesis, corpus]);

  const analyze = useCallback(async (action: Record<string, unknown>) => {
    if (running) return;
    setRunning(true);
    setAgents([]);
    setVerdict(null);
    setPendingAction(action);
    setRationale("");
    setFeedbackDone(false);
    setTimeout(() => verdictRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }), 60);
    const v: Verdict = { verdict: "", message: "", findings: [], impacted: [], exchanges: [] };
    try {
      await streamPost("/api/lynx/analyze", { action, semantic }, (ev) => {
        if (ev.type === "agent") {
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
    if (!pendingAction) return;
    await fetch(`${API_BASE}/api/lynx/apply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: pendingAction, rationale }),
    }).catch(() => null);
    setVerdict(null);
    setPendingAction(null);
    setAudit(null); // la matrice a changé : l'audit précédent ne vaut plus
    setFixRecap(null);
    await refresh();
  };

  const sendFeedback = async (correct: boolean) => {
    if (!verdict || !pendingAction || feedbackDone) return;
    setFeedbackDone(true);
    await fetch(`${API_BASE}/api/lynx/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action_type: String(pendingAction.action_type),
        target_id: String(pendingAction.target_id),
        verdict: verdict.verdict, message: verdict.message, correct,
      }),
    }).catch(() => null);
  };

  const runAudit = async () => {
    if (auditRunning || fixing) return;
    setAuditRunning(true);
    setAudit(null);
    setAuditProgress(null);
    setFixRecap(null);
    try {
      await streamPost("/api/lynx/audit", { deep }, (ev) => {
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
    const res = await fetch(`${API_BASE}/api/lynx/audit/fix/apply`, {
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
  };

  /** Génération descendante : l'agent propose des filles auto-auditées sur
   * une copie — rien n'est créé avant la validation sélective du récap. */
  const runGenerateChildren = async () => {
    if (!sel || genRunning || running) return;
    setGenRunning(true);
    setGenRecap(null);
    setGenProgress(null);
    setGenError(null);
    const ctl = new AbortController();
    genAbort.current = ctl;
    try {
      await streamPost("/api/lynx/generate/children", { req_id: sel.id }, (ev) => {
        if (ev.type === "progress") setGenProgress(ev as unknown as GenProgress);
        else if (ev.type === "result") setGenRecap(ev as unknown as GenRecap);
        else if (ev.type === "error") setGenError(`Génération : ${String(ev.message)}`);
      }, ctl.signal);
    } catch (e) {
      if (!ctl.signal.aborted) setGenError(`API injoignable (${String(e)})`);
    }
    genAbort.current = null;
    setGenProgress(null);
    setGenRunning(false);
  };

  const cancelGenerateChildren = () => genAbort.current?.abort();

  /** Crée réellement les filles cochées (liens DERIVE via parent_id). */
  const applyGenerateChildren = async (items: { id: string; texte: string; niveau: number }[]) => {
    if (!sel) return;
    const res = await fetch(`${API_BASE}/api/lynx/generate/children/apply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ parent_id: sel.id, items }),
    }).catch(() => null);
    if (!res?.ok) {
      setGenError("Création des filles impossible.");
      return;
    }
    setGenRecap(null);
    setAudit(null); // la matrice a changé : l'audit précédent ne vaut plus
    await refresh();
  };

  const suggest = async () => {
    if (!sel || suggesting) return;
    setSuggesting(true);
    setSuggestion(null);
    const problems = [
      ...(audit?.findings.filter((f) => f.req_id === sel.id).map((f) => f.message) ?? []),
      ...(verdict?.findings.map((f) => f.msg) ?? []),
    ];
    try {
      const res = await fetch(`${API_BASE}/api/lynx/correct`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ req_id: sel.id, problems }),
      });
      setSuggestion(await res.json());
    } catch (e) {
      setSuggestion({ error: String(e) });
    }
    setSuggesting(false);
  };

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    const fd = new FormData();
    Array.from(files).forEach((f) => fd.append("files", f));
    const res = await fetch(`${API_BASE}/api/lynx/corpus/upload`, { method: "POST", body: fd })
      .catch(() => null);
    if (res?.ok) {
      setSelected(null);
      setVerdict(null);
      setAudit(null);
      setFixRecap(null);
      await refresh();
    } else {
      setError("Import impossible : JSON de matrice invalide.");
    }
    if (fileRef.current) fileRef.current.value = "";
  };

  if (error && !corpus) return <Banner tone="bad">{error}</Banner>;
  if (!corpus)
    return (
      <p className="flex items-center gap-2 text-xs text-fg-muted">
        <Spinner /> Chargement de la matrice…
      </p>
    );

  const blocked = verdict?.verdict === "BLOQUANT";

  return (
    <div className="flex flex-col gap-5">
      {error && <Banner tone="bad">{error}</Banner>}

      {/* Barre d'état : santé, modèle, profondeur — import à droite. */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-fg-muted">
        <span className="flex items-center gap-1.5">
          <Dot tone={llmOk ? "good" : "warn"} />
          <span className="font-mono tabular-nums">{corpus.length}</span> exigences
          <span className="text-fg-faint">·</span>
          {llmOk ? "agents IA prêts" : "LLM indisponible — règles seules"}
        </span>
        {models.length > 0 && (
          <label className="flex items-center gap-1.5">
            <span className="text-fg-faint">modèle</span>
            <select
              value={model}
              onChange={async (e) => {
                setModel(e.target.value);
                await fetch(`${API_BASE}/api/lynx/model`, {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ model: e.target.value }),
                }).catch(() => null);
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
        <span className="ml-auto flex items-center gap-2">
          <input ref={fileRef} type="file" accept=".json" multiple hidden
                 onChange={(e) => upload(e.target.files)} />
          <button onClick={() => fileRef.current?.click()} className={btnGhost}>
            Importer (JSON)
          </button>
          <button
            onClick={async () => {
              await fetch(`${API_BASE}/api/lynx/corpus/reset`, { method: "POST" }).catch(() => null);
              setSelected(null); setVerdict(null); setAudit(null); setFixRecap(null);
              await refresh();
            }}
            title="Abandonne la matrice de travail et recharge la matrice d'origine."
            className={btnGhost}
          >
            Réinitialiser
          </button>
        </span>
      </div>

      {/* La scène : graphe (2D par niveaux, ou 3D en couches) + inspecteur. */}
      <div className="grid gap-4 xl:grid-cols-[5fr_2fr]">
        <div className="relative">
          <div className="absolute right-2 top-2 z-10 flex overflow-hidden rounded-lg border border-edge bg-surface-2 text-[11px]"
               role="group" aria-label="Vue du graphe">
            {(["2d", "3d"] as const).map((v) => (
              <button key={v} onClick={() => setGraphView(v)}
                      className={`cursor-pointer px-2.5 py-1 transition-colors ${
                        graphView === v ? "bg-accent/20 text-foreground" : "text-fg-faint hover:text-foreground"}`}>
                {v.toUpperCase()}
              </button>
            ))}
          </div>
          {graphView === "3d" ? (
            <ReqGraph3D corpus={corpus} selected={selected} impacted={impacted}
                        flaggedSev={flaggedSev} mentioned={mentioned} onSelect={select} />
          ) : (
            <ReqGraph corpus={corpus} selected={selected} impacted={impacted}
                      flaggedSev={flaggedSev} mentioned={mentioned} onSelect={select} />
          )}
        </div>

        <aside className="rounded-xl border border-edge bg-surface p-4 xl:max-h-[calc(100vh-22rem)] xl:min-h-[560px] xl:overflow-y-auto">
          {!sel ? (
            <div className="flex h-full min-h-40 flex-col items-center justify-center gap-2 text-center">
              <svg width="36" height="36" viewBox="0 0 24 24" fill="none" aria-hidden
                   className="text-fg-faint">
                <circle cx="12" cy="5" r="2.2" stroke="currentColor" strokeWidth="1.4" />
                <circle cx="6" cy="18" r="2.2" stroke="currentColor" strokeWidth="1.4" />
                <circle cx="18" cy="18" r="2.2" stroke="currentColor" strokeWidth="1.4" />
                <path d="M11 7 7 16M13 7l4 9" stroke="currentColor" strokeWidth="1.2" />
              </svg>
              <p className="text-xs text-fg-muted">
                Sélectionnez une exigence dans le graphe
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
                        style={{ background: couleurNiveau(sel.niveau, maxNiveau(corpus ?? [])) }} />
                  L{sel.niveau} · {sel.domaine ?? "Général"} · test {sel.test_status ?? "PENDING"}
                </p>
              </div>
              <textarea
                value={editText}
                onChange={(e) => setEditText(e.target.value)}
                rows={4}
                className={`${inputCls} w-full leading-relaxed`}
              />
              <button
                onClick={() => analyze({ action_type: "UPDATE", target_id: sel.id,
                                         new_text: editText })}
                disabled={running}
                className={`${btnPrimary} w-full`}
              >
                Analyser la modification
              </button>
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
                  <LinkForm sel={sel} corpus={corpus} disabled={running} onLink={analyze} />
                </div>
              </details>

              {/* Créer une exigence fille (parité Streamlit). */}
              <CreateChildForm key={sel.id} sel={sel} disabled={running} onCreate={analyze} />

              {/* Génération descendante L(n+1) par l'agent, récap sélectif. */}
              <GenerateChildrenBlock
                key={`gen-${sel.id}`} sel={sel} llmOk={llmOk} disabled={running}
                genRunning={genRunning} genProgress={genProgress} genRecap={genRecap}
                genError={genError} onRun={runGenerateChildren}
                onCancel={cancelGenerateChildren} onApply={applyGenerateChildren}
                onClose={() => { setGenRecap(null); setGenError(null); }} />
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
                    <button onClick={apply} disabled={blocked && !rationale.trim()}
                            className={btnPrimary}>
                      {blocked ? "Appliquer malgré le blocage" : "Appliquer à la matrice"}
                    </button>
                    <button onClick={() => { setVerdict(null); setPendingAction(null); }}
                            className={btnGhost}>
                      Annuler
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* L'audit : jauge + défauts détaillés, conformes comptés. */}
      <AuditPanel
        auditRunning={auditRunning}
        auditProgress={auditProgress}
        audit={audit}
        deep={deep}
        setDeep={setDeep}
        onRun={runAudit}
        onSelect={select}
        llmOk={llmOk}
        fixing={fixing}
        fixProgress={fixProgress}
        fixRecap={fixRecap}
        onFix={runBatchFix}
        onCancelFix={cancelBatchFix}
        onApplyFix={applyBatchFix}
        onCloseRecap={() => setFixRecap(null)}
      />
    </div>
  );
}
