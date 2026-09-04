"use client";

import { useCallback, useEffect, useState } from "react";

import { apiFetch, getJSON } from "@/lib/api";
import { useExpert } from "@/components/expert-toggle";
import { Banner, Spinner } from "@/components/ui";

type PromptItem = {
  key: string; role: string; description: string; editable: boolean;
  default: string; active: string; overridden: boolean; variables: string[]; version: number;
};
export type PromptGroup = "chat";

const PROMPT_GROUPS: Array<{ id: PromptGroup; label: string; description: string }> = [
  { id: "chat", label: "Chat / RAG", description: "Prompts de conversation, recherche, planification et synthèse." },
];

function promptGroup(): PromptGroup {
  return "chat";
}

export function AgentsView({ groups = ["chat"] }: { groups?: PromptGroup[] }) {
  const expert = useExpert();
  const [items, setItems] = useState<PromptItem[] | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<string | null>(null);
  const [activeGroup, setActiveGroup] = useState<PromptGroup>(groups[0] ?? "chat");
  const promptScope = "rag";

  const load = useCallback(async () => {
    const data = await getJSON<{ prompts: PromptItem[] }>(`/api/prompts${promptScope ? `?scope=${promptScope}` : ""}`);
    setItems(data.prompts);
    setDrafts(Object.fromEntries(data.prompts.map((p) => [p.key, p.active])));
  }, [promptScope]);

  useEffect(() => {
    const timer = setTimeout(() => { void load().catch(() => setItems([])); }, 0);
    return () => clearTimeout(timer);
  }, [load]);

  const act = async (item: PromptItem, reset = false) => {
    setStatus(null);
    const res = await apiFetch(`/api/prompts/${encodeURIComponent(item.key)}`, {
      method: reset ? "DELETE" : "PUT",
      headers: { "Content-Type": "application/json" },
      body: reset ? undefined : JSON.stringify({ template: drafts[item.key] }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setStatus(`Échec : ${body.detail ?? `HTTP ${res.status}`}`);
      return;
    }
    setStatus(reset ? "Template par défaut restauré." : "Nouvelle version activée.");
    await load();
  };

  if (!items) return <p className="flex gap-2 text-xs text-fg-muted"><Spinner /> Chargement…</p>;
  if (!expert) return <Banner tone="neutral">Activez le mode Expert pour inspecter et modifier les prompts.</Banner>;
  if (!items.length) return <Banner tone="bad">API des prompts indisponible.</Banner>;
  const visibleItems = items.filter(() => groups.includes(promptGroup()) && promptGroup() === activeGroup);
  const group = PROMPT_GROUPS.find((item) => item.id === activeGroup) ?? PROMPT_GROUPS[0];

  return (
    <div className="mt-6 space-y-4">
      {status && <Banner tone="neutral">{status}</Banner>}
      <Banner tone="neutral">
        Les modifications sont versionnées et appliquées aux prochaines requêtes.
      </Banner>
      {groups.length > 1 && <nav className="flex flex-wrap gap-2 rounded-xl border border-edge bg-surface/50 p-2" aria-label="Familles d’agents">
        {PROMPT_GROUPS.filter((item) => groups.includes(item.id)).map((item) => {
          const count = items.filter(() => promptGroup() === item.id).length;
          return <button key={item.id} onClick={() => setActiveGroup(item.id)}
            className={"cursor-pointer rounded-lg border px-3 py-2 text-sm " + (activeGroup === item.id ? "border-accent bg-surface-2 text-foreground" : "border-edge text-fg-muted")}>
            {item.label} <span className="ml-1 text-[10px] text-fg-faint">{count}</span>
          </button>;
        })}
      </nav>}
      <div>
        <h3 className="text-sm font-semibold text-foreground">{group.label}</h3>
        <p className="mt-1 text-xs text-fg-muted">{group.description}</p>
      </div>
      {visibleItems.map((item) => (
        <section key={item.key} className="rounded-xl border border-edge bg-surface/50 p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold text-foreground">{item.role}</h3>
              <p className="text-xs text-fg-muted">{item.description}</p>
            </div>
            <span className="rounded-full border border-edge px-2 py-1 text-[10px] text-fg-faint">
              {item.editable ? `v${item.version} · ${item.overridden ? "personnalisé" : "défaut"}` : "verrouillé"}
            </span>
          </div>
          <textarea value={drafts[item.key] ?? ""} readOnly={!item.editable}
            onChange={(e) => setDrafts({ ...drafts, [item.key]: e.target.value })}
            className="mt-3 min-h-56 w-full rounded-lg border border-edge bg-background p-3 font-mono text-xs text-foreground focus:border-accent focus:outline-none" />
          {!!item.variables.length && (
            <p className="mt-2 text-[11px] text-fg-faint">
              Variables obligatoires : {item.variables.map((v) => `{${v}}`).join(", ")}
            </p>
          )}
          {item.editable && (
            <div className="mt-3 flex gap-2">
              <button onClick={() => void act(item)}
                className="cursor-pointer rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-background">
                Enregistrer une version
              </button>
              <button onClick={() => void act(item, true)}
                className="cursor-pointer rounded-lg border border-edge px-3 py-1.5 text-xs text-fg-muted">
                Restaurer le défaut
              </button>
            </div>
          )}
        </section>
      ))}
    </div>
  );
}
