"use client";

import { Suspense, useCallback, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { LynxBrowser } from "@/components/lynx-browser";
import { LynxChat } from "@/components/lynx-chat";
import { LynxInfo } from "@/components/lynx-info";
import { LynxNavContext } from "@/components/lynx-nav";
import { LynxRuns } from "@/components/lynx-runs";
import { Requirements } from "@/components/requirements";

/** AI for Requirements (LynX) : seconde lecture de la matrice d'exigences.
 * Les onglets vivent dans la barre de menu du haut (header-nav, liens
 * ?tab=…) : Matrice (travail quotidien), Exigences (navigateur en liste),
 * Chat (baseline, moteur RAG à périmètre verrouillé), Paramètres
 * (orchestration + prompts des agents). La page occupe toute la largeur
 * utile (classe lynx-wide) — la Matrice est l'outil de travail principal.
 * Le contexte LynxNav relie les onglets : une citation du chat ou une fiche
 * du navigateur ouvre l'exigence dans la Matrice ; l'inverse pré-remplit le
 * Chat. Matrice, Exigences et Chat restent montés (masqués CSS) : sélection,
 * audit et fil de conversation survivent aux allers-retours. */

export type LynxTab = "matrice" | "exigences" | "chat" | "suivi" | "parametres";

const TABS: LynxTab[] = ["matrice", "exigences", "chat", "suivi", "parametres"];

function RequirementsPageInner() {
  const router = useRouter();
  const params = useSearchParams();
  const urlTab = params.get("tab") as LynxTab | null;
  const tab: LynxTab = urlTab && TABS.includes(urlTab) ? urlTab : "matrice";

  const setTab = useCallback((t: LynxTab) => {
    router.replace(t === "matrice" ? "/requirements" : `/requirements?tab=${t}`,
                   { scroll: false });
  }, [router]);

  // Objets recréés à chaque clic (même exigence comprise) : c'est l'identité
  // de l'objet qui déclenche la re-sélection / le pré-remplissage.
  const [focusReq, setFocusReq] = useState<{ id: string; edit?: boolean } | null>(null);
  const [chatPrefill, setChatPrefill] = useState<{ text: string } | null>(null);
  const nav = useMemo(() => ({
    openRequirement: (id: string) => { setFocusReq({ id }); setTab("matrice"); },
    editRequirement: (id: string) => { setFocusReq({ id, edit: true }); setTab("matrice"); },
    askAboutRequirement: (id: string) => {
      setChatPrefill({ text: `Explique l'exigence ${id} : son rôle, ses liens de dérivation et ce qui la vérifie.` });
      setTab("chat");
    },
  }), [setTab]);

  return (
    <div className="lynx-wide rise-in py-2">
      <LynxNavContext.Provider value={nav}>
        <div className={tab === "matrice" ? "" : "hidden"}>
          <Requirements focusReq={focusReq} />
        </div>
        <div className={tab === "exigences" ? "" : "hidden"}>
          <LynxBrowser active={tab === "exigences"} />
        </div>
        <div className={tab === "chat" ? "" : "hidden"}>
          <LynxChat prefill={chatPrefill} />
        </div>
        <div className={tab === "suivi" ? "" : "hidden"}>
          <LynxRuns active={tab === "suivi"} />
        </div>
        {tab === "parametres" && <LynxInfo />}
      </LynxNavContext.Provider>
    </div>
  );
}

export default function RequirementsPage() {
  // useSearchParams impose une frontière Suspense (App Router).
  return (
    <Suspense fallback={<div className="py-8" />}>
      <RequirementsPageInner />
    </Suspense>
  );
}
