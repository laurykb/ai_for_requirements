"use client";

/** Outil RAG — un assistant conversationnel local, façon produit de chat :
 * conversations persistées à gauche, fil au centre, composeur en bas
 * (pièce jointe, périmètre documentaire, mode, envoi/stop).
 * Boîte de verre : routage affiché, pipeline visible, raisonnement de
 * l'agent replié, sources et passages sous chaque réponse. */

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

import { API_BASE, getJSON, type SourcesResponse } from "@/lib/api";
import { streamAsk } from "@/lib/sse";
import { fmt } from "@/lib/format";
import { loadPrefs } from "@/lib/prefs";
import type { ChatMessage, ChunkView, Citation, EvalResult, SessionInfo } from "@/lib/types";
import { useExpert } from "@/components/expert-toggle";
import { Banner, Dot, Hint, Spinner } from "@/components/ui";

const EXAMPLES = [
  "Quelles sont les exigences de chiffrement ?",
  "Quelles sont les menaces identifiées ?",
  "Résume les principales fonctions de sécurité.",
];

const NOT_FOUND_MESSAGE =
  "Je n'ai pas trouvé d'information sur ce sujet dans vos documents. " +
  "Essayez de reformuler, ou choisissez un autre document à interroger.";

type Phase = "idle" | "retrieve" | "agent" | "generate";

/** Libellé d'un passage : source – section – page – score. */
function chunkLabel(c: ChunkView, i: number): string {
  const m = c.meta;
  const loc =
    m.heading ?? m.breadcrumb ?? (m.section_idx != null ? `section ${m.section_idx}` : "");
  const page = m.page_number ? ` – p. ${m.page_number}` : "";
  const score = typeof c.ce_score === "number" ? ` – score ${fmt(c.ce_score)}` : "";
  return `[${i + 1}] ${m.source ?? "document"}${loc ? ` – ${loc}` : ""}${page}${score}`;
}

function SourcesBlock({ citations }: { citations: Citation[] }) {
  if (!citations.length) return null;
  return (
    <details className="chat-details">
      <summary>Sources ({citations.length})</summary>
      <ul className="mt-2 space-y-1 text-xs text-fg-muted">
        {citations.map((c) => (
          <li key={c.idx}>
            <span className="font-mono text-fg-faint">[{c.idx}]</span> {c.source}
            {" – "}
            {c.heading ?? c.breadcrumb ?? `section ${c.section ?? "?"}`}
            {c.page ? ` – p. ${c.page}` : ""}
          </li>
        ))}
      </ul>
    </details>
  );
}

/** Passages récupérés (boîte de verre). Sur la DERNIÈRE réponse : cases à
 * cocher + « Régénérer avec la sélection ». */
function ChunksBlock({ chunks, canRegenerate, onRegenerate }: {
  chunks: ChunkView[];
  canRegenerate?: boolean;
  onRegenerate?: (selected: ChunkView[]) => void;
}) {
  const [checked, setChecked] = useState<boolean[]>(() => chunks.map(() => true));
  if (!chunks.length) return null;
  return (
    <details className="chat-details">
      <summary>
        Passages récupérés ({chunks.length})
        <Hint text="Le contenu exact que le moteur de recherche a retrouvé et fourni au modèle pour répondre." />
      </summary>
      <div className="mt-2 space-y-2">
        {chunks.map((c, i) => (
          <div key={i} className="flex items-start gap-2">
            {canRegenerate && (
              <input
                type="checkbox"
                checked={checked[i] ?? true}
                onChange={(e) =>
                  setChecked((cs) => cs.map((v, j) => (j === i ? e.target.checked : v)))}
                className="mt-2.5 accent-(--accent)"
                aria-label={`garder le passage ${i + 1}`}
              />
            )}
            <details className="chat-details min-w-0 flex-1">
              <summary className="text-xs">{chunkLabel(c, i)}</summary>
              <div className="chat-md mt-2 max-h-72 overflow-y-auto text-xs text-fg-muted">
                <ReactMarkdown>{c.doc}</ReactMarkdown>
              </div>
              {(c.meta.keywords_str || c.meta.entities_str) && (
                <p className="mt-2 border-t border-edge pt-2 text-[11px] text-fg-faint">
                  {c.meta.keywords_str && <>Mots-clés – {c.meta.keywords_str}</>}
                  {c.meta.keywords_str && c.meta.entities_str && <br />}
                  {c.meta.entities_str && <>Entités – {c.meta.entities_str}</>}
                </p>
              )}
            </details>
          </div>
        ))}
        {canRegenerate && onRegenerate && (
          <button
            onClick={() => onRegenerate(chunks.filter((_, i) => checked[i] ?? true))}
            className="cursor-pointer rounded-md border border-accent/50 px-2 py-1 text-[11px] text-accent-bright transition-colors hover:bg-accent/10"
          >
            Régénérer avec la sélection
          </button>
        )}
      </div>
    </details>
  );
}

function EvalBlock({ e }: { e: EvalResult }) {
  return (
    <div className="mt-2 rounded-lg border border-edge bg-surface-2 px-3 py-2 text-xs">
      <p className="flex items-center gap-1.5 text-[11px] uppercase tracking-[0.14em] text-fg-faint">
        Vérification automatique
        <Hint text="Question à enjeu détectée : fidélité aux sources contrôlée par LLM-as-judge (0 → 1)." />
      </p>
      <div className="mt-1.5 flex flex-wrap gap-x-5 gap-y-1 font-mono tabular-nums text-fg-muted">
        <span>Fidélité {fmt(e.faithfulness ?? 0)}</span>
        <span>Pertinence réponse {fmt(e.answer_relevance ?? 0)}</span>
        <span>Pertinence contexte {fmt(e.context_relevance ?? 0)}</span>
      </div>
      {(e.issues?.length ?? 0) > 0 && (
        <ul className="mt-1.5 list-disc pl-4 text-fg-faint">
          {e.issues!.map((it, i) => <li key={i}>{it}</li>)}
        </ul>
      )}
    </div>
  );
}

function AssistantMessage({ m, expert, canRegenerate, onRegenerate }: {
  m: ChatMessage; expert: boolean;
  canRegenerate?: boolean;
  onRegenerate?: (selected: ChunkView[]) => void;
}) {
  return (
    <div className="min-w-0">
      {m.route && expert && (
        <p className="mb-1 text-[11px] text-fg-faint">Routage : {m.route}</p>
      )}
      {m.reasoning && (
        <details className="chat-details mb-2">
          <summary>Raisonnement de l&apos;agent</summary>
          <div className="chat-md mt-2 text-xs text-fg-muted">
            <ReactMarkdown>{m.reasoning}</ReactMarkdown>
          </div>
        </details>
      )}
      <div className="chat-md text-sm leading-relaxed">
        <ReactMarkdown>{m.content}</ReactMarkdown>
      </div>
      {m.stopped && (
        <p className="mt-2 flex items-center gap-2 text-xs text-fg-faint">
          <Dot tone="warn" /> Génération arrêtée — réponse partielle.
        </p>
      )}
      {m.error && (
        <div className="mt-2">
          <Banner tone="bad">{m.error}</Banner>
        </div>
      )}
      {m.citations && <SourcesBlock citations={m.citations} />}
      {expert && m.chunks && (
        <ChunksBlock chunks={m.chunks} canRegenerate={canRegenerate}
                     onRegenerate={onRegenerate} />
      )}
      {m.eval && <EvalBlock e={m.eval} />}
    </div>
  );
}

/** Une conversation dans la barre latérale (renommage inline, suppression). */
function SessionRow({ s, active, onOpen, onRename, onDelete }: {
  s: SessionInfo; active: boolean;
  onOpen: () => void; onRename: (t: string) => void; onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(s.title);
  return (
    <div
      className={`group flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs transition-colors ${
        active ? "bg-accent/15 text-foreground" : "text-fg-muted hover:bg-surface-2"}`}
    >
      {editing ? (
        <input
          autoFocus
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onBlur={() => { setEditing(false); onRename(title); }}
          onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
          className="w-full rounded border border-accent/50 bg-surface px-1 py-0.5 text-xs text-foreground focus:outline-none"
        />
      ) : (
        <>
          <button onClick={onOpen}
                  className="min-w-0 flex-1 cursor-pointer truncate text-left"
                  title={s.title}>
            {s.title || "Conversation"}
          </button>
          <button onClick={() => setEditing(true)} title="Renommer"
                  className="hidden cursor-pointer px-1 text-fg-faint hover:text-foreground group-hover:block">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" aria-hidden>
              <path d="M4 20h4L19 9l-4-4L4 16v4Z" stroke="currentColor" strokeWidth="2"
                    strokeLinejoin="round" />
            </svg>
          </button>
          <button onClick={onDelete} title="Supprimer"
                  className="hidden cursor-pointer px-1 text-fg-faint hover:text-bad group-hover:block">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" aria-hidden>
              <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2"
                    strokeLinecap="round" />
            </svg>
          </button>
        </>
      )}
    </div>
  );
}

// ─── Composant principal ───────────────────────────────────────────────────

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
  const [route, setRoute] = useState<string>("");
  const [mode, setMode] = useState<"auto" | "rag" | "agent">("auto");
  const [input, setInput] = useState("");
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
    const t = setTimeout(() => { refreshDocs(); refreshSessions(); }, 0);
    return () => clearTimeout(t);
  }, [refreshDocs, refreshSessions]);

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

  const ask = useCallback(async (question: string) => {
    const q = question.trim();
    if (!q || busy) return;
    setInput("");
    const prefs = loadPrefs();
    const history = prefs.useMemory
      ? messages.map((m) => ({ role: m.role, content: m.content }))
      : [];
    setMessages((ms) => [...ms, { role: "user", content: q }]);
    setPhase("retrieve");
    setNChunks(null);
    setPartial("");
    setAgentTrace([]);
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
        else if (ev.type === "eval") {
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
    setPhase("idle");
  }, [busy, messages, selected, mode, sessionId, refreshSessions]);

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
        next[i] = { ...next[i], content: d.answer, citations: d.citations,
                    chunks: selectedChunks, eval: undefined };
        return next;
      });
    } catch { /* silencieux : le message existant reste */ }
    setPhase("idle");
  };

  /** Pièce jointe façon chatbot : réglages par défaut, barre de progression
   * visible, envoi BLOQUÉ tant que l'indexation tourne, puis le périmètre est
   * automatiquement fixé sur le document ajouté (le prompt suivant porte
   * dessus). Réglages fins : onglet Documents. */
  const attachFiles = async (files: FileList | null) => {
    if (!files?.length || attaching) return;
    const name = files[0].name;
    setAttachError(null);
    setAttach({ name, pct: 0, step: "Dépôt du document…" });
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
      setAttachError(`Dépôt impossible pour « ${name} » — type non accepté ou API indisponible.`);
      return;
    }
    // Suivi de LA tâche de ce document jusqu'au bout.
    pollRef.current = setInterval(async () => {
      const s = await getJSON<{ active: boolean; jobs: { name: string; status: string;
        pct: number; step: string; message: string | null }[] }>("/api/ingest/status")
        .catch(() => null);
      const job = s?.jobs.filter((j) => j.name === name).at(-1);
      if (!job) return;
      if (job.status === "queued" || job.status === "running") {
        setAttach({ name, pct: job.pct, step: job.step });
        return;
      }
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
      setAttach(null);
      if (job.status === "success") {
        refreshDocs();
        setSelected(name); // le prompt suivant porte sur CE document
      } else {
        setAttachError(`Indexation de « ${name} » échouée : ${job.message ?? "erreur."}`);
      }
    }, 1200);
  };

  return (
    <div className="flex h-[calc(100vh-10.5rem)] min-h-105 gap-4">
      {/* Conversations. */}
      <aside className="hidden w-56 shrink-0 flex-col gap-2 md:flex">
        <button
          onClick={newConversation}
          className="cursor-pointer rounded-lg border border-edge px-3 py-2 text-left text-xs text-fg-muted transition-colors hover:border-accent/50 hover:text-foreground"
        >
          + Nouvelle conversation
        </button>
        <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto pr-1">
          {sessions.map((s) => (
            <SessionRow
              key={s.id}
              s={s}
              active={s.id === sessionId}
              onOpen={() => openSession(s)}
              onRename={async (t) => {
                await fetch(`${API_BASE}/api/sessions/${s.id}`, {
                  method: "PATCH",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ title: t }),
                }).catch(() => null);
                refreshSessions();
              }}
              onDelete={async () => {
                await fetch(`${API_BASE}/api/sessions/${s.id}`, { method: "DELETE" })
                  .catch(() => null);
                if (s.id === sessionId) newConversation();
                refreshSessions();
              }}
            />
          ))}
          {sessions.length === 0 && (
            <p className="px-2 py-1.5 text-[11px] text-fg-faint">
              Vos conversations persistées apparaîtront ici.
            </p>
          )}
        </div>
      </aside>

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
              <p key={i}
                 className="ml-auto max-w-[85%] rounded-2xl bg-accent/15 px-4 py-2.5 text-sm text-foreground">
                {m.content}
              </p>
            ) : (
              <div key={i} className="rounded-2xl border border-edge bg-surface px-4 py-3">
                <AssistantMessage
                  m={m} expert={expert}
                  canRegenerate={expert && i === messages.length - 1 && !busy}
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
                      <><Dot tone="accent" pulse /> Agent en raisonnement</>
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
                <button
                  onClick={() => abortRef.current?.abort()}
                  title="Arrête la génération immédiatement (la réponse partielle est conservée)."
                  className="cursor-pointer rounded-md border border-bad/40 px-2 py-0.5 text-xs text-bad transition-colors hover:bg-bad/15"
                >
                  ■ Stop
                </button>
              </div>
              {agentTrace.length > 0 && (
                <div className="chat-md mb-2 border-l-2 border-edge pl-3 text-xs text-fg-muted">
                  <ReactMarkdown>{agentTrace.join("\n\n")}</ReactMarkdown>
                </div>
              )}
              {partial ? (
                <div className="chat-md stream-caret text-sm leading-relaxed">
                  <ReactMarkdown>{partial}</ReactMarkdown>
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

        {/* Pièce jointe en cours : barre de progression visible, envoi bloqué. */}
        {attach && (
          <div className="mb-2 rounded-xl border border-edge bg-surface-2 px-3 py-2">
            <div className="mb-1 flex items-baseline justify-between gap-2 text-xs">
              <span className="flex items-center gap-2 text-fg-muted">
                <Dot tone="accent" pulse /> Indexation de {attach.name}
              </span>
              <span className="font-mono tabular-nums text-fg-faint">{attach.pct} %</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-muted">
              <div className="h-full rounded-full bg-accent transition-[width] duration-500"
                   style={{ width: `${attach.pct}%` }} />
            </div>
            <p className="mt-1 text-[11px] text-fg-faint">
              {attach.step} — vous pourrez interroger ce document dès la fin de
              l&apos;indexation.
            </p>
          </div>
        )}
        {attachError && (
          <div className="mb-2 flex items-center justify-between gap-2 rounded-xl border border-bad/40 bg-surface-2 px-3 py-2 text-xs text-fg-muted">
            <span>{attachError}</span>
            <button onClick={() => setAttachError(null)} aria-label="Fermer"
                    className="cursor-pointer text-fg-faint hover:text-foreground">✕</button>
          </div>
        )}

        {/* Composeur. */}
        <div className="rounded-2xl border border-edge bg-surface-2 p-2 focus-within:border-accent/60">
          <form
            onSubmit={(e) => { e.preventDefault(); ask(input); }}
          >
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  if (!attaching) ask(input);
                }
              }}
              disabled={busy}
              rows={1}
              placeholder="Posez une question sur vos documents…"
              aria-label="Question"
              className="max-h-40 w-full resize-none bg-transparent px-2 py-1.5 text-sm text-foreground [field-sizing:content] placeholder:text-fg-faint focus:outline-none disabled:opacity-60"
            />
            <div className="mt-1 flex items-center gap-2">
              <input ref={fileRef} type="file" multiple hidden
                     accept=".pdf,.docx,.pptx,.xlsx,.html,.md"
                     onChange={(e) => attachFiles(e.target.files)} />
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                disabled={attaching}
                title="Joindre un document : indexé avec les réglages par défaut (réglages fins dans l'onglet Documents). L'envoi attend la fin de l'indexation."
                className="cursor-pointer rounded-lg p-1.5 text-fg-faint transition-colors hover:bg-muted hover:text-foreground disabled:cursor-default disabled:opacity-40"
                aria-label="Joindre un document"
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden>
                  <path d="M21 12.5 12.6 21a5.6 5.6 0 0 1-8-8L13 4.5a3.7 3.7 0 0 1 5.3 5.3L10 18a1.9 1.9 0 0 1-2.7-2.7l7.6-7.5"
                        stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
                </svg>
              </button>
              <select
                value={selected}
                onChange={(e) => setSelected(e.target.value)}
                disabled={busy}
                aria-label="Document interrogé"
                title="Périmètre : un document, ou tous."
                className="max-w-56 cursor-pointer rounded-lg border border-edge bg-surface px-2 py-1 text-[11px] text-fg-muted focus:outline-none"
              >
                <option value="">Tous les documents</option>
                {docs.map((d) => (
                  <option key={d.name} value={d.name}>{d.name}</option>
                ))}
              </select>
              {expert && (
                <select
                  value={mode}
                  onChange={(e) => setMode(e.target.value as typeof mode)}
                  disabled={busy}
                  aria-label="Mode de traitement"
                  title="Auto : un routeur choisit RAG ou Agent selon la question. Agent : raisonnement multi-étapes (plus lent)."
                  className="cursor-pointer rounded-lg border border-edge bg-surface px-2 py-1 text-[11px] text-fg-muted focus:outline-none"
                >
                  <option value="auto">Auto</option>
                  <option value="rag">RAG</option>
                  <option value="agent">Agent</option>
                </select>
              )}
              <button
                type="submit"
                disabled={busy || attaching || !input.trim()}
                className="ml-auto cursor-pointer rounded-xl bg-accent px-3.5 py-1.5 text-sm font-medium text-background transition-all duration-200 hover:bg-accent-bright active:scale-[0.97] disabled:cursor-default disabled:opacity-40"
                aria-label="Envoyer"
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden>
                  <path d="M4 12h14M13 6l6 6-6 6" stroke="currentColor" strokeWidth="2"
                        strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
            </div>
          </form>
        </div>
      </section>
    </div>
  );
}
