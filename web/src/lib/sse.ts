/** SSE sur POST via fetch + ReadableStream (EventSource ne fait pas de POST).
 *
 * Robustesse : le flux peut se couper sans trame `done` (API redémarrée…) —
 * l'appelant le détecte (`done` jamais reçu) et affiche un état dégradé au
 * lieu de pendre. Toute ligne non-JSON est ignorée sans casser le flux. */

import { apiFetch, requireOk } from "@/lib/api";
import type { AskEvent } from "@/lib/types";

export async function streamSSE<T>(
  path: string,
  init: RequestInit,
  onEvent: (event: T) => void,
): Promise<void> {
  const res = await requireOk(await apiFetch(path, init), path);
  if (!res.body) throw new Error(`${path} → réponse vide`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      for (const line of frame.split("\n")) {
        if (!line.startsWith("data: ")) continue;
        try {
          onEvent(JSON.parse(line.slice(6)) as T);
        } catch {
          // Une trame isolée malformée ne doit pas interrompre le flux.
        }
      }
    }
  }
}

export async function streamAsk(
  body: {
    question: string;
    source: string | null;
    history: { role: string; content: string }[];
    parent_child?: boolean | null;
    self_rag?: boolean | null;
    system_prompt?: string | null;
    mode?: string;
    session_id?: string | null;
  },
  onEvent: (ev: AskEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamSSE<AskEvent>("/api/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  }, onEvent);
}
