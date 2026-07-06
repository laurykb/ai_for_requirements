"use client";

/** État de l'API locale : une ligne discrète, pastille + libellé.
 * Vert = API et services prêts ; ambre = API là mais un service manque ;
 * rouge = API hors ligne (avec la commande pour la lancer). */

import { useEffect, useState } from "react";

import { getJSON, type Health, type SourcesResponse } from "@/lib/api";
import { Dot, type Tone } from "@/components/ui";

type State =
  | { kind: "loading" }
  | { kind: "offline" }
  | { kind: "online"; health: Health; docs: number | null };

export function ApiStatus() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const health = await getJSON<Health>("/health");
        let docs: number | null = null;
        try {
          const s = await getJSON<SourcesResponse>("/api/sources");
          docs = s.available ? s.sources.length : null;
        } catch {
          /* /health suffit pour l'état global */
        }
        if (!cancelled) setState({ kind: "online", health, docs });
      } catch {
        if (!cancelled) setState({ kind: "offline" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.kind === "loading") return null;

  let tone: Tone = "good";
  let label: React.ReactNode;
  if (state.kind === "offline") {
    tone = "bad";
    label = (
      <>
        API hors ligne — lancer <code className="font-mono">python serve.py --web</code>
      </>
    );
  } else {
    const { mongo, ollama } = state.health.services;
    const missing = [!mongo && "MongoDB", !ollama && "Ollama"].filter(Boolean);
    if (missing.length > 0) {
      tone = "warn";
      label = `API connectée · ${missing.join(" et ")} arrêté${missing.length > 1 ? "s" : ""}`;
    } else {
      label =
        state.docs === null
          ? "API connectée"
          : `API connectée · ${state.docs} document${state.docs > 1 ? "s" : ""} ingéré${state.docs > 1 ? "s" : ""}`;
    }
  }

  return (
    <p className="mt-4 flex items-center gap-2 text-xs text-fg-faint">
      <Dot tone={tone} />
      {label}
    </p>
  );
}
