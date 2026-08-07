"use client";

import { useState } from "react";

import { LynxChat } from "@/components/lynx-chat";
import { LynxInfo } from "@/components/lynx-info";
import { Requirements } from "@/components/requirements";
import { PanelTitle } from "@/components/ui";

/** AI for Requirements (LynX) : seconde lecture de la matrice d'exigences.
 * Trois onglets : la Matrice (travail quotidien), le Chat (interroger la
 * baseline d'exigences, moteur RAG à périmètre verrouillé) et les
 * Informations (prompts des agents + orchestration — pilotage de l'outil). */

export default function RequirementsPage() {
  const [tab, setTab] = useState<"matrice" | "chat" | "infos">("matrice");
  return (
    <div className="rise-in py-4">
      <div className="flex items-start justify-between gap-4">
        <PanelTitle
          kicker="Vérification d'exigences"
          title="AI for Requirements"
          hint="Des agents relisent votre matrice : impact d'une action, audit complet, corrections suggérées — raisonnement visible."
        />
        <div className="flex overflow-hidden rounded-lg border border-edge text-xs">
          {([["matrice", "Matrice"], ["chat", "Chat"], ["infos", "Informations"]] as const).map(([k, lbl]) => (
            <button key={k} onClick={() => setTab(k)}
                    className={`cursor-pointer px-3 py-1.5 transition-colors ${
                      tab === k ? "bg-accent/20 text-accent-bright" : "text-fg-muted hover:text-foreground"}`}>
              {lbl}
            </button>
          ))}
        </div>
      </div>
      {tab === "matrice" ? <Requirements /> : tab === "chat" ? <LynxChat /> : <LynxInfo />}
    </div>
  );
}
