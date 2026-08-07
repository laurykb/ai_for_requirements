"use client";

import { useMemo, useState } from "react";

import { LynxChat } from "@/components/lynx-chat";
import { LynxInfo } from "@/components/lynx-info";
import { LynxNavContext } from "@/components/lynx-nav";
import { Requirements } from "@/components/requirements";
import { PanelTitle } from "@/components/ui";

/** AI for Requirements (LynX) : seconde lecture de la matrice d'exigences.
 * Trois onglets : la Matrice (travail quotidien), le Chat (interroger la
 * baseline d'exigences, moteur RAG à périmètre verrouillé) et les
 * Informations (prompts des agents + orchestration — pilotage de l'outil).
 * Le contexte LynxNav relie les deux premiers : une citation du chat peut
 * ouvrir l'exigence correspondante dans l'arbre de la Matrice. */

export default function RequirementsPage() {
  const [tab, setTab] = useState<"matrice" | "chat" | "infos">("matrice");
  // Objet recréé à chaque clic (même exigence comprise) : c'est l'identité de
  // l'objet qui déclenche la re-sélection côté Matrice.
  const [focusReq, setFocusReq] = useState<{ id: string } | null>(null);
  const nav = useMemo(() => ({
    openRequirement: (id: string) => { setFocusReq({ id }); setTab("matrice"); },
  }), []);
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
      <LynxNavContext.Provider value={nav}>
        {/* Matrice et Chat restent montés (masqués par CSS) : la sélection,
            le verdict, l'audit ET le fil de conversation survivent aux
            allers-retours citations ↔ arbre. */}
        <div className={tab === "matrice" ? "" : "hidden"}>
          <Requirements focusReq={focusReq} />
        </div>
        <div className={tab === "chat" ? "" : "hidden"}>
          <LynxChat />
        </div>
        {tab === "infos" && <LynxInfo />}
      </LynxNavContext.Provider>
    </div>
  );
}
