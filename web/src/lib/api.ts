/** Client de l'API locale (FastAPI, :8000). */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

export type Health = {
  status: string;
  services: { mongo: boolean; ollama: boolean };
};

export type SourcesResponse = {
  available: boolean;
  sources: { name: string; chunks: number; quality?: { accepted: number; degraded: number; quarantined: number } }[];
};

/** Document réservé de la baseline d'exigences LynX dans l'index documentaire.
 * Doit rester identique à `BASELINE_SOURCE` (api/lynx_chat.py). */
export const LYNX_BASELINE_SOURCE = "baseline-exigences-lynx.md";

/** Fraîcheur de l'index baseline (GET /api/lynx/chat/status). */
export type LynxChatStatus = {
  available: boolean;
  source: string;
  n_exigences: number;
  indexed_chunks: number;
  indexed_n_exigences: number | null;
  synced_at: number | null;
  in_sync: boolean;
  syncing: boolean;
  sync_pct: number | null;
  sync_error: string | null;
};

/** GET JSON — lève sur statut non-2xx ; à attraper côté appelant. */
export async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`);
  return res.json() as Promise<T>;
}
