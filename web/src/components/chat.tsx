"use client";

/** Chat RAG : question → pipeline visible (boîte de verre) → réponse streamée.
 *
 * Principes : signal > bruit — la réponse d'abord ; sources et passages
 * récupérés en blocs repliés. Le bandeau pipeline (Recherche ▸ passages ▸
 * Génération) rend chaque étape visible sans encombrer. */

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

import { getJSON, type SourcesResponse } from "@/lib/api";
import { streamAsk } from "@/lib/sse";
import { fmt } from "@/lib/format";
import type { ChatMessage, ChunkView, Citation } from "@/lib/types";
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
    <p className="mb-2 flex items-center gap-2 text-xs text-fg-faint">
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

function AssistantMessage({ m }: { m: ChatMessage }) {
  return (
    <div className="rounded-xl border border-edge bg-surface px-4 py-3">
      <div className="chat-md text-sm leading-relaxed">
        <ReactMarkdown>{m.content}</ReactMarkdown>
      </div>
      {m.error && (
        <div className="mt-2">
          <Banner tone="bad">{m.error}</Banner>
        </div>
      )}
      {m.citations && <SourcesBlock citations={m.citations} />}
      {m.chunks && <ChunksBlock chunks={m.chunks} />}
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
      const history = messages.map((m) => ({ role: m.role, content: m.content }));
      setMessages((ms) => [...ms, { role: "user", content: q }]);
      setPhase("retrieve");
      setNChunks(null);
      setPartial("");

      // L'état de la réponse en cours (les trames arrivent plus vite que React ne re-rend).
      const draft: ChatMessage = { role: "assistant", content: "" };
      let chunks: ChunkView[] = [];
      let gotDone = false;

      try {
        await streamAsk({ question: q, source: selected || null, history }, (ev) => {
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
        });
        if (!gotDone)
          draft.error =
            "Le flux s'est interrompu avant la fin (API redémarrée ?). Réponse partielle affichée.";
      } catch (e) {
        draft.error = `L'API locale est injoignable (${String(e)}). Lancer : python serve.py --web`;
      }

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
            <AssistantMessage key={i} m={m} />
          ),
        )}

        {/* Réponse en cours : pipeline + texte streamé. */}
        {busy && (
          <div className="rounded-xl border border-edge bg-surface px-4 py-3">
            <PipelineStrip phase={phase} nChunks={nChunks} />
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
