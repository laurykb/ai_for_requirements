/** SSE sur POST via fetch + ReadableStream (EventSource ne fait pas de POST).
 *
 * Robustesse : le flux peut se couper sans trame `done` (API redémarrée…) —
 * l'appelant le détecte (`done` jamais reçu) et affiche un état dégradé au
 * lieu de pendre. Toute ligne non-JSON est ignorée sans casser le flux. */

import { API_BASE } from "@/lib/api";
import type { AskEvent } from "@/lib/types";

export async function streamAsk(
  body: { question: string; source: string | null; history: { role: string; content: string }[] },
  onEvent: (ev: AskEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`/api/ask → HTTP ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Les trames SSE sont séparées par une ligne vide.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      for (const line of frame.split("\n")) {
        if (!line.startsWith("data: ")) continue;
        try {
          onEvent(JSON.parse(line.slice(6)) as AskEvent);
        } catch {
          // ligne malformée : ignorée, le flux continue
        }
      }
    }
  }
}
