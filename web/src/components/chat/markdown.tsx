"use client";

/** Rendu Markdown des réponses avec attribution par affirmation :
 *
 * - les marqueurs [n] émis par le modèle deviennent des puces discrètes
 *   cliquables (clic → ouvre et surligne le passage n) ;
 * - les affirmations `non_sourcee` (passe post-hoc) sont surlignées d'un
 *   fond ambre discret avec info-bulle.
 *
 * Le contenu passe par ReactMarkdown : on transforme les nœuds TEXTE de
 * l'arbre HTML via un petit plugin rehype maison (aucune dépendance de
 * plus) — post-processing propre qui ne casse ni le gras, ni les listes,
 * ni les blocs de code (ignorés). */

import ReactMarkdown from "react-markdown";
import type { Element, Root, Text } from "hast";

import type { Affirmation } from "@/lib/types";

type HastChild = Element["children"][number];

/** Échappe une chaîne pour l'injecter dans une RegExp. */
const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Construit la RegExp d'une affirmation : tolère les espaces multiples et
 * les marqueurs [n] intercalés (le texte de l'affirmation est recopié de la
 * réponse SANS ses marqueurs). Les caractères d'emphase Markdown sont
 * retirés (ils n'existent plus dans le texte rendu). */
function claimPattern(texte: string): RegExp | null {
  const cleaned = texte.replace(/\[\d{1,2}\]/g, " ").replace(/[*_`]/g, " ").trim();
  const tokens = cleaned.split(/\s+/).filter(Boolean).map(escapeRe);
  if (!tokens.length) return null;
  return new RegExp(tokens.join("(?:\\s*\\[\\d{1,2}\\])*\\s+"), "i");
}

/** Parcourt les nœuds texte (hors code/pre/a) et remplace chacun par la
 * liste de nœuds retournée (null = inchangé). */
function walkTexts(
  node: Root | Element,
  replace: (value: string) => HastChild[] | null,
) {
  const children = node.children as HastChild[];
  for (let i = 0; i < children.length; i++) {
    const child = children[i];
    if (child.type === "element") {
      if (["code", "pre", "a"].includes(child.tagName)) continue;
      walkTexts(child, replace);
    } else if (child.type === "text") {
      const out = replace(child.value);
      if (out) {
        children.splice(i, 1, ...out);
        i += out.length - 1;
      }
    }
  }
}

const text = (value: string): Text => ({ type: "text", value });

const citeLink = (n: number): Element => ({
  type: "element",
  tagName: "a",
  properties: { href: `#src-${n}` },
  children: [text(String(n))],
});

const reqLink = (id: string): Element => ({
  type: "element",
  tagName: "a",
  properties: { href: `#req-${id}` },
  children: [text(id)],
});

const unsourcedMark = (value: string): Element => ({
  type: "element",
  tagName: "mark",
  properties: {
    className: ["unsourced"],
    title: "Affirmation non sourcée dans les documents",
  },
  children: [text(value)],
});

/** Plugin rehype : surligne les affirmations non sourcées, transforme les
 * marqueurs [n] (1 ≤ n ≤ maxCite) en liens internes #src-n, puis (chat
 * baseline) les identifiants d'exigences cités en liens vers la Matrice. */
function rehypeAttribution(maxCite: number, unsourced: string[], reqIds: string[]) {
  const patterns = unsourced
    .map(claimPattern)
    .filter((p): p is RegExp => p !== null);
  // Identifiants les plus longs d'abord (REQ-10 avant REQ-1) ; frontières
  // strictes pour ne jamais couper un identifiant voisin.
  const reqRe = reqIds.length
    ? new RegExp(`(?<![\\w-])(?:${[...reqIds].sort((a, b) => b.length - a.length)
        .map(escapeRe).join("|")})(?![\\w-])`, "g")
    : null;
  return () => (tree: Root) => {
    // 1) Fond ambre sur les affirmations non sourcées (au plus une fois
    //    chacune ; une affirmation coupée par de la mise en forme n'est pas
    //    retrouvée dans un seul nœud texte -> pas de surlignage, les
    //    compteurs du bloc de vérification restent la source de vérité).
    const pending = [...patterns];
    if (pending.length) {
      walkTexts(tree, (value) => {
        for (let p = 0; p < pending.length; p++) {
          const m = pending[p].exec(value);
          if (m && m[0].trim()) {
            pending.splice(p, 1);
            const start = m.index;
            const end = start + m[0].length;
            const out: HastChild[] = [];
            if (start > 0) out.push(text(value.slice(0, start)));
            out.push(unsourcedMark(value.slice(start, end)));
            if (end < value.length) out.push(text(value.slice(end)));
            return out;
          }
        }
        return null;
      });
    }
    // 2) Marqueurs [n] -> liens cliquables (uniquement si n est un numéro
    //    de passage valide ; sinon le texte reste tel quel).
    if (maxCite > 0) {
      const re = /\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]/g;
      walkTexts(tree, (value) => {
        re.lastIndex = 0;
        if (!re.test(value)) return null;
        re.lastIndex = 0;
        const out: HastChild[] = [];
        let last = 0;
        let m: RegExpExecArray | null;
        while ((m = re.exec(value)) !== null) {
          const nums = m[1].split(",").map((s) => Number(s.trim()));
          if (nums.some((n) => n < 1 || n > maxCite)) continue; // pas un marqueur
          if (m.index > last) out.push(text(value.slice(last, m.index)));
          nums.forEach((n) => out.push(citeLink(n)));
          last = m.index + m[0].length;
        }
        if (!out.length) return null;
        if (last < value.length) out.push(text(value.slice(last)));
        return out;
      });
    }
    // 3) Identifiants d'exigences (chat baseline) -> liens #req-<id> vers la
    //    Matrice. Seuls les identifiants réellement présents dans les
    //    passages de la réponse sont liés (jamais de lien fantôme).
    if (reqRe) {
      walkTexts(tree, (value) => {
        reqRe.lastIndex = 0;
        if (!reqRe.test(value)) return null;
        reqRe.lastIndex = 0;
        const out: HastChild[] = [];
        let last = 0;
        let m: RegExpExecArray | null;
        while ((m = reqRe.exec(value)) !== null) {
          if (m.index > last) out.push(text(value.slice(last, m.index)));
          out.push(reqLink(m[0]));
          last = m.index + m[0].length;
        }
        if (last < value.length) out.push(text(value.slice(last)));
        return out;
      });
    }
  };
}

export function AnswerMarkdown({ content, maxCite = 0, affirmations, onCiteClick,
                                 reqIds, onReqClick }: {
  content: string;
  /** Nombre de passages numérotés (contrat marqueur↔passage : [n] valide si n ≤ maxCite). */
  maxCite?: number;
  /** Affirmations de la passe d'attribution (les `non_sourcee` sont surlignées). */
  affirmations?: Affirmation[];
  /** Clic sur un marqueur [n] — absent pendant le streaming (puces non cliquables). */
  onCiteClick?: (n: number) => void;
  /** Chat baseline : identifiants d'exigences des passages — cités dans le
   * texte, ils deviennent des liens vers la Matrice. */
  reqIds?: string[];
  onReqClick?: (id: string) => void;
}) {
  const unsourced = (affirmations ?? [])
    .filter((a) => a.statut === "non_sourcee")
    .map((a) => a.texte);
  return (
    <ReactMarkdown
      rehypePlugins={[rehypeAttribution(maxCite, unsourced, onReqClick ? (reqIds ?? []) : [])]}
      components={{
        a: ({ node, href, children, ...rest }) => {
          void node;
          const m = /^#src-(\d+)$/.exec(href ?? "");
          if (m) {
            const n = Number(m[1]);
            if (!onCiteClick) {
              return <sup className="cite-marker">{n}</sup>;
            }
            return (
              <button
                type="button"
                className="cite-marker"
                title={`Voir le passage [${n}]`}
                aria-label={`Voir le passage ${n}`}
                onClick={() => onCiteClick(n)}
              >
                {n}
              </button>
            );
          }
          const r = /^#req-(.+)$/.exec(href ?? "");
          if (r && onReqClick) {
            const id = decodeURIComponent(r[1]);
            return (
              <button
                type="button"
                className="req-link"
                title={`Ouvrir ${id} dans la Matrice`}
                onClick={() => onReqClick(id)}
              >
                {id}
              </button>
            );
          }
          return <a href={href} {...rest}>{children}</a>;
        },
      }}
    >
      {content}
    </ReactMarkdown>
  );
}
