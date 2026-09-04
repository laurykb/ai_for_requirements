"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { LynxChat } from "@/components/lynx-chat";
import { LynxConversion } from "@/components/lynx-conversion";
import { LynxInfo } from "@/components/lynx-info";
import { LynxNavContext } from "@/components/lynx-nav";
import { LynxRuns } from "@/components/lynx-runs";
import { Requirements } from "@/components/requirements";
import { getJSON } from "@/lib/api";
import { Panel, Spinner } from "@/components/ui";

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

export type LynxTab = "pilotage" | "conversion" | "exigences" | "qualite" | "chat" | "suivi" | "parametres";

const TABS: LynxTab[] = ["pilotage", "conversion", "exigences", "qualite", "chat", "suivi", "parametres"];

type DashboardData = {
  health: { n: number; broken_links_count: number; roots_count: number };
  queue: { total: number; by_priority: Record<string, number>; by_code: Record<string, number> };
  impact: { changed: string[]; impacted: string[] };
  versions: { versions: { version_id: string; created_at: string; reason: string; n: number }[] };
};

function BaselineDashboard({ onOpen }: { onOpen: (tab: LynxTab) => void }) {
  const [data, setData] = useState<DashboardData | null>(null);
  useEffect(() => {
    let cancelled = false;
    Promise.all([
      getJSON<DashboardData["health"]>("/api/lynx/corpus/health"),
      getJSON<DashboardData["queue"]>("/api/lynx/corpus/issues"),
      getJSON<DashboardData["impact"]>("/api/lynx/corpus/impact"),
      getJSON<DashboardData["versions"]>("/api/lynx/corpus/versions"),
    ]).then(([health, queue, impact, versions]) => {
      if (!cancelled) setData({ health, queue, impact, versions });
    }).catch(() => { if (!cancelled) setData(null); });
    return () => { cancelled = true; };
  }, []);
  if (!data) return <p className="flex items-center gap-2 py-8 text-xs text-fg-muted"><Spinner /> Chargement du pilotage…</p>;
  const lastVersion = data.versions.versions[0];
  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div><p className="text-[11px] uppercase tracking-[0.16em] text-fg-faint">Baseline active</p><h1 className="text-xl font-semibold text-foreground">Pilotage des exigences</h1></div>
      </header>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <Metric label="Exigences actives" value={data.health.n} detail={lastVersion ? "Version " + lastVersion.version_id : "Baseline initiale"} />
        <Metric label="Contrôles structurels" value={data.queue.total} detail={(data.queue.by_priority.critical || 0) + " critiques · " + (data.queue.by_priority.high || 0) + " hautes"} tone="warn" />
        <Metric label="Liens documentaires" value={data.health.broken_links_count} detail={data.health.broken_links_count ? "liens à remapper" : "aucun lien cassé détecté"} tone={data.health.broken_links_count ? "warn" : "neutral"} />
        <Metric label="Dernier changement" value={data.impact.changed.length} detail={data.impact.impacted.length + " exigences impactées"} />
      </div>
      <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
        <Panel><h2 className="text-sm font-semibold text-foreground">Prochaines actions</h2><div className="mt-3 divide-y divide-edge">
          <ActionRow title="Corriger les liens documentaires" count={data.queue.by_code.BROKEN_LINK || 0} onClick={() => onOpen("exigences")} />
          <ActionRow title="Rattacher les exigences isolées" count={data.queue.by_code.NO_RELATION || 0} onClick={() => onOpen("exigences")} />
          <ActionRow title="Revoir le dernier périmètre d’impact" count={data.impact.impacted.length} onClick={() => onOpen("exigences")} />
        </div></Panel>
        <Panel><h2 className="text-sm font-semibold text-foreground">Parcours</h2><div className="mt-3 grid gap-2">
          <DashboardButton label="Importer ou comparer une baseline" onClick={() => onOpen("conversion")} />
          <DashboardButton label="Traiter la file qualité" onClick={() => onOpen("exigences")} />
          <DashboardButton label="Auditer la baseline" onClick={() => onOpen("qualite")} />
          <DashboardButton label="Interroger la baseline" onClick={() => onOpen("chat")} />
          <DashboardButton label="Consulter l’historique" onClick={() => onOpen("suivi")} />
        </div></Panel>
      </div>
    </div>
  );
}
function Metric({ label, value, detail, tone = "neutral" }: { label: string; value: number | string; detail: string; tone?: "neutral" | "warn" | "good" }) {
  return <Panel className={tone === "warn" ? "border-warn/30" : tone === "good" ? "border-good/30" : ""}><p className="text-[10px] uppercase tracking-[0.14em] text-fg-faint">{label}</p><p className="mt-1 font-mono text-2xl text-foreground">{value}</p><p className="mt-1 text-[11px] text-fg-muted">{detail}</p></Panel>;
}
function ActionRow({ title, count, onClick }: { title: string; count: number; onClick: () => void }) {
  return <button onClick={onClick} className="flex w-full items-center gap-3 py-3 text-left text-xs text-fg-muted hover:text-foreground"><span className="flex-1">{title}</span><span className="font-mono text-warn">{count}</span><span>→</span></button>;
}
function DashboardButton({ label, onClick }: { label: string; onClick: () => void }) {
  return <button onClick={onClick} className="rounded-lg border border-edge bg-surface-2 px-3 py-2 text-left text-xs text-fg-muted hover:border-accent/50 hover:text-foreground">{label} →</button>;
}

function RequirementsPageInner() {
  const router = useRouter();
  const params = useSearchParams();
  const requestedTab = params.get("tab");
  // Compatibilité des favoris créés avant la fusion Matrice → Exigences.
  const urlTab = (requestedTab === "matrice" ? "exigences" : requestedTab) as LynxTab | null;
  const tab: LynxTab = urlTab && TABS.includes(urlTab) ? urlTab : "pilotage";

  const setTab = useCallback((t: LynxTab) => {
    router.replace(t === "pilotage" ? "/requirements" : `/requirements?tab=${t}`,
                   { scroll: false });
  }, [router]);

  // Objets recréés à chaque clic (même exigence comprise) : c'est l'identité
  // de l'objet qui déclenche la re-sélection / le pré-remplissage.
  const [focusReq, setFocusReq] = useState<{ id: string; edit?: boolean } | null>(null);
  const [chatPrefill, setChatPrefill] = useState<{ text: string } | null>(null);
  const nav = useMemo(() => ({
    openRequirement: (id: string) => { setFocusReq({ id }); setTab("exigences"); },
    editRequirement: (id: string) => { setFocusReq({ id, edit: true }); setTab("exigences"); },
    askAboutRequirement: (id: string) => {
      setChatPrefill({ text: `Explique l'exigence ${id} : son rôle, ses liens de dérivation et ce qui la vérifie.` });
      setTab("chat");
    },
  }), [setTab]);

  return (
    <div className="lynx-wide rise-in py-2">
      <LynxNavContext.Provider value={nav}>
        {tab === "pilotage" && <BaselineDashboard onOpen={setTab} />}
        <div className={tab === "conversion" ? "" : "hidden"}>
          <LynxConversion active={tab === "conversion"} />
        </div>
        <div className={tab === "exigences" || tab === "qualite" ? "" : "hidden"}>
          <Requirements focusReq={focusReq} active={tab === "exigences" || tab === "qualite"}
                        section={tab === "qualite" ? "quality" : "catalogue"} />
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
