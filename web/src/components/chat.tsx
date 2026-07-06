"use client";

/** Chat RAG : question → pipeline visible (boîte de verre) → réponse streamée.
 *
 * Principes : signal > bruit — la réponse d'abord ; sources et passages
 * récupérés en blocs repliés. Le bandeau pipeline (Recherche ▸ passages ▸
 * Génération) rend chaque étape visible sans encombrer. */

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

import { API_BASE, getJSON, type SourcesResponse } from "@/lib/api";
import { streamAsk } from "@/lib/sse";
import { fmt } from "@/lib/format";
import { loadPrefs } from "@/lib/prefs";
import type { ChatMessage, ChunkView, Citation } from "@/lib/types";
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

type Phase = "idle" | "retrieve" | "generate";

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

/** Boîte de verre : le contenu exact de chaque passage retrouvé, replié. */
function ChunksBlock({ chunks }: { chunks: ChunkView[] }) {
  if (!chunks.length) return null;
  return (
    <details className="chat-details">
      <summary>
        Passages récupérés ({chunks.length})
        <Hint text="Le contenu exact que le moteur de recherche a retrouvé et fourni au modèle pour répondre." />
      </summary>
      <div className="mt-2 space-y-2">
        {chunks.map((c, i) => (
          <details key={i} className="chat-details">
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
        ))}
      </div>
    </details>
  );
}

/** Bandeau pipeline : chaque étape s'allume quand elle tourne, verte une fois faite. */
function PipelineStrip({ phase, nChunks }: { phase: Phase; nChunks: number | null }) {
  const retrieving = phase === "retrieve";
  return (
    <p className="flex items-center gap-2 text-xs text-fg-faint">
      <Dot tone={retrieving ? "accent" : "good"} pulse={retrieving} />
      Recherche
      <span className="text-fg-faint">▸</span>
      {nChunks === null ? "…" : `${nChunks} passage${nChunks > 1 ? "s" : ""}`}
      <span className="text-fg-faint">▸</span>
      <Dot tone={phase === "generate" ? "accent" : "neutral"} pulse={phase === "generate"} />
      Génération
    </p>
  );
}

function AssistantMessage({ m, expert }: { m: ChatMessage; expert: boolean }) {
  return (
    <div className="rounded-xl border border-edge bg-surface px-4 py-3">
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
      {/* Passages récupérés : détail technique — mode expert seulement. */}
      {expert && m.chunks && <ChunksBlock chunks={m.chunks} />}
    </div>
  );
}

type EvalResult = {
  faithfulness?: number | null;
  answer_relevance?: number | null;
  context_relevance?: number | null;
  issues?: string[];
};

/** Vérification LLM-as-judge de la dernière réponse (expert, à la demande) :
 * 1 appel fusionné → 3 axes + extraits problématiques. */
function VerifyBlock({ question, m }: { question: string; m: ChatMessage }) {
  const [result, setResult] = useState<EvalResult | null>(null);
  const [running, setRunning] = useState(false);
  if (!m.chunks?.length) return null;

  const run = async () => {
    setRunning(true);
    try {
      const res = await fetch(`${API_BASE}/api/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, answer: m.content, chunks: m.chunks }),
      });
      setResult(res.ok ? await res.json() : {});
    } catch {
      setResult({});
    }
    setRunning(false);
  };

  return (
    <div className="mt-1">
      {!result && (
        <button
          onClick={run}
          disabled={running}
          title="Contrôle la fidélité aux sources (1 appel LLM, à la demande)."
          className="cursor-pointer rounded-md border border-edge px-2 py-1 text-xs text-fg-faint transition-colors hover:border-accent/60 hover:text-foreground disabled:opacity-50"
        >
          {running ? "Vérification…" : "Vérifier la réponse"}
        </button>
      )}
      {result && (
        <div className="rounded-lg border border-edge bg-surface-2 px-3 py-2 text-xs">
          <p className="text-[11px] uppercase tracking-[0.14em] text-fg-faint">
            Vérification automatique (LLM-as-judge, 0 → 1)
          </p>
          <div className="mt-1.5 flex gap-5 font-mono tabular-nums text-fg-muted">
            <span>Fidélité {fmt(result.faithfulness ?? 0)}</span>
            <span>Pertinence réponse {fmt(result.answer_relevance ?? 0)}</span>
            <span>Pertinence contexte {fmt(result.context_relevance ?? 0)}</span>
          </div>
          {(result.issues?.length ?? 0) > 0 && (
            <ul className="mt-1.5 list-disc pl-4 text-fg-faint">
              {result.issues!.map((it, i) => (
                <li key={i}>{it}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

export function Chat() {
  const [docs, setDocs] = useState<{ name: string; chunks: number }[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [phase, setPhase] = useState<Phase>("idle");
  const [nChunks, setNChunks] = useState<number | null>(null);
  const [partial, setPartial] = useState<string>("");
  const [input, setInput] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const expert = useExpert();
  const busy = phase !== "idle";

  useEffect(() => {
    getJSON<SourcesResponse>("/api/sources")
      .then((s) => setDocs(s.sources))
      .catch(() => setDocs([]));
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, partial]);

  const ask = useCallback(
    async (question: string) => {
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

      // L'état de la réponse en cours (les trames arrivent plus vite que React ne re-rend).
      const draft: ChatMessage = { role: "assistant", content: "" };
      let chunks: ChunkView[] = [];
      let gotDone = false;
      const controller = new AbortController();
      abortRef.current = controller;

      try {
        await streamAsk({
          question: q,
          source: selected || null,
          history,
          parent_child: prefs.parentChild,
          self_rag: prefs.selfRag,
          system_prompt: prefs.systemPrompt,
        }, (ev) => {
          if (ev.type === "stage" && ev.stage === "generate") setPhase("generate");
          else if (ev.type === "retrieved") {
            chunks = ev.chunks;
            setNChunks(ev.chunks.length);
          } else if (ev.type === "token") {
            draft.content += ev.text;
            setPartial(draft.content);
          } else if (ev.type === "sources") draft.citations = ev.citations;
          else if (ev.type === "done") {
            gotDone = true;
            if (!ev.found && !draft.content) draft.content = NOT_FOUND_MESSAGE;
          } else if (ev.type === "error") {
            gotDone = true; // trame terminale : pas un flux coupé
            draft.error = `Une erreur est survenue pendant la génération : ${ev.message}`;
          }
        }, controller.signal);
        if (!gotDone)
          draft.error =
            "Le flux s'est interrompu avant la fin (API redémarrée ?). Réponse partielle affichée.";
      } catch (e) {
        if (controller.signal.aborted) {
          // Arrêt volontaire : la coupure remonte jusqu'à Ollama via l'API.
          draft.stopped = true;
        } else {
          draft.error = `L'API locale est injoignable (${String(e)}). Lancer : python serve.py --web`;
        }
      }
      abortRef.current = null;

      if (chunks.length) draft.chunks = chunks;
      setMessages((ms) => [...ms, draft]);
      setPhase("idle");
      setPartial("");
    },
    [busy, messages, selected],
  );

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      {/* Périmètre : un document, ou tous. */}
      <div>
        <select
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          disabled={busy}
          aria-label="Document interrogé"
          className="w-full rounded-lg border border-edge bg-surface-2 px-3 py-2 text-sm text-foreground focus:border-accent focus:outline-none"
        >
          <option value="">Tous les documents</option>
          {docs.map((d) => (
            <option key={d.name} value={d.name}>
              {d.name} ({d.chunks} passages)
            </option>
          ))}
        </select>
        <p className="mt-1.5 text-xs text-fg-faint">
          {selected
            ? `Réponses basées sur ${selected}.`
            : "Aucun document choisi — je cherche dans tous vos documents."}
        </p>
      </div>

      {/* Historique. */}
      <div className="flex flex-col gap-3">
        {messages.length === 0 && !busy && (
          <div className="rise-in py-8 text-center">
            <p className="text-sm font-medium text-foreground">
              Posez une question sur vos documents
            </p>
            <p className="mt-1 text-xs text-fg-muted">
              Réponses sourcées, citant les passages de vos documents.
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
            <p
              key={i}
              className="ml-auto max-w-[85%] rounded-xl bg-accent/15 px-4 py-2.5 text-sm text-foreground"
            >
              {m.content}
            </p>
          ) : (
            <div key={i}>
              <AssistantMessage m={m} expert={expert} />
              {/* Vérification de la DERNIÈRE réponse seulement (expert). */}
              {expert && i === messages.length - 1 && !busy && (
                <VerifyBlock question={messages[i - 1]?.content ?? ""} m={m} />
              )}
            </div>
          ),
        )}

        {/* Réponse en cours : pipeline + texte streamé + arrêt d'urgence. */}
        {busy && (
          <div className="rounded-xl border border-edge bg-surface px-4 py-3">
            <div className="mb-2 flex items-start justify-between gap-3">
              <PipelineStrip phase={phase} nChunks={nChunks} />
              <button
                onClick={() => abortRef.current?.abort()}
                title="Arrête la génération immédiatement (la réponse partielle est conservée)."
                className="cursor-pointer rounded-md border border-bad/40 px-2 py-0.5 text-xs text-bad transition-colors hover:bg-bad/15"
              >
                ■ Stop
              </button>
            </div>
            {partial ? (
              <div className="chat-md stream-caret text-sm leading-relaxed">
                <ReactMarkdown>{partial}</ReactMarkdown>
              </div>
            ) : (
              <p className="flex items-center gap-2 text-xs text-fg-muted">
                <Spinner />
                {phase === "retrieve" ? "Recherche dans les documents…" : "Génération…"}
              </p>
            )}
          </div>
        )}
        <div ref={endRef} />
      </div>

      {/* Saisie. */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          ask(input);
        }}
        className="flex gap-2"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={busy}
          placeholder="Posez une question sur vos documents…"
          aria-label="Question"
          className="flex-1 rounded-xl border border-edge bg-surface-2 px-4 py-2.5 text-sm text-foreground placeholder:text-fg-faint focus:border-accent focus:outline-none disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={busy || !input.trim()}
          className="cursor-pointer rounded-xl bg-accent px-4 py-2.5 text-sm font-medium text-background transition-colors hover:bg-accent-bright disabled:cursor-default disabled:opacity-40"
        >
          Envoyer
        </button>
      </form>
    </div>
  );
}
