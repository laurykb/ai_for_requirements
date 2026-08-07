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

/** GET JSON — lève sur statut non-2xx ; à attraper côté appelant. */
export async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`);
  return res.json() as Promise<T>;
}
