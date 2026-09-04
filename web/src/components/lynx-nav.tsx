"use client";

/** Navigation interne de l'espace AI for Requirements : permet au chat
 * baseline d'ouvrir une exigence dans l'onglet Matrice (citations -> arbre).
 * Fourni par la page requirements ; absent (null) dans le monde RAG, où les
 * passages n'affichent donc aucun bouton d'ouverture. */

import { createContext, useContext } from "react";

export type LynxNav = {
  /** Bascule sur l'onglet Matrice et sélectionne l'exigence. */
  openRequirement: (reqId: string) => void;
  /** Matrice, exigence sélectionnée ET éditeur focalisé : préparer une
   * modification depuis une réponse du chat (l'analyse d'impact suit). */
  editRequirement: (reqId: string) => void;
  /** Bascule sur l'onglet Chat avec une question pré-remplie sur l'exigence. */
  askAboutRequirement: (reqId: string) => void;
};

export const LynxNavContext = createContext<LynxNav | null>(null);

export function useLynxNav(): LynxNav | null {
  return useContext(LynxNavContext);
}
