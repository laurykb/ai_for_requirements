"use client";

/** Briques partagées de la vue LynX : types miroirs de l'API /api/lynx/*,
 * styles (sévérités, rôles, boutons), pastille de rôle, boîte de verre des
 * agents et lecteur de flux SSE. Aucun état métier ici. */

import ReactMarkdown from "react-markdown";

import { API_BASE } from "@/lib/api";
import { Hint, type Tone } from "@/components/ui";

// ─── Types (miroir de l'API /api/lynx/*) ───────────────────────────────────

export type Debate = { statut: string; plaidoyer: string; jugement: string; erreur?: string };
export type Finding = { scope: string; sev: string; analyzer: string; method?: string;
                        sim?: number | null; msg: string; debate?: Debate | null };
export type Exchange = { agent: string; role: string; mission?: string; input?: string;
                         output?: string; latency_ms?: number | null; cached?: boolean; ok?: boolean };
export type Verdict = { verdict: string; message: string; findings: Finding[];
                        impacted: string[]; exchanges: Exchange[] };
export type AuditFinding = { req_id: string; axis: string; severity: string; message: string;
                             debate?: Debate | null };
export type AuditReport = { n: number; score: number; counts: Record<string, number>;
                            flagged_ids: string[]; n_non_audite: number;
                            findings: AuditFinding[];
                            exchanges: { req_id: string; input: string; output: string;
                                         flagged: boolean }[] };
export type Suggestion = { texte?: string; justification?: string; changements?: string[];
                           corrige_tout?: boolean; error?: string };
export type FixProgress = { phase: string; passe: number; done: number; total: number;
                            req_id?: string | null };
export type FixItem = { req_id: string; texte_avant: string; texte_apres: string;
                        findings_avant: AuditFinding[]; findings_apres: AuditFinding[];
                        justification: string; erreur: string; statut: string; passes: number };
export type FixRecap = { recap: FixItem[]; compteurs: Record<string, number>;
                         passes: number; score_apres: number | null };

export const SEV_TONE: Record<string, Tone> = { INFO: "good", WARNING: "warn",
                                                BLOCKING: "bad", BLOQUANT: "bad" };

/** Badge du débat contradictoire sur un BLOQUANT sémantique : « contesté ·
 * maintenu » ou « contesté · rétrogradé », plaidoyer + motivation dépliables. */
export function DebateBadge({ debate }: { debate?: Debate | null }) {
  if (!debate) return null;
  const retro = debate.statut === "RETROGRADE";
  return (
    <details className="chat-details mt-1">
      <summary className="text-[11px]">
        <span className={`rounded bg-muted px-1 py-px font-mono text-[10px] ${
          retro ? "text-warn" : "text-fg-faint"}`}>
          contesté · {retro ? "rétrogradé" : "maintenu"}
        </span>{" "}
        <Hint text="Chaque BLOQUANT issu d'un agent IA passe par un débat contradictoire : un avocat de la défense tente de le réfuter à partir du contexte de traçabilité, un juge tranche. Réfuté → rétrogradé en avertissement (jamais supprimé). Toute erreur pendant le débat conserve le verdict initial." />
      </summary>
      <div className="mt-1 space-y-1 text-[11px] leading-relaxed text-fg-muted">
        {debate.plaidoyer && (
          <p><span className="font-medium text-foreground">Avocat de la défense :</span>{" "}
            {debate.plaidoyer}</p>
        )}
        {debate.jugement && (
          <p><span className="font-medium text-foreground">Juge :</span> {debate.jugement}</p>
        )}
        {debate.erreur && (
          <p className="text-fg-faint">Débat interrompu ({debate.erreur}) — verdict initial conservé.</p>
        )}
      </div>
    </details>
  );
}
export const ROLE_STYLE: Record<string, { bg: string; label: string }> = {
  "déterministe": { bg: "var(--role-rule)", label: "Règle" },
  embeddings: { bg: "var(--role-vector)", label: "Vectoriel" },
  IA: { bg: "var(--role-agent)", label: "Agent IA" },
  "synthèse": { bg: "var(--role-synthesis)", label: "Synthèse" },
};

export const btnPrimary =
  "cursor-pointer rounded-lg bg-accent px-3 py-2 text-xs font-semibold text-background " +
  "transition-all duration-200 hover:bg-accent-bright active:scale-[0.98] " +
  "disabled:cursor-default disabled:opacity-40";
export const btnGhost =
  "cursor-pointer rounded-lg border border-edge px-3 py-1.5 text-xs text-fg-muted " +
  "transition-colors duration-200 hover:border-edge-strong hover:text-foreground " +
  "disabled:cursor-default disabled:opacity-40";
export const btnDanger =
  "cursor-pointer rounded-lg border border-bad/40 px-3 py-1.5 text-xs text-bad " +
  "transition-colors duration-200 hover:bg-bad/15 disabled:cursor-default disabled:opacity-40";
export const inputCls =
  "rounded-lg border border-edge bg-surface-2 px-3 py-2 text-xs text-foreground " +
  "placeholder:text-fg-faint focus:border-accent focus:outline-none";

export function RoleChip({ role }: { role: string }) {
  const s = ROLE_STYLE[role] ?? { bg: "var(--role-default)", label: role };
  return (
    <span className="rounded-md px-1.5 py-0.5 text-[10px] font-medium text-white"
          style={{ background: s.bg }}>
      {s.label}
    </span>
  );
}

/** Boîte de verre : la timeline des agents (reçu → répondu), repliée. */
export function GlassBox({ exchanges, title }: { exchanges: Exchange[]; title: string }) {
  if (!exchanges.length) return null;
  return (
    <details className="chat-details">
      <summary>{title} ({exchanges.length} agents)</summary>
      <div className="mt-3 space-y-4">
        {exchanges.map((x, i) => (
          <div key={i} className="border-l-2 border-edge pl-3">
            <p className="flex items-center gap-2 text-xs text-foreground">
              <RoleChip role={x.role} />
              <span className="font-medium">{x.agent}</span>
              {x.latency_ms != null && (
                <span className="font-mono text-[10px] tabular-nums text-fg-faint">
                  {x.latency_ms} ms{x.cached ? " · cache" : ""}
                </span>
              )}
            </p>
            {x.mission && <p className="mt-0.5 text-[11px] italic text-fg-faint">{x.mission}</p>}
            {x.input && <p className="mt-1.5 text-[11px] text-fg-faint">Reçu — {x.input}</p>}
            {x.output && (
              <div className="chat-md mt-1 text-xs leading-relaxed text-fg-muted">
                <ReactMarkdown>{x.output}</ReactMarkdown>
              </div>
            )}
          </div>
        ))}
      </div>
    </details>
  );
}

export async function streamPost(path: string, body: unknown,
                                 onEvent: (ev: Record<string, unknown>) => void,
                                 signal?: AbortSignal): Promise<void> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`${path} → HTTP ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames)
      for (const line of frame.split("\n"))
        if (line.startsWith("data: "))
          try { onEvent(JSON.parse(line.slice(6))); } catch { /* ignorée */ }
  }
}
