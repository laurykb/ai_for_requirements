"use client";

/** LynX — AI for Requirements. La matrice est la scène : le graphe occupe
 * l'espace, l'inspecteur d'exigence vit à sa droite, le verdict se joue en
 * dessous (bannière colorée + boîte de verre), l'audit ferme la page avec sa
 * jauge. Boîte de verre partout, signal > bruit, un seul CTA par zone. */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import ReactMarkdown from "react-markdown";

import { API_BASE, getJSON } from "@/lib/api";
import { Banner, Dot, Hint, Meter, Spinner, type Tone } from "@/components/ui";
import { NIVEAU_COLORS, type Req } from "@/components/req-graph";

const ReqGraph = dynamic(() => import("@/components/req-graph").then((m) => m.ReqGraph), {
  ssr: false,
  loading: () => <div className="h-[520px] rounded-xl border border-edge bg-surface" />,
});

// ─── Types (miroir de l'API /api/lynx/*) ───────────────────────────────────

type Finding = { scope: string; sev: string; analyzer: string; method?: string;
                 sim?: number | null; msg: string };
type Exchange = { agent: string; role: string; mission?: string; input?: string;
                  output?: string; latency_ms?: number | null; cached?: boolean; ok?: boolean };
type Verdict = { verdict: string; message: string; findings: Finding[];
                 impacted: string[]; exchanges: Exchange[] };
type AuditFinding = { req_id: string; axis: string; severity: string; message: string };
type AuditReport = { n: number; score: number; counts: Record<string, number>;
                     flagged_ids: string[]; n_non_audite: number;
                     findings: AuditFinding[];
                     exchanges: { req_id: string; input: string; output: string;
                                  flagged: boolean }[] };
type Suggestion = { texte?: string; justification?: string; changements?: string[];
                    corrige_tout?: boolean; error?: string };

const SEV_TONE: Record<string, Tone> = { INFO: "good", WARNING: "warn",
                                         BLOCKING: "bad", BLOQUANT: "bad" };
const VERDICT_COLOR: Record<string, string> = {
  VALIDE: "var(--good)", ATTENTION: "var(--warn)", BLOQUANT: "var(--bad)" };
const ROLE_STYLE: Record<string, { bg: string; label: string }> = {
  "déterministe": { bg: "#0891B2", label: "Règle" },
  embeddings: { bg: "#0D9488", label: "Vectoriel" },
  IA: { bg: "#7C3AED", label: "Agent IA" },
  "synthèse": { bg: "#B45309", label: "Synthèse" },
};

const btnPrimary =
  "cursor-pointer rounded-lg bg-accent px-3 py-2 text-xs font-semibold text-background " +
  "transition-all duration-200 hover:bg-accent-bright active:scale-[0.98] " +
  "disabled:cursor-default disabled:opacity-40";
const btnGhost =
  "cursor-pointer rounded-lg border border-edge px-3 py-1.5 text-xs text-fg-muted " +
  "transition-colors duration-200 hover:border-edge-strong hover:text-foreground " +
  "disabled:cursor-default disabled:opacity-40";
const btnDanger =
  "cursor-pointer rounded-lg border border-bad/40 px-3 py-1.5 text-xs text-bad " +
  "transition-colors duration-200 hover:bg-bad/15 disabled:cursor-default disabled:opacity-40";
const inputCls =
  "rounded-lg border border-edge bg-surface-2 px-3 py-2 text-xs text-foreground " +
  "placeholder:text-fg-faint focus:border-accent focus:outline-none";

function RoleChip({ role }: { role: string }) {
  const s = ROLE_STYLE[role] ?? { bg: "#4B5563", label: role };
  return (
    <span className="rounded-md px-1.5 py-0.5 text-[10px] font-medium text-white"
          style={{ background: s.bg }}>
      {s.label}
    </span>
  );
}

/** Boîte de verre : la timeline des agents (reçu → répondu), repliée. */
function GlassBox({ exchanges, title }: { exchanges: Exchange[]; title: string }) {
  if (!exchanges.length) return null;
  return (
    <details className="chat-details">
      <summary>{title} ({exchanges.length} agents)</summary>
      <div className="mt-3 space-y-4">
        {exchanges.map((x, i) => (
          <div key={i} className="border-l-2 border-edge pl-3">
            <p className="flex items-center gap-2 text-xs text-foreground">
              <RoleChip role={x.role} />
              <span className="font-medium">{x.agent}</span>
              {x.latency_ms != null && (
                <span className="font-mono text-[10px] tabular-nums text-fg-faint">
                  {x.latency_ms} ms{x.cached ? " · cache" : ""}
                </span>
              )}
            </p>
            {x.mission && <p className="mt-0.5 text-[11px] italic text-fg-faint">{x.mission}</p>}
            {x.input && <p className="mt-1.5 text-[11px] text-fg-faint">Reçu — {x.input}</p>}
            {x.output && (
              <div className="chat-md mt-1 text-xs leading-relaxed text-fg-muted">
                <ReactMarkdown>{x.output}</ReactMarkdown>
              </div>
            )}
          </div>
        ))}
      </div>
    </details>
  );
}

async function streamPost(path: string, body: unknown,
                          onEvent: (ev: Record<string, unknown>) => void): Promise<void> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) throw new Error(`${path} → HTTP ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames)
      for (const line of frame.split("\n"))
        if (line.startsWith("data: "))
          try { onEvent(JSON.parse(line.slice(6))); } catch { /* ignorée */ }
  }
}

// ─── Composant principal ───────────────────────────────────────────────────

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

  const [suggestion, setSuggestion] = useState<Suggestion | null>(null);
  const [suggesting, setSuggesting] = useState(false);

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

  const select = useCallback((id: string) => {
    setSelected(id);
    setCorpus((c) => {
      const r = c?.find((x) => x.id === id);
      setEditText(r?.texte ?? "");
      return c;
    });
    setSuggestion(null);
  }, []);

  // Identités STABLES pendant le streaming (sinon le graphe se reconstruit à
  // chaque token et React Flow devient instable).
  const impacted = useMemo(() => new Set(verdict?.impacted ?? []), [verdict?.impacted]);
  const flagged = useMemo(() => new Set(audit?.flagged_ids ?? []), [audit?.flagged_ids]);

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
    if (auditRunning) return;
    setAuditRunning(true);
    setAudit(null);
    setAuditProgress(null);
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
  const nBloquant = audit?.findings.filter((f) => f.severity === "BLOQUANT").length ?? 0;

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
              setSelected(null); setVerdict(null); setAudit(null);
              await refresh();
            }}
            title="Abandonne la matrice de travail et recharge la matrice d'origine."
            className={btnGhost}
          >
            Réinitialiser
          </button>
        </span>
      </div>

      {/* La scène : graphe + inspecteur. */}
      <div className="grid gap-4 xl:grid-cols-[5fr_2fr]">
        <ReqGraph corpus={corpus} selected={selected} impacted={impacted}
                  flagged={flagged} onSelect={select} />

        <aside className="rounded-xl border border-edge bg-surface p-4 xl:max-h-[520px] xl:overflow-y-auto">
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
                        style={{ background: NIVEAU_COLORS[sel.niveau] }} />
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
                {verdict.message && (
                  <div className={`chat-md text-xs leading-relaxed text-fg-muted ${
                    running ? "stream-caret" : ""}`}>
                    <ReactMarkdown>{verdict.message}</ReactMarkdown>
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
      <div className="rounded-xl border border-edge bg-surface px-5 py-4">
        <div className="flex flex-wrap items-center gap-3">
          <h3 className="text-sm font-semibold text-foreground">Audit de la matrice</h3>
          <label className="flex items-center gap-1.5 text-xs text-fg-muted">
            <input type="checkbox" checked={deep} onChange={(e) => setDeep(e.target.checked)}
                   className="accent-(--accent)" />
            Audit IA par exigence
            <Hint text="Décoché : règles structurelles et doublons vectoriels seulement (rapide)." />
          </label>
          <button onClick={runAudit} disabled={auditRunning} className={`ml-auto ${btnPrimary}`}>
            {auditRunning ? "Audit en cours…" : "Auditer"}
          </button>
        </div>
        {auditRunning && (
          <div className="mt-3">
            <p className="flex items-center gap-2 text-xs text-fg-muted">
              <Spinner />
              {auditProgress
                ? `${auditProgress[0]}/${auditProgress[1]} exigences auditées`
                : "Règles structurelles et doublons vectoriels…"}
            </p>
            {auditProgress && (
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
                <div className="h-full rounded-full bg-accent transition-[width] duration-500"
                     style={{ width: `${(100 * auditProgress[0]) / (auditProgress[1] || 1)}%` }} />
              </div>
            )}
          </div>
        )}
        {audit && (
          <div className="rise-in mt-3 space-y-3">
            <div className="flex flex-wrap items-end gap-6">
              <div>
                <p className="text-[10px] font-medium uppercase tracking-[0.18em] text-fg-faint">
                  Score de santé
                </p>
                <p className="font-mono text-3xl font-bold tabular-nums"
                   style={{ color: audit.score >= 80 ? "var(--good)"
                            : audit.score >= 50 ? "var(--warn)" : "var(--bad)" }}>
                  {audit.score}
                  <span className="text-sm text-fg-faint">/100</span>
                </p>
              </div>
              <div className="min-w-44 flex-1">
                <Meter label="Santé de la matrice" value={audit.score / 100} invert
                       hint="100 − pénalités (BLOQUANT −9, ATTENTION −3)." />
              </div>
              <p className="text-[11px] text-fg-faint">
                Matrice ▸ Règles structurelles · Doublons vectoriels
                {deep ? ` · ${audit.n} audits IA` : ""} ▸ Score
              </p>
            </div>
            {audit.flagged_ids.length === 0 ? (
              <p className="flex items-center gap-2 text-xs text-fg-muted">
                <Dot tone="good" /> Aucune exigence signalée —{" "}
                <span className="font-mono tabular-nums">{audit.n}</span> conformes.
              </p>
            ) : (
              <>
                <p className="flex items-center gap-2 text-xs text-fg-muted">
                  <Dot tone={nBloquant > 0 ? "bad" : "warn"} />
                  <span className="font-mono tabular-nums">{audit.flagged_ids.length}</span>
                  exigence(s) en défaut —{" "}
                  <span className="font-mono tabular-nums">
                    {audit.n - audit.flagged_ids.length}
                  </span>{" "}
                  conformes
                  {audit.n_non_audite > 0 && ` · ${audit.n_non_audite} non auditées`}
                </p>
                <div className="space-y-1.5">
                  {audit.flagged_ids.map((rid) => (
                    <details key={rid} className="chat-details">
                      <summary className="text-xs">
                        <button
                          onClick={(e) => { e.preventDefault(); select(rid); }}
                          className="cursor-pointer font-mono transition-colors hover:text-accent-bright"
                        >
                          {rid}
                        </button>
                        <span className="text-fg-faint">
                          · {audit.findings.filter((f) => f.req_id === rid).length} constat(s)
                        </span>
                      </summary>
                      <ul className="mt-1.5 space-y-1 text-xs text-fg-muted">
                        {audit.findings.filter((f) => f.req_id === rid).map((f, i) => (
                          <li key={i} className="flex items-start gap-2">
                            <span className="mt-1"><Dot tone={SEV_TONE[f.severity] ?? "neutral"} /></span>
                            <span>
                              <span className="rounded bg-muted px-1 py-px font-mono text-[10px] text-fg-faint">
                                {f.axis}
                              </span>{" "}
                              {f.message}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </details>
                  ))}
                </div>
              </>
            )}
            <GlassBox
              exchanges={audit.exchanges.map((x) => ({
                agent: `Audit ${x.req_id}`, role: "IA",
                input: x.input, output: x.output,
              }))}
              title="Comment LynX a audité — boîte de verre"
            />
          </div>
        )}
      </div>
    </div>
  );
}

/** Formulaire d'ajout de lien DERIVE (mère au niveau N-1 ou fille au niveau N+1). */
function LinkForm({ sel, corpus, disabled, onLink }: {
  sel: Req; corpus: Req[]; disabled: boolean;
  onLink: (action: Record<string, unknown>) => void;
}) {
  const [direction, setDirection] = useState<"mere" | "fille">("mere");
  const [other, setOther] = useState("");
  const candidates = corpus.filter((r) =>
    r.id !== sel.id && r.niveau === sel.niveau + (direction === "mere" ? -1 : 1));
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-edge pt-2">
      <select value={direction}
              onChange={(e) => { setDirection(e.target.value as "mere" | "fille"); setOther(""); }}
              className="rounded-md border border-edge bg-surface px-2 py-1 text-[11px] text-foreground">
        <option value="mere">Rattacher à une mère (L{sel.niveau - 1})</option>
        <option value="fille">Adopter une fille (L{sel.niveau + 1})</option>
      </select>
      <select value={other} onChange={(e) => setOther(e.target.value)}
              className="rounded-md border border-edge bg-surface px-2 py-1 font-mono text-[11px] text-foreground">
        <option value="">choisir…</option>
        {candidates.map((r) => (
          <option key={r.id} value={r.id}>{r.id}</option>
        ))}
      </select>
      <button
        onClick={() => {
          if (!other) return;
          const [child, parent] = direction === "mere" ? [sel.id, other] : [other, sel.id];
          onLink({ action_type: "LINK", target_id: child, link_target: parent,
                   link_type: "DERIVE" });
        }}
        disabled={disabled || !other}
        className="cursor-pointer rounded-md border border-accent/50 px-2 py-1 text-[11px] text-accent-bright transition-colors hover:bg-accent/10 disabled:opacity-40"
      >
        Lier (DERIVE)
      </button>
    </div>
  );
}

/** Créer une exigence FILLE de la sélection (id optionnel, niveau N+1 par défaut). */
function CreateChildForm({ sel, disabled, onCreate }: {
  sel: Req; disabled: boolean;
  onCreate: (action: Record<string, unknown>) => void;
}) {
  // Remonté via key={sel.id} : l'état repart proprement à chaque sélection.
  const [text, setText] = useState("");
  const [customId, setCustomId] = useState("");
  const [niveau, setNiveau] = useState(Math.min(sel.niveau + 1, 5));
  return (
    <details className="chat-details">
      <summary>
        Créer une exigence fille
        <Hint text="Nouvelle exigence rattachée à la sélection (parent principal). L'impact est analysé avant application." />
      </summary>
      <div className="mt-2 space-y-2">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={2}
          placeholder="Texte de la nouvelle exigence…"
          className="w-full rounded-lg border border-edge bg-surface px-3 py-2 text-xs leading-relaxed text-foreground placeholder:text-fg-faint focus:border-accent focus:outline-none"
        />
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={customId}
            onChange={(e) => setCustomId(e.target.value)}
            placeholder="ID (vide = auto)"
            className="w-36 rounded-md border border-edge bg-surface px-2 py-1 font-mono text-[11px] text-foreground placeholder:text-fg-faint focus:outline-none"
          />
          <label className="flex items-center gap-1 text-[11px] text-fg-muted">
            niveau
            <input type="number" min={0} max={5} value={niveau}
                   onChange={(e) => setNiveau(Number(e.target.value))}
                   className="w-14 rounded-md border border-edge bg-surface px-2 py-1 font-mono text-[11px] text-foreground focus:outline-none" />
          </label>
          <button
            onClick={() => {
              if (!text.trim()) return;
              onCreate({
                action_type: "CREATE",
                target_id: customId.trim() || `REQ-NEW-${Math.random().toString(16).slice(2, 8).toUpperCase()}`,
                new_text: text.trim(), parent_id: sel.id, niveau,
                domaine: sel.domaine ?? "Général",
              });
              setText("");
              setCustomId("");
            }}
            disabled={disabled || !text.trim()}
            className="cursor-pointer rounded-md border border-accent/50 px-2 py-1 text-[11px] text-accent-bright transition-colors hover:bg-accent/10 disabled:opacity-40"
          >
            Analyser la création
          </button>
        </div>
      </div>
    </details>
  );
}
