"use client";

/** Barre latérale des conversations persistées : nouvelle conversation,
 * ouverture, renommage inline et suppression (les appels REST sessions
 * vivent ici ; l'état du fil reste dans index.tsx). */

import { useState } from "react";

import { API_BASE } from "@/lib/api";
import type { SessionInfo } from "@/lib/types";

/** Une conversation dans la barre latérale (renommage inline, suppression). */
function SessionRow({ s, active, onOpen, onRename, onDelete }: {
  s: SessionInfo; active: boolean;
  onOpen: () => void; onRename: (t: string) => void; onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(s.title);
  return (
    <div
      className={`group flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs transition-colors ${
        active ? "bg-accent/15 text-foreground" : "text-fg-muted hover:bg-surface-2"}`}
    >
      {editing ? (
        <input
          autoFocus
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onBlur={() => { setEditing(false); onRename(title); }}
          onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
          className="w-full rounded border border-accent/50 bg-surface px-1 py-0.5 text-xs text-foreground focus:outline-none"
        />
      ) : (
        <>
          <button onClick={onOpen}
                  className="min-w-0 flex-1 cursor-pointer truncate text-left"
                  title={s.title}>
            {s.title || "Conversation"}
          </button>
          <button onClick={() => setEditing(true)} title="Renommer"
                  className="hidden cursor-pointer px-1 text-fg-faint hover:text-foreground group-hover:block">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" aria-hidden>
              <path d="M4 20h4L19 9l-4-4L4 16v4Z" stroke="currentColor" strokeWidth="2"
                    strokeLinejoin="round" />
            </svg>
          </button>
          <button onClick={onDelete} title="Supprimer"
                  className="hidden cursor-pointer px-1 text-fg-faint hover:text-bad group-hover:block">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" aria-hidden>
              <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2"
                    strokeLinecap="round" />
            </svg>
          </button>
        </>
      )}
    </div>
  );
}

/** La colonne « Conversations » complète (masquée sous md). */
export function SessionsSidebar({ sessions, sessionId, onNew, onOpen, refreshSessions }: {
  sessions: SessionInfo[];
  sessionId: string | null;
  onNew: () => void;
  onOpen: (s: SessionInfo) => void;
  refreshSessions: () => void;
}) {
  return (
    <aside className="hidden w-56 shrink-0 flex-col gap-2 md:flex">
      <button
        onClick={onNew}
        className="cursor-pointer rounded-lg border border-edge px-3 py-2 text-left text-xs text-fg-muted transition-colors hover:border-accent/50 hover:text-foreground"
      >
        + Nouvelle conversation
      </button>
      <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto pr-1">
        {sessions.map((s) => (
          <SessionRow
            key={s.id}
            s={s}
            active={s.id === sessionId}
            onOpen={() => onOpen(s)}
            onRename={async (t) => {
              await fetch(`${API_BASE}/api/sessions/${s.id}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ title: t }),
              }).catch(() => null);
              refreshSessions();
            }}
            onDelete={async () => {
              await fetch(`${API_BASE}/api/sessions/${s.id}`, { method: "DELETE" })
                .catch(() => null);
              if (s.id === sessionId) onNew();
              refreshSessions();
            }}
          />
        ))}
        {sessions.length === 0 && (
          <p className="px-2 py-1.5 text-[11px] text-fg-faint">
            Vos conversations persistées apparaîtront ici.
          </p>
        )}
      </div>
    </aside>
  );
}
