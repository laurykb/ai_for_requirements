"use client";

/** LynX — AI for Requirements : matrice (graphe DAG), analyse d'impact d'une
 * action (verdict VALIDE/ATTENTION/BLOQUANT + boîte de verre), audit complet,
 * suggestion de correction. Boîte de verre partout, signal > bruit. */

import { useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import ReactMarkdown from "react-markdown";

import { API_BASE, getJSON } from "@/lib/api";
import { Banner, Dot, Hint, Spinner, type Tone } from "@/components/ui";
import type { Req } from "@/components/req-graph";

const ReqGraph = dynamic(() => import("@/components/req-graph").then((m) => m.ReqGraph), {
  ssr: false,
  loading: () => <div className="h-[440px] rounded-xl border border-edge bg-surface" />,
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
const ROLE_STYLE: Record<string, { bg: string; label: string }> = {
  "déterministe": { bg: "#0891B2", label: "Règle" },
  embeddings: { bg: "#0D9488", label: "Vectoriel" },
  IA: { bg: "#7C3AED", label: "Agent IA" },
  "synthèse": { bg: "#B45309", label: "Synthèse" },
};

function RoleChip({ role }: { role: string }) {
  const s = ROLE_STYLE[role] ?? { bg: "#4B5563", label: role };
  return (
    <span className="rounded-md px-1.5 py-0.5 text-[10px] font-medium text-white"
          style={{ background: s.bg }}>
      {s.label}
    </span>
  );
}

function VerdictWord({ v }: { v: string }) {
  return (
    <span className={`text-sm font-bold ${
      v === "VALIDE" ? "text-good" : v === "ATTENTION" ? "text-warn" : "text-bad"}`}>
      {v}
    </span>
  );
}

/** Boîte de verre : la timeline des agents (reçu → répondu), repliée. */
function GlassBox({ exchanges, title }: { exchanges: Exchange[]; title: string }) {
  if (!exchanges.length) return null;
  return (
    <details className="chat-details">
      <summary>{title} ({exchanges.length} agents)</summary>
      <div className="mt-2 space-y-3">
        {exchanges.map((x, i) => (
          <div key={i} className="border-l-2 border-edge pl-3">
            <p className="flex items-center gap-2 text-xs text-foreground">
              <RoleChip role={x.role} />
              {x.agent}
              {x.latency_ms != null && (
                <span className="text-[10px] text-fg-faint">
                  {x.latency_ms} ms{x.cached ? " · cache" : ""}
                </span>
              )}
            </p>
            {x.mission && <p className="mt-0.5 text-[11px] text-fg-faint">{x.mission}</p>}
            {x.input && <p className="mt-1 text-[11px] text-fg-faint">Reçu — {x.input}</p>}
            {x.output && (
              <div className="chat-md mt-1 text-xs text-fg-muted">
                <ReactMarkdown>{x.output}</ReactMarkdown>
              </div>
            )}
          </div>
        ))}
      </div>
    </details>
  );
}

// ─── Flux SSE générique (analyze / audit) ──────────────────────────────────

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
  const [selected, setSelected] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [semantic, setSemantic] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Analyse en cours + verdict.
  const [running, setRunning] = useState(false);
  const [agents, setAgents] = useState<{ label: string; done: boolean }[]>([]);
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [pendingAction, setPendingAction] = useState<Record<string, unknown> | null>(null);
  const [rationale, setRationale] = useState("");

  // Audit.
  const [auditRunning, setAuditRunning] = useState(false);
  const [auditProgress, setAuditProgress] = useState<[number, number] | null>(null);
  const [audit, setAudit] = useState<AuditReport | null>(null);
  const [deep, setDeep] = useState(true);

  // Correction.
  const [suggestion, setSuggestion] = useState<Suggestion | null>(null);
  const [suggesting, setSuggesting] = useState(false);

  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      const c = await getJSON<{ n: number; exigences: Req[];
                                llm: { available: boolean } }>("/api/lynx/corpus");
      setCorpus(c.exigences);
      setLlmOk(c.llm.available);
      setError(null);
    } catch {
      setError("API hors ligne — lancer python serve.py --web");
    }
  }, []);

  useEffect(() => {
    const t = setTimeout(refresh, 0);
    return () => clearTimeout(t);
  }, [refresh]);

  const sel = corpus?.find((r) => r.id === selected) ?? null;

  const select = (id: string) => {
    setSelected(id);
    const r = corpus?.find((x) => x.id === id);
    setEditText(r?.texte ?? "");
    setSuggestion(null);
  };

  /** Lance l'analyse d'impact d'une action (SSE, boîte de verre en direct). */
  const analyze = useCallback(async (action: Record<string, unknown>) => {
    if (running) return;
    setRunning(true);
    setAgents([]);
    setVerdict(null);
    setPendingAction(action);
    setRationale("");
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

  /** Applique l'action en attente (après verdict). */
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

  const impacted = new Set(verdict?.impacted ?? []);
  const flagged = new Set(audit?.flagged_ids ?? []);
  const blocked = verdict?.verdict === "BLOQUANT";

  return (
    <div className="flex flex-col gap-4">
      {error && <Banner tone="bad">{error}</Banner>}

      {/* Barre corpus. */}
      <div className="flex flex-wrap items-center gap-3 text-xs text-fg-muted">
        <span className="flex items-center gap-1.5">
          <Dot tone={llmOk ? "good" : "warn"} />
          {corpus.length} exigences · {llmOk ? "agents IA prêts" : "LLM indisponible (règles seules)"}
        </span>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={semantic} onChange={(e) => setSemantic(e.target.checked)}
                 className="accent-(--accent)" />
          Analyse approfondie (IA)
          <Hint text="Décoché : seules les règles déterministes tournent (instantané, sans LLM)." />
        </label>
        <span className="ml-auto flex items-center gap-2">
          <input ref={fileRef} type="file" accept=".json" multiple hidden
                 onChange={(e) => upload(e.target.files)} />
          <button onClick={() => fileRef.current?.click()}
                  className="cursor-pointer rounded-md border border-edge px-2 py-1 text-fg-muted hover:text-foreground">
            Importer (JSON)
          </button>
          <button
            onClick={async () => {
              await fetch(`${API_BASE}/api/lynx/corpus/reset`, { method: "POST" }).catch(() => null);
              setSelected(null); setVerdict(null); setAudit(null);
              await refresh();
            }}
            title="Abandonne la matrice de travail et recharge la matrice d'origine."
            className="cursor-pointer rounded-md border border-edge px-2 py-1 text-fg-muted hover:text-foreground"
          >
            Réinitialiser
          </button>
        </span>
      </div>

      {/* Graphe + panneau. */}
      <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
        <ReqGraph corpus={corpus} selected={selected} impacted={impacted}
                  flagged={flagged} onSelect={select} />

        <div className="rounded-xl border border-edge bg-surface p-4">
          {!sel ? (
            <p className="text-xs text-fg-muted">
              Cliquez une exigence dans le graphe pour l&apos;inspecter, la modifier,
              la relier ou la corriger.
            </p>
          ) : (
            <div className="space-y-3">
              <p className="font-mono text-sm text-foreground">{sel.id}</p>
              <p className="text-[11px] text-fg-faint">
                Niveau L{sel.niveau} · {sel.domaine ?? "Général"} · test {sel.test_status ?? "PENDING"}
              </p>
              <textarea
                value={editText}
                onChange={(e) => setEditText(e.target.value)}
                rows={4}
                className="w-full rounded-lg border border-edge bg-surface-2 px-3 py-2 text-xs leading-relaxed text-foreground focus:border-accent focus:outline-none"
              />
              <div className="flex flex-wrap gap-2">
                <button
                  onClick={() => analyze({ action_type: "UPDATE", target_id: sel.id,
                                           new_text: editText })}
                  disabled={running}
                  className="cursor-pointer rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-background hover:bg-accent-bright disabled:opacity-40"
                >
                  Analyser la modification
                </button>
                <button
                  onClick={() => analyze({ action_type: "DELETE", target_id: sel.id })}
                  disabled={running}
                  title="Analyse l'impact d'une suppression avant de l'appliquer."
                  className="cursor-pointer rounded-lg border border-bad/40 px-3 py-1.5 text-xs text-bad hover:bg-bad/15 disabled:opacity-40"
                >
                  Supprimer…
                </button>
                <button
                  onClick={suggest}
                  disabled={suggesting || !llmOk}
                  title="Demande à l'agent de rédaction une version corrigée (1 appel LLM)."
                  className="cursor-pointer rounded-lg border border-edge px-3 py-1.5 text-xs text-fg-muted hover:text-foreground disabled:opacity-40"
                >
                  {suggesting ? "Suggestion…" : "Suggérer une correction"}
                </button>
              </div>

              {suggestion && (
                <div className="rounded-lg border border-edge bg-surface-2 px-3 py-2 text-xs">
                  {suggestion.error ? (
                    <p className="text-bad">Suggestion impossible : {suggestion.error}</p>
                  ) : (
                    <>
                      <p className="text-[11px] uppercase tracking-[0.14em] text-fg-faint">
                        Proposition de l&apos;agent rédaction
                      </p>
                      <p className="mt-1 text-fg-muted">{suggestion.texte}</p>
                      {suggestion.justification && (
                        <p className="mt-1 text-[11px] text-fg-faint">
                          {suggestion.justification}
                        </p>
                      )}
                      <button
                        onClick={() => { setEditText(suggestion.texte ?? editText);
                                         setSuggestion(null); }}
                        className="mt-2 cursor-pointer rounded-md border border-accent/50 px-2 py-1 text-accent-bright hover:bg-accent/10"
                      >
                        Reprendre ce texte → puis « Analyser la modification »
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
                <div className="mt-2 space-y-1 text-xs text-fg-muted">
                  {sel.parent_id && <p>mère — {sel.parent_id} (principal)</p>}
                  {(sel.links ?? []).map((lk, i) => (
                    <p key={i} className="flex items-center justify-between gap-2">
                      {lk.type} → {lk.target}
                      <button
                        onClick={() => analyze({ action_type: "UNLINK", target_id: sel.id,
                                                 link_target: lk.target, link_type: lk.type })}
                        className="cursor-pointer text-[11px] text-fg-faint hover:text-bad"
                      >
                        retirer
                      </button>
                    </p>
                  ))}
                  <LinkForm sel={sel} corpus={corpus} disabled={running} onLink={analyze} />
                </div>
              </details>
            </div>
          )}
        </div>
      </div>

      {/* Analyse en cours / verdict. */}
      {(running || verdict) && (
        <div className="rounded-xl border border-edge bg-surface px-4 py-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-fg-faint">
            {agents.map((a) => (
              <span key={a.label} className="flex items-center gap-1.5">
                <Dot tone={a.done ? "good" : "accent"} pulse={!a.done} />
                {a.label}
              </span>
            ))}
            {running && !agents.length && <><Spinner /> Analyse…</>}
          </div>
          {verdict?.verdict && (
            <div className="mt-2 space-y-2">
              <p className="flex items-center gap-3">
                <VerdictWord v={verdict.verdict} />
                {verdict.impacted.length > 0 && (
                  <span className="text-[11px] text-fg-faint">
                    {verdict.impacted.length} exigence(s) impactée(s)
                  </span>
                )}
              </p>
              {verdict.message && (
                <div className={`chat-md text-xs text-fg-muted ${running ? "stream-caret" : ""}`}>
                  <ReactMarkdown>{verdict.message}</ReactMarkdown>
                </div>
              )}
              {verdict.findings.length > 0 && (
                <ul className="space-y-1 text-xs text-fg-muted">
                  {verdict.findings.map((f, i) => (
                    <li key={i} className="flex items-start gap-2">
                      <span className="mt-1"><Dot tone={SEV_TONE[f.sev] ?? "neutral"} /></span>
                      <span><span className="text-fg-faint">{f.scope}</span> — {f.msg}</span>
                    </li>
                  ))}
                </ul>
              )}
              <GlassBox exchanges={verdict.exchanges}
                        title="Comment LynX a raisonné — boîte de verre" />
              {!running && pendingAction && (
                <div className="flex flex-wrap items-center gap-2 border-t border-edge pt-2">
                  {blocked && (
                    <input
                      value={rationale}
                      onChange={(e) => setRationale(e.target.value)}
                      placeholder="Justification du passage en force (obligatoire)…"
                      className="flex-1 rounded-lg border border-bad/40 bg-surface-2 px-2 py-1 text-xs text-foreground placeholder:text-fg-faint focus:outline-none"
                    />
                  )}
                  <button
                    onClick={apply}
                    disabled={blocked && !rationale.trim()}
                    className="cursor-pointer rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-background hover:bg-accent-bright disabled:opacity-40"
                  >
                    {blocked ? "Appliquer malgré le blocage" : "Appliquer à la matrice"}
                  </button>
                  <button
                    onClick={() => { setVerdict(null); setPendingAction(null); }}
                    className="cursor-pointer rounded-lg border border-edge px-3 py-1.5 text-xs text-fg-muted hover:text-foreground"
                  >
                    Annuler
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Audit de la matrice. */}
      <div className="rounded-xl border border-edge bg-surface px-4 py-3">
        <div className="flex flex-wrap items-center gap-3">
          <h3 className="text-sm font-semibold text-foreground">Audit de la matrice</h3>
          <label className="flex items-center gap-1.5 text-xs text-fg-muted">
            <input type="checkbox" checked={deep} onChange={(e) => setDeep(e.target.checked)}
                   className="accent-(--accent)" />
            Audit IA par exigence
            <Hint text="Décoché : règles structurelles et doublons vectoriels seulement (rapide)." />
          </label>
          <button
            onClick={runAudit}
            disabled={auditRunning}
            className="ml-auto cursor-pointer rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-background hover:bg-accent-bright disabled:opacity-40"
          >
            {auditRunning ? "Audit en cours…" : "Auditer"}
          </button>
        </div>
        {auditRunning && (
          <p className="mt-2 flex items-center gap-2 text-xs text-fg-muted">
            <Spinner />
            {auditProgress ? `${auditProgress[0]}/${auditProgress[1]} exigences auditées`
                           : "Règles structurelles et doublons vectoriels…"}
          </p>
        )}
        {audit && (
          <div className="mt-2 space-y-2">
            <p className="text-xs text-fg-muted">
              Matrice ▸ Règles structurelles · Doublons vectoriels
              {deep ? ` · ${audit.n} audits IA` : ""} ▸ Score{" "}
              <span className={`font-mono font-bold ${
                audit.score >= 80 ? "text-good" : audit.score >= 50 ? "text-warn" : "text-bad"}`}>
                {audit.score}/100
              </span>
            </p>
            {audit.flagged_ids.length === 0 ? (
              <p className="flex items-center gap-2 text-xs text-fg-muted">
                <Dot tone="good" /> Aucune exigence signalée — {audit.n} conformes.
              </p>
            ) : (
              <>
                <p className="flex items-center gap-2 text-xs text-fg-muted">
                  <Dot tone={audit.findings.some((f) => f.severity === "BLOQUANT") ? "bad" : "warn"} />
                  {audit.flagged_ids.length} exigence(s) en défaut —{" "}
                  {audit.n - audit.flagged_ids.length} conformes
                  {audit.n_non_audite > 0 && ` · ${audit.n_non_audite} non auditées`}
                </p>
                <div className="space-y-1">
                  {audit.flagged_ids.map((rid) => (
                    <details key={rid} className="chat-details">
                      <summary className="text-xs">
                        <button
                          onClick={(e) => { e.preventDefault(); select(rid); }}
                          className="cursor-pointer font-mono hover:text-accent-bright"
                        >
                          {rid}
                        </button>
                        <span className="text-fg-faint">
                          · {audit.findings.filter((f) => f.req_id === rid).length} constat(s)
                        </span>
                      </summary>
                      <ul className="mt-1 space-y-1 text-xs text-fg-muted">
                        {audit.findings.filter((f) => f.req_id === rid).map((f, i) => (
                          <li key={i} className="flex items-start gap-2">
                            <span className="mt-1"><Dot tone={SEV_TONE[f.severity] ?? "neutral"} /></span>
                            <span><span className="text-fg-faint">{f.axis}</span> — {f.message}</span>
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
        className="cursor-pointer rounded-md border border-accent/50 px-2 py-1 text-[11px] text-accent-bright hover:bg-accent/10 disabled:opacity-40"
      >
        Lier (DERIVE)
      </button>
    </div>
  );
}
