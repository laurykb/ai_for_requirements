"use client";

/** Interrupteur Simple/Expert du header. Expert = options de recherche,
 * passages récupérés, vérification, Observabilité. */

import { useEffect, useState } from "react";

import { loadPrefs, savePrefs } from "@/lib/prefs";

export function useExpert(): boolean {
  const [expert, setExpert] = useState(false);
  useEffect(() => {
    const sync = () => setExpert(loadPrefs().expert);
    sync();
    window.addEventListener("prefs-changed", sync);
    return () => window.removeEventListener("prefs-changed", sync);
  }, []);
  return expert;
}

export function ExpertToggle() {
  const expert = useExpert();
  return (
    <button
      onClick={() => savePrefs({ expert: !expert })}
      title="Mode expert : options de recherche, passages récupérés, vérification, observabilité. Désactivé = interface simple."
      aria-pressed={expert}
      className={`cursor-pointer rounded-full border px-2.5 py-0.5 text-xs transition-colors ${
        expert
          ? "border-accent/60 text-accent-bright"
          : "border-edge text-fg-faint hover:text-fg-muted"
      }`}
    >
      Expert
    </button>
  );
}
