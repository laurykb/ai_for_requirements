/** Hiérarchie de niveaux dérivée du corpus (plus de tableaux figés).
 * Non-régression : L0–L5 gardent exactement les teintes et libellés d'origine ;
 * au-delà, une échelle de teintes déterministe prend le relais. */

export type NiveauCat = { niveau: number; label: string };

export const BASE_COLORS = ["#818cf8", "#60a5fa", "#22d3ee", "#34d399", "#fbbf24", "#fb7185"];
export const BASE_LABELS = ["L0 · Besoin", "L1 · Système", "L2 · Sous-système",
                            "L3 · Composant", "L4 · Configuration", "L5 · Test"];

export function maxNiveau(reqs: { niveau: number }[]): number {
  return reqs.reduce((m, r) => Math.max(m, r.niveau ?? 0), 0);
}

/** Couleur d'un niveau : hex d'origine pour n<6, sinon teinte HSL répartie. */
export function couleurNiveau(n: number, nMax: number): string {
  if (n < BASE_COLORS.length) return BASE_COLORS[n];
  const extra = Math.max(1, nMax - BASE_COLORS.length + 1);
  const t = (n - BASE_COLORS.length + 1) / extra;      // 0..1
  const hue = Math.round(280 - 200 * t);               // violet → cyan
  return `hsl(${hue} 70% 65%)`;
}

/** Libellé d'un niveau : catalogue du corpus si présent, sinon repli. */
export function libelleNiveau(n: number, cat?: NiveauCat[]): string {
  const c = cat?.find((e) => e.niveau === n);
  if (c) return `L${n} · ${c.label}`;
  if (n < BASE_LABELS.length) return BASE_LABELS[n];
  return `L${n}`;
}
