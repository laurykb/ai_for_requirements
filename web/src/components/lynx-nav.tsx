"use client";

/** Navigation interne de l'espace AI for Requirements : permet au chat
 * baseline d'ouvrir une exigence dans l'onglet Matrice (citations -> arbre).
 * Fourni par la page requirements ; absent (null) dans le monde RAG, où les
 * passages n'affichent donc aucun bouton d'ouverture. */

import { createContext, useContext } from "react";

export type LynxNav = {
  /** Bascule sur l'onglet Matrice et sélectionne l'exigence. */
  openRequirement: (reqId: string) => void;
};

export const LynxNavContext = createContext<LynxNav | null>(null);

export function useLynxNav(): LynxNav | null {
  return useContext(LynxNavContext);
}
