"use client";

/** Préférences locales (localStorage) : mode expert + options de recherche.
 * `null` = laisser le défaut .env côté backend. */

export type Prefs = {
  expert: boolean;
  useMemory: boolean;
  parentChild: boolean | null;
  selfRag: boolean | null;
  systemPrompt: string | null;
};

const KEY = "ai_for_ssh_prefs";
const DEFAULTS: Prefs = { expert: false, useMemory: true, parentChild: null,
                          selfRag: null, systemPrompt: null };

export function loadPrefs(): Prefs {
  if (typeof window === "undefined") return DEFAULTS;
  try {
    return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) ?? "{}") };
  } catch {
    return DEFAULTS;
  }
}

export function savePrefs(p: Partial<Prefs>): Prefs {
  const next = { ...loadPrefs(), ...p };
  localStorage.setItem(KEY, JSON.stringify(next));
  // Les composants montés (header, chat…) se resynchronisent via cet événement.
  window.dispatchEvent(new CustomEvent("prefs-changed"));
  return next;
}
