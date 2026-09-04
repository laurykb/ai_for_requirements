/** Client de l'API locale (FastAPI, :8000). */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "/backend";

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly detail?: unknown) {
    super(message);
    this.name = "ApiError";
  }
}

export type Health = {
  status: string;
  services: { mongo: boolean; ollama: boolean };
};

export type SourceIndexStatus = {
  mongo: number;
  indexable: number;
  bm25: boolean;
  vectors: number;
  in_sync: boolean;
  degraded_reasons: string[];
};

export type SourcesResponse = {
  available: boolean;
  in_sync?: boolean;
  consistency_available?: boolean;
  orphans?: SourceIndexStatus[];
  sources: { name: string; chunks: number;
    quality?: { accepted: number; degraded: number; quarantined: number };
    index?: SourceIndexStatus;
  }[];
};

/** Document réservé de la baseline d'exigences LynX dans l'index documentaire.
 * Doit rester identique à `BASELINE_SOURCE` (api/lynx_chat.py). */
export const LYNX_BASELINE_SOURCE = "baseline-exigences-lynx.md";

/** Nom de source pour l'affichage : la source réservée de la baseline
 * apparaît sous son étiquette produit, jamais son nom de fichier interne. */
export function displaySourceName(source?: string | null): string {
  if (!source) return "document";
  return source === LYNX_BASELINE_SOURCE ? "Baseline d'exigences" : source;
}

/** Diff baseline indexée -> courante (ids plafonnés à 20 par liste). */
export type LynxBaselineDiff = {
  added: string[]; removed: string[]; changed: string[];
  n_added: number; n_removed: number; n_changed: number;
};

/** Fraîcheur de l'index baseline (GET /api/lynx/chat/status). */
export type LynxChatStatus = {
  available: boolean;
  source: string;
  n_exigences: number;
  indexed_chunks: number;
  indexed_n_exigences: number | null;
  synced_at: number | null;
  in_sync: boolean;
  diff: LynxBaselineDiff | null;
  syncing: boolean;
  sync_pct: number | null;
  sync_error: string | null;
  retrieval_mode: "hybrid" | "stale" | "unavailable";
  degraded_reasons: string[];
  active_version: string | null;
  preparing_version: string | null;
  last_success: number | null;
  index_counts: { mongo: number; bm25: number; vectors: number } | null;
  versions: {
    version_id: string; status: "preparing" | "active" | "ready" | "failed";
    n_exigences: number; created_at: number; activated_at?: number;
    error?: string | null; index_counts?: { mongo: number; bm25: number; vectors: number };
  }[];
};

function detailMessage(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "message" in detail) {
    const message = (detail as { message?: unknown }).message;
    return typeof message === "string" ? message : null;
  }
  return null;
}

/** Transport HTTP unique : URL et politique de cache cohérentes. */
export function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(`${API_BASE}${path}`, { cache: "no-store", ...init });
}

export async function requireOk(res: Response, path: string): Promise<Response> {
  if (res.ok) return res;
  let detail: unknown;
  try {
    detail = ((await res.json()) as { detail?: unknown }).detail;
  } catch {
    detail = undefined;
  }
  throw new ApiError(
    detailMessage(detail) ?? `${path} → HTTP ${res.status}`,
    res.status,
    detail,
  );
}

export async function requestJSON<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await requireOk(await apiFetch(path, init), path);
  return res.json() as Promise<T>;
}

export function sendJSON<T>(
  path: string,
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  return requestJSON<T>(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
}

export function getJSON<T>(path: string): Promise<T> {
  return requestJSON<T>(path);
}
