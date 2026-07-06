/** Formats partagés du front. */

/** Nombre [0,1] → chaîne compacte à 2 décimales (jauges, scores). */
export function fmt(v: number): string {
  return v.toFixed(2);
}
