"use client";

/** Outil RAG — un assistant conversationnel local, façon produit de chat :
 * conversations persistées à gauche, fil au centre, composeur en bas
 * (pièce jointe, périmètre documentaire, mode, envoi/stop).
 * Boîte de verre : routage affiché, pipeline visible, raisonnement de
 * l'agent replié, sources et passages sous chaque réponse.
 *
 * Ce fichier porte la machine à états (SSE, sessions, pièce jointe) ;
 * l'affichage est découpé : blocks.tsx (réponse), sessions-sidebar.tsx
 * (colonne conversations), composer.tsx (saisie + options). */

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

import { API_BASE, getJSON, type SourcesResponse } from "@/lib/api";
import { streamAsk } from "@/lib/sse";
import { loadPrefs } from "@/lib/prefs";
import { useEngineHealth, useElapsedLabel } from "@/lib/use-health";
import type { ChatMessage, ChunkView, PlanStep, SessionInfo } from "@/lib/types";
import { useExpert } from "@/components/expert-toggle";
import { Dot, Spinner } from "@/components/ui";
import { AssistantMessage, NOT_FOUND_MESSAGE } from "@/components/chat/blocks";
import { AnswerMarkdown } from "@/components/chat/markdown";
import { SessionsSidebar } from "@/components/chat/sessions-sidebar";
import { Composer, type Mode } from "@/components/chat/composer";

const EXAMPLES = [
  "Quelles sont les exigences de chiffrement ?",
  "Quelles sont les menaces identifiées ?",
  "Résume les principales fonctions de sécurité.",
];

type Phase = "idle" | "retrieve" | "agent" | "generate";

/** Statut d'une étape du plan de l'agent : à venir, en cours, faite, ou sans
 * résultat (hors du périmètre documentaire). */
type StepStatus = "pending" | "active" | "done" | "vide";

/** Plan de recherche de l'agent, suivi en direct (événements plan/step/replan). */
type PlanView = { steps: PlanStep[]; statuts: StepStatus[]; replanned: boolean };

export function Chat() {
  const [docs, setDocs] = useState<{ name: string; chunks: number }[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [phase, setPhase] = useState<Phase>("idle");
  const [nChunks, setNChunks] = useState<number | null>(null);
  const [partial, setPartial] = useState<string>("");
  const [agentTrace, setAgentTrace] = useState<string[]>([]);
  const [plan, setPlan] = useState<PlanView | null>(null);
  const [route, setRoute] = useState<string>("");
  const [mode, setMode] = useState<Mode>("auto");
  /** Modèle de génération : changement à chaud + chargement VRAM (parité
   * Streamlit « Charger le modèle » depuis le chat). */
  const [models, setModels] = useState<string[]>([]);
  const [genModel, setGenModel] = useState("");
  const [modelStatus, setModelStatus] = useState<string | null>(null);
  const [input, setInput] = useState("");
  // Édition du dernier prompt : le texte revient dans le champ, l'envoi
  // remplace l'échange précédent (fil + session persistée).
  const [editing, setEditing] = useState(false);
  /** Indexation d'une pièce jointe : { name, pct, step } — bloque l'envoi. */
  const [attach, setAttach] = useState<{ name: string; pct: number; step: string } | null>(null);
  const [attachError, setAttachError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const expert = useExpert();
  const busy = phase !== "idle";
  const attaching = attach !== null;
  /** Moteur local sondé en continu : Ollama arrêté (ex. redémarrage du
   * service) => envoi suspendu, bannière avec le délai, reprise auto. */
  const engine = useEngineHealth();
  const engineDown = engine.kind === "down";
  const downFor = useElapsedLabel(engineDown ? engine.since : null);

  // Nettoyage du poll d'ingestion au démontage.
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const refreshDocs = useCallback(() => {
    getJSON<SourcesResponse>("/api/sources")
      .then((s) => setDocs(s.sources)).catch(() => setDocs([]));
  }, []);
  const refreshSessions = useCallback(() => {
    getJSON<{ sessions: SessionInfo[] }>("/api/sessions")
      .then((s) => setSessions(s.sessions)).catch(() => setSessions([]));
  }, []);

  useEffect(() => {
    const t = setTimeout(() => {
      refreshDocs();
      refreshSessions();
      getJSON<{ models: string[]; routing: Record<string, string> }>("/api/models")
        .then((m) => { setModels(m.models); setGenModel(m.routing?.generate ?? m.models[0] ?? ""); })
        .catch(() => setModels([]));
    }, 0);
    return () => clearTimeout(t);
  }, [refreshDocs, refreshSessions]);

  const loadModel = async (model: string) => {
    setGenModel(model);
    setModelStatus("chargement…");
    const res = await fetch(`${API_BASE}/api/models/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model, action: "load" }),
    }).catch(() => null);
    setModelStatus(res?.ok ? "chargé" : "échec du chargement");
    setTimeout(() => setModelStatus(null), 4000);
  };

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, partial, agentTrace]);

  const newConversation = () => {
    setSessionId(null);
    setMessages([]);
    setSelected("");
  };

  const openSession = async (s: SessionInfo) => {
    if (busy) return;
    try {
      const d = await getJSON<{ source_filter: string | null; messages: ChatMessage[] }>(
        `/api/sessions/${s.id}/messages`);
      setSessionId(s.id);
      setSelected(d.source_filter ?? "");
      setMessages(d.messages.map((m) => ({ ...m })));
    } catch { /* session disparue */ }
  };

  const ask = useCallback(async (question: string, base?: ChatMessage[]) => {
    const q = question.trim();
    if (!q || busy) return;
    setInput("");
    const prefs = loadPrefs();
    // `base` : fil de départ imposé (édition du dernier prompt — l'échange
    // remplacé ne doit pas réapparaître dans l'historique envoyé au moteur).
    const ms0 = base ?? messages;
    const history = prefs.useMemory
      ? ms0.map((m) => ({ role: m.role, content: m.content }))
      : [];
    setMessages([...ms0, { role: "user", content: q }]);
    setPhase("retrieve");
    setNChunks(null);
    setPartial("");
    setAgentTrace([]);
    setPlan(null);
    setRoute("");

    const draft: ChatMessage = { role: "assistant", content: "" };
    let chunks: ChunkView[] = [];
    let gotDone = false;
    let pushed = false; // le message a-t-il déjà été ajouté au fil ?
    const trace: string[] = [];
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamAsk({
        question: q, source: selected || null, history,
        parent_child: prefs.parentChild, self_rag: prefs.selfRag,
        system_prompt: prefs.systemPrompt, mode, session_id: sessionId,
      }, (ev) => {
        if (ev.type === "session") {
          setSessionId(ev.id);
        } else if (ev.type === "route") {
          draft.route = `${ev.mode.toUpperCase()} — ${ev.reason}`;
          setRoute(draft.route);
          if (ev.mode === "agent") setPhase("agent");
        } else if (ev.type === "stage" && ev.stage === "generate") setPhase("generate");
        else if (ev.type === "retrieved") {
          chunks = ev.chunks;
          setNChunks(ev.chunks.length);
        } else if (ev.type === "plan") {
          setPlan({ steps: ev.steps, statuts: ev.steps.map(() => "pending"),
                    replanned: false });
          trace.push(`**Plan** (${ev.steps.length} étapes)\n\n${ev.steps
            .map((s, i) => `${i + 1}. ${s.sous_question}`).join("\n")}`);
          setAgentTrace([...trace]);
        } else if (ev.type === "step_start") {
          setPlan((p) => p && {
            ...p,
            statuts: p.statuts.map((st, i) => (i === ev.index - 1 ? "active" : st)),
          });
          trace.push(`→ **Étape ${ev.index}/${ev.total}** — ${ev.text}`);
          setAgentTrace([...trace]);
        } else if (ev.type === "step_done") {
          setPlan((p) => p && {
            ...p,
            statuts: p.statuts.map((st, i) =>
              i === ev.index - 1 ? (ev.hors_scope ? "vide" : "done") : st),
          });
          trace.push(`_${ev.text}_`);
          setAgentTrace([...trace]);
        } else if (ev.type === "replan") {
          // Les étapes restantes ont été révisées : liste complète re-reçue,
          // les statuts des étapes déjà exécutées sont conservés.
          setPlan((p) => ({
            steps: ev.steps,
            statuts: ev.steps.map((_, i) =>
              i < ev.index ? (p?.statuts[i] ?? "done") : "pending"),
            replanned: true,
          }));
          trace.push("**Re-planification** — les étapes restantes ont été révisées.");
          setAgentTrace([...trace]);
        } else if (ev.type === "thought") {
          trace.push(`**Pensée** — ${ev.text}`);
          setAgentTrace([...trace]);
        } else if (ev.type === "action") {
          trace.push(`→ **Recherche** \`${ev.text}\``);
          setAgentTrace([...trace]);
        } else if (ev.type === "observation") {
          trace.push(`_${ev.text}_`);
          setAgentTrace([...trace]);
        } else if (ev.type === "token") {
          setPhase("generate"); // no-op si déjà en génération
          draft.content += ev.text;
          setPartial(draft.content);
        } else if (ev.type === "sources") draft.citations = ev.citations;
        else if (ev.type === "attribution") {
          // Attribution par affirmation : arrive APRÈS done (passe post-hoc)
          // -> mise à jour du dernier message (marqueurs déjà affichés).
          const { type: _t, ...attribution } = ev;
          void _t;
          draft.attribution = attribution;
          setMessages((ms) => {
            const last = ms[ms.length - 1];
            if (last?.role === "assistant") {
              return [...ms.slice(0, -1), { ...last, attribution }];
            }
            return ms;
          });
        } else if (ev.type === "eval") {
          draft.eval = ev;
          setMessages((ms) => {
            // l'éval arrive APRÈS done : mise à jour du dernier message
            const last = ms[ms.length - 1];
            if (last?.role === "assistant") {
              return [...ms.slice(0, -1), { ...last, eval: ev }];
            }
            return ms;
          });
        } else if (ev.type === "done") {
          gotDone = true;
          pushed = true;
          if (!ev.found && !draft.content) draft.content = NOT_FOUND_MESSAGE;
          if (trace.length) draft.reasoning = trace.join("\n\n");
          if (chunks.length) draft.chunks = chunks;
          setMessages((ms) => [...ms, { ...draft }]);
          setPartial("");
          setAgentTrace([]);
          setPlan(null);
          setPhase("idle");
          refreshSessions(); // le titre/updated_at ont pu changer
        } else if (ev.type === "error") {
          gotDone = true;
          draft.error = `Une erreur est survenue : ${ev.message}`;
        }
      }, controller.signal);
      if (!gotDone) {
        draft.error = "Le flux s'est interrompu avant la fin. Réponse partielle affichée.";
      }
    } catch (e) {
      if (controller.signal.aborted) draft.stopped = true;
      else draft.error = `L'API locale est injoignable (${String(e)}). Lancer : python serve.py --web`;
    }
    abortRef.current = null;
    // Si done n'a pas déjà poussé le message (stop, coupure, erreur transport).
    if (!pushed) {
      if (chunks.length) draft.chunks = chunks;
      if (trace.length) draft.reasoning = trace.join("\n\n");
      setMessages((ms) => [...ms, { ...draft }]);
    }
    setPartial("");
    setAgentTrace([]);
    setPlan(null);
    setPhase("idle");
  }, [busy, messages, selected, mode, sessionId, refreshSessions]);

  // Index du dernier message utilisateur (siège du bouton « modifier »).
  const lastUserIndex = messages.reduce(
    (acc, m, i) => (m.role === "user" ? i : acc), -1);

  /** Envoi : en mode édition, l'échange précédent (question + réponse) est
   * retiré du fil ET de la session persistée avant de re-poser la question. */
  const send = useCallback(async (q: string) => {
    if (!editing) return ask(q);
    setEditing(false);
    const base = lastUserIndex >= 0 ? messages.slice(0, lastUserIndex) : messages;
    if (sessionId) {
      // Attendre la troncature : /api/ask ré-appendra la question éditée.
      await fetch(`${API_BASE}/api/sessions/${sessionId}/last-exchange`,
                  { method: "DELETE" }).catch(() => null);
    }
    return ask(q, base);
  }, [editing, ask, lastUserIndex, messages, sessionId]);

  const regenerate = async (selectedChunks: ChunkView[]) => {
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    if (!lastUser || busy || !selectedChunks.length) return;
    setPhase("generate");
    setPartial("");
    try {
      const res = await fetch(`${API_BASE}/api/regenerate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: lastUser.content, chunks: selectedChunks,
                               system_prompt: loadPrefs().systemPrompt,
                               session_id: sessionId }),
      });
      const d = await res.json();
      setMessages((ms) => {
        const i = ms.map((m) => m.role).lastIndexOf("assistant");
        if (i < 0) return ms;
        const next = [...ms];
        // d.chunks = la sélection APRÈS affinage pré-génération : c'est la
        // liste numérotée [1..n] du contexte (contrat marqueur↔passage).
        next[i] = { ...next[i], content: d.answer, citations: d.citations,
                    chunks: d.chunks ?? selectedChunks, eval: undefined,
                    attribution: undefined };
        return next;
      });
    } catch { /* silencieux : le message existant reste */ }
    setPhase("idle");
  };

  /** Pièce jointe façon chatbot : réglages par défaut, barre de progression
   * visible, envoi BLOQUÉ tant que l'indexation tourne, puis le périmètre est
   * automatiquement fixé sur le document ajouté — via son NOM DE SOURCE réel
   * en base (un PDF devient <nom>-clean.md). Réglages fins : onglet
   * Documents. Suit TOUT le lot déposé ; abandon propre si l'API redémarre. */
  const attachFiles = async (files: FileList | null) => {
    if (!files?.length || attaching) return;
    const names = Array.from(files).map((f) => f.name);
    const label = names.length > 1 ? `${names[0]} (+${names.length - 1})` : names[0];
    setAttachError(null);
    setAttach({ name: label, pct: 0, step: "Dépôt du document…" });
    const d = await getJSON<{ params: Record<string, unknown> }>("/api/ingest/defaults")
      .catch(() => null);
    const p = d?.params ?? { nkw: 5, nq: 3, mode: "technical", raptor: true, enh_model: "" };
    const fd = new FormData();
    Array.from(files).forEach((f) => fd.append("files", f));
    fd.append("nkw", String(p.nkw));
    fd.append("nq", String(p.nq));
    fd.append("mode", String(p.mode));
    fd.append("raptor", String(p.raptor));
    fd.append("enh_model", String(p.enh_model ?? ""));
    const res = await fetch(`${API_BASE}/api/documents`, { method: "POST", body: fd })
      .catch(() => null);
    if (fileRef.current) fileRef.current.value = "";
    if (!res?.ok) {
      setAttach(null);
      setAttachError(`Dépôt impossible pour « ${label} » — type non accepté ou API indisponible.`);
      return;
    }
    // Suivi de TOUTES les tâches du lot jusqu'au bout.
    let misses = 0;
    type Job = { name: string; status: string; pct: number; step: string;
                 message: string | null; source_name: string | null };
    pollRef.current = setInterval(async () => {
      const s = await getJSON<{ active: boolean; jobs: Job[] }>("/api/ingest/status")
        .catch(() => null);
      const jobs = names
        .map((n) => s?.jobs.filter((j) => j.name === n).at(-1))
        .filter((j): j is Job => !!j);
      if (!s || jobs.length < names.length) {
        // File en mémoire disparue (API redémarrée ?) : ne pas bloquer à vie.
        if (++misses >= 5) {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          setAttach(null);
          setAttachError(
            "Suivi d'indexation perdu (API redémarrée ?) — vérifiez l'onglet Documents.");
        }
        return;
      }
      misses = 0;
      const pending = jobs.filter((j) => j.status === "queued" || j.status === "running");
      if (pending.length) {
        const cur = pending.find((j) => j.status === "running") ?? pending[0];
        const done = jobs.length - pending.length;
        const pct = Math.round((100 * done + cur.pct) / jobs.length);
        setAttach({ name: label, pct, step: cur.step });
        return;
      }
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
      setAttach(null);
      const ok = jobs.filter((j) => j.status === "success");
      const failed = jobs.filter((j) => j.status === "error");
      if (ok.length) {
        refreshDocs();
        // Le prompt suivant porte sur LE document ajouté (source réelle) —
        // seulement si le lot n'en contient qu'un (sinon : tous les documents).
        if (ok.length === 1 && !failed.length) {
          setSelected(ok[0].source_name ?? ok[0].name);
        }
      }
      if (failed.length) {
        setAttachError(failed
          .map((j) => `Indexation de « ${j.name} » échouée : ${j.message ?? "erreur."}`)
          .join(" — "));
      }
    }, 1200);
  };

  return (
    <div className="flex h-[calc(100vh-10.5rem)] min-h-105 gap-4">
      {/* Conversations. */}
      <SessionsSidebar
        sessions={sessions}
        sessionId={sessionId}
        onNew={newConversation}
        onOpen={openSession}
        refreshSessions={refreshSessions}
      />

      {/* Fil de conversation. */}
      <section className="flex min-w-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pb-3 pr-1">
          {messages.length === 0 && !busy && (
            <div className="rise-in flex h-full flex-col items-center justify-center text-center">
              <p className="text-base font-medium text-foreground">
                Posez une question sur vos documents
              </p>
              <p className="mt-1 text-xs text-fg-muted">
                Réponses sourcées, citant les passages de vos documents — tout reste sur
                cette machine.
              </p>
              <div className="mt-4 flex flex-wrap justify-center gap-2">
                {EXAMPLES.map((ex) => (
                  <button
                    key={ex}
                    onClick={() => ask(ex)}
                    className="cursor-pointer rounded-full border border-edge bg-surface-2 px-3 py-1 text-xs text-fg-muted transition-colors hover:border-accent/60 hover:text-foreground"
                  >
                    {ex}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m, i) =>
            m.role === "user" ? (
              <div key={i} className="group ml-auto flex max-w-[85%] items-center gap-2">
                {i === lastUserIndex && !busy && (
                  <button
                    onClick={() => { setInput(m.content); setEditing(true); }}
                    title="Modifier ce message et régénérer la réponse (l'échange actuel sera remplacé)"
                    className="cursor-pointer text-[11px] text-fg-faint opacity-0 transition-opacity group-hover:opacity-100 hover:text-foreground"
                  >
                    modifier
                  </button>
                )}
                <p className="rounded-2xl bg-accent/15 px-4 py-2.5 text-sm text-foreground">
                  {m.content}
                </p>
              </div>
            ) : (
              <div key={i} className="rounded-2xl border border-edge bg-surface px-4 py-3">
                <AssistantMessage
                  m={m} expert={expert}
                  canRegenerate={i === messages.length - 1 && !busy}
                  onRegenerate={regenerate}
                />
              </div>
            ),
          )}

          {/* Réponse en cours. */}
          {busy && (
            <div className="rounded-2xl border border-edge bg-surface px-4 py-3">
              <div className="mb-2 flex items-start justify-between gap-3">
                <div className="min-w-0 text-xs text-fg-faint">
                  {route && expert && <p className="mb-1">Routage : {route}</p>}
                  <p className="flex flex-wrap items-center gap-2">
                    {phase === "agent" ? (
                      <><Dot tone="accent" pulse />
                        {plan ? "Exécution du plan" : "Agent en raisonnement"}</>
                    ) : (
                      <>
                        <Dot tone={phase === "retrieve" ? "accent" : "good"}
                             pulse={phase === "retrieve"} />
                        Recherche
                        <span>▸</span>
                        {nChunks === null ? "…" : `${nChunks} passage${nChunks > 1 ? "s" : ""}`}
                        <span>▸</span>
                        <Dot tone={phase === "generate" ? "accent" : "neutral"}
                             pulse={phase === "generate"} />
                        Génération
                      </>
                    )}
                  </p>
                </div>
                {/* Un seul Stop : celui du composer (le bouton d'envoi devient
                    un carré pendant la génération) — doublon retiré ici. */}
              </div>
              {/* Plan de recherche de l'agent : étapes cochées en direct. */}
              {plan && (
                <div className="mb-2 rounded-lg border border-edge bg-surface-2 px-3 py-2">
                  <p className="text-[11px] uppercase tracking-[0.14em] text-fg-faint">
                    Plan de recherche
                    {plan.replanned && (
                      <span className="ml-2 normal-case tracking-normal text-warn">
                        révisé en cours de route
                      </span>
                    )}
                  </p>
                  <ul className="mt-1.5 space-y-1 text-xs">
                    {plan.steps.map((s, i) => {
                      const st = plan.statuts[i] ?? "pending";
                      return (
                        <li key={i} className="flex items-baseline gap-2">
                          <Dot
                            tone={st === "done" ? "good" : st === "vide" ? "warn"
                              : st === "active" ? "accent" : "neutral"}
                            pulse={st === "active"}
                          />
                          <span className={
                            st === "active" ? "text-foreground"
                              : st === "pending" ? "text-fg-faint" : "text-fg-muted"
                          }>
                            {s.sous_question}
                            {st === "done" && <span className="ml-1.5 text-good">✓</span>}
                            {st === "vide" && (
                              <span className="ml-1.5 text-warn">sans résultat</span>
                            )}
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}
              {agentTrace.length > 0 && (
                <div className="chat-md mb-2 border-l-2 border-edge pl-3 text-xs text-fg-muted">
                  <ReactMarkdown>{agentTrace.join("\n\n")}</ReactMarkdown>
                </div>
              )}
              {partial ? (
                <div className="chat-md stream-caret text-sm leading-relaxed">
                  {/* Marqueurs [n] stylés dès le streaming (cliquables une
                      fois la réponse finalisée, avec ses passages). */}
                  <AnswerMarkdown content={partial} maxCite={nChunks ?? 0} />
                </div>
              ) : (
                <p className="flex items-center gap-2 text-xs text-fg-muted">
                  <Spinner />
                  {phase === "retrieve" ? "Recherche dans les documents…"
                    : phase === "agent" ? "L'agent réfléchit…" : "Génération…"}
                </p>
              )}
            </div>
          )}
          <div ref={endRef} />
        </div>

        {engineDown && (
          <div className="mb-2 flex items-center gap-2 rounded-xl border border-warn/40 bg-surface-2 px-3 py-2 text-xs text-fg-muted">
            <Dot tone="warn" pulse />
            <span>
              {engine.reason} — l&apos;envoi est suspendu depuis {downFor}.
              Il sera réactivé automatiquement au retour du service.
            </span>
          </div>
        )}
        {editing && (
          <div className="mb-2 flex items-center gap-2 rounded-xl border border-accent/40 bg-surface-2 px-3 py-2 text-xs text-fg-muted">
            <span>
              Modification du dernier message — à l&apos;envoi, la question et sa réponse
              actuelles seront remplacées.
            </span>
            <button
              onClick={() => { setEditing(false); setInput(""); }}
              className="ml-auto cursor-pointer text-fg-faint transition-colors hover:text-foreground"
            >
              Annuler
            </button>
          </div>
        )}
        <Composer
          input={input} setInput={setInput}
          disabled={engineDown}
          busy={busy} attaching={attaching}
          attach={attach} attachError={attachError}
          onDismissError={() => setAttachError(null)}
          onAsk={send}
          onStop={() => abortRef.current?.abort()}
          fileRef={fileRef} onAttachFiles={attachFiles}
          selected={selected} setSelected={setSelected} docs={docs}
          models={models} genModel={genModel} onLoadModel={loadModel}
          modelStatus={modelStatus}
          mode={mode} setMode={setMode} expert={expert}
        />
      </section>
    </div>
  );
}
