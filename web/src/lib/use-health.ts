"use client";

/** Sonde du moteur local : /health interrogé en continu (toutes les 4 s).
 *
 * Sert au chat pour suspendre l'envoi quand Ollama (ou l'API) est arrêté —
 * par ex. pendant un redémarrage du service — et le réactiver dès le retour.
 * `since` (epoch ms) date le début de l'indisponibilité pour afficher le
 * délai à l'utilisateur. Mongo absent ne bloque PAS l'envoi : le chat
 * fonctionne sans persistance. */

import { useEffect, useState } from "react";

import { getJSON, type Health } from "@/lib/api";

const POLL_MS = 4000;

export type EngineState =
  | { kind: "checking" }
  | { kind: "ok" }
  | { kind: "down"; reason: string; since: number };

export function useEngineHealth(): EngineState {
  const [state, setState] = useState<EngineState>({ kind: "checking" });

  useEffect(() => {
    let cancelled = false;

    const probe = async () => {
      let reason: string | null = null;
      try {
        const h = await getJSON<Health>("/health");
        if (!h.services.ollama) reason = "Ollama est arrêté";
      } catch {
        reason = "L'API locale est injoignable";
      }
      if (cancelled) return;
      setState((prev) => {
        if (!reason) return { kind: "ok" };
        // Indisponibilité déjà datée : on garde l'origine du délai.
        if (prev.kind === "down") return { ...prev, reason };
        return { kind: "down", reason, since: Date.now() };
      });
    };

    probe();
    const timer = setInterval(probe, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return state;
}

function formatElapsed(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s} s`;
  return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")}`;
}

/** Délai écoulé, lisible : « 12 s » puis « 1 min 05 ». Mis à jour chaque seconde. */
export function useElapsedLabel(since: number | null): string {
  const [state, setState] = useState<{ since: number | null; label: string }>({
    since: null,
    label: "",
  });
  // Nouvelle indisponibilité : libellé remis à zéro pendant le rendu
  // (« adjusting state during render »), l'intervalle prend le relais.
  if (state.since !== since) {
    setState({ since, label: since === null ? "" : "0 s" });
  }
  useEffect(() => {
    if (since === null) return;
    const timer = setInterval(
      () => setState({ since, label: formatElapsed(Date.now() - since) }),
      1000,
    );
    return () => clearInterval(timer);
  }, [since]);
  return state.label;
}
