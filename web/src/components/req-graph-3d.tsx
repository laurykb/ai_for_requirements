"use client";

/** Vue 3D de la matrice (bascule depuis la vue 2D par niveaux).
 * Mêmes codes que la 2D : couleur = niveau, halo d'état (sélection aqua,
 * citée blanc, impactée/attention ambre, bloquante rouge), clic = sélection.
 * Les niveaux L0..L5 sont des COUCHES horizontales empilées (y fixé) ; la
 * disposition dans chaque couche est laissée à la simulation de forces.
 * 100 % local : three.js est embarqué dans le bundle. */

import { memo, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph3D from "react-force-graph-3d";
import SpriteText from "three-spritetext";

import { LEVEL_LABELS, NIVEAU_COLORS, STATE_HEX, type Req } from "@/components/req-graph";

type GNode = {
  id: string; niveau: number; state: string; texte: string;
  fy: number;
};
type GLink = { source: string; target: string; dashed: boolean };

export const ReqGraph3D = memo(function ReqGraph3D({
  corpus, selected, impacted, flaggedSev, mentioned, onSelect,
}: {
  corpus: Req[];
  selected: string | null;
  impacted: Set<string>;
  flaggedSev: Map<string, "bad" | "warn">;
  mentioned: Set<string>;
  onSelect: (id: string) => void;
}) {
  // Largeur mesurée du cadre (la lib se dimensionne en pixels).
  const boxRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const data = useMemo(() => {
    const nodes: GNode[] = corpus.map((r) => {
      const lvl = Math.max(0, Math.min(5, r.niveau ?? 0));
      return {
        id: r.id, niveau: lvl, texte: r.texte ?? "",
        state: selected === r.id ? "selected"
          : mentioned.has(r.id) ? "mentioned"
          : flaggedSev.get(r.id) === "bad" ? "flagged-bad"
          : flaggedSev.get(r.id) === "warn" ? "flagged-warn"
          : impacted.has(r.id) ? "impacted" : "none",
        fy: 220 - lvl * 88, // couche par niveau (L0 en haut)
      };
    });
    const ids = new Set(nodes.map((n) => n.id));
    const links: GLink[] = [];
    for (const r of corpus) {
      if (r.parent_id && ids.has(r.parent_id))
        links.push({ source: r.parent_id, target: r.id, dashed: false });
      for (const lk of r.links ?? [])
        if (ids.has(lk.target))
          links.push({ source: lk.target, target: r.id, dashed: true });
    }
    return { nodes, links };
  }, [corpus, selected, impacted, flaggedSev, mentioned]);

  return (
    <div className="flex flex-col gap-1.5">
      <div ref={boxRef}
           className="h-[calc(100vh-22rem)] min-h-[560px] overflow-hidden rounded-xl border border-edge bg-surface">
        {size && (
          <ForceGraph3D
            width={size.w}
            height={size.h}
            graphData={data}
            backgroundColor="rgba(0,0,0,0)"
            showNavInfo={false}
            nodeLabel={(n: object) => {
              const g = n as GNode;
              return `<div style="max-width:280px;font-size:11px"><b>${g.id}</b> · ${
                LEVEL_LABELS[g.niveau]}<br/>${g.texte}</div>`;
            }}
            nodeThreeObject={(n: object) => {
              const g = n as GNode;
              const label = new SpriteText(g.id);
              label.color = STATE_HEX[g.state] ?? STATE_HEX.mentioned;
              label.backgroundColor = "rgba(20,26,58,0.85)";
              label.padding = 1.5;
              label.borderRadius = 2;
              label.borderWidth = g.state === "none" ? 0 : 0.4;
              label.borderColor = STATE_HEX[g.state] ?? "transparent";
              label.textHeight = g.state === "selected" ? 4.5 : 3.4;
              label.fontFace = "monospace";
              return label;
            }}
            nodeThreeObjectExtend={false}
            nodeColor={(n: object) => STATE_HEX[(n as GNode).state] ?? NIVEAU_COLORS[(n as GNode).niveau]}
            onNodeClick={(n: object) => onSelect(String((n as GNode).id))}
            linkColor={(l: object) => ((l as GLink).dashed ? "rgba(93,191,213,0.55)" : "rgba(148,163,214,0.45)")}
            linkOpacity={0.55}
            linkWidth={0.6}
            enableNodeDrag={false}
            warmupTicks={60}
            cooldownTicks={120}
          />
        )}
      </div>
      <p className="flex flex-wrap gap-x-4 gap-y-1 px-1 text-[10px] text-fg-faint">
        <span>glisser : pivoter · molette : zoom · clic droit : déplacer · clic : sélectionner</span>
        <span><span style={{ color: "var(--accent-bright)" }}>●</span> sélection</span>
        <span><span style={{ color: "var(--foreground-bright)" }}>●</span> citée par la synthèse</span>
        <span><span style={{ color: "var(--warn)" }}>●</span> impactée / attention</span>
        <span><span style={{ color: "var(--bad)" }}>●</span> bloquante</span>
      </p>
    </div>
  );
});
