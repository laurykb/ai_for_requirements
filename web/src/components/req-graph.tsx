"use client";

/** Graphe de la matrice d'exigences (DAG du cycle en V) : positions FIXES
 * (x = rang dans le niveau, y = niveau), couleur par niveau, bordure selon
 * l'état (sélection aqua, impacté ambre, signalé rouge). Clic = sélection. */

import { useMemo } from "react";
import {
  Background,
  ReactFlow,
  type Edge,
  type Node,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

export type Req = {
  id: string;
  niveau: number;
  type?: string;
  domaine?: string;
  texte?: string;
  parent_id?: string | null;
  test_status?: string;
  links?: { type: string; target: string }[];
};

// Couleurs par niveau L0..L5 (mêmes que lynx/app.py).
const NIVEAU_COLORS = ["#4338CA", "#2563EB", "#0891B2", "#059669", "#D97706", "#E11D48"];

const LEVELS = ["L0 Besoin", "L1 Système", "L2 Sous-système", "L3 Composant",
                "L4 Config.", "L5 Test"];

export function ReqGraph({
  corpus,
  selected,
  impacted,
  flagged,
  onSelect,
}: {
  corpus: Req[];
  selected: string | null;
  impacted: Set<string>;
  flagged: Set<string>;
  onSelect: (id: string) => void;
}) {
  const { nodes, edges } = useMemo(() => {
    const byLevel = new Map<number, Req[]>();
    for (const r of corpus) {
      const lvl = Math.max(0, Math.min(5, r.niveau ?? 0));
      byLevel.set(lvl, [...(byLevel.get(lvl) ?? []), r]);
    }
    const nodes: Node[] = [];
    for (const [lvl, reqs] of byLevel) {
      reqs.sort((a, b) => a.id.localeCompare(b.id));
      reqs.forEach((r, i) => {
        const border = selected === r.id ? "#7fd4e6"
          : flagged.has(r.id) ? "#f87171"
          : impacted.has(r.id) ? "#fbbf24" : "transparent";
        nodes.push({
          id: r.id,
          position: { x: i * 170, y: lvl * 150 },
          data: { label: r.id },
          draggable: false,
          connectable: false,
          style: {
            background: NIVEAU_COLORS[lvl],
            color: "#fff",
            border: `2px solid ${border}`,
            borderRadius: 8,
            fontSize: 11,
            fontFamily: "var(--font-jetbrains), monospace",
            padding: "4px 8px",
            width: "auto",
          },
        });
      });
    }
    const edges: Edge[] = [];
    for (const r of corpus) {
      if (r.parent_id)
        edges.push({ id: `p-${r.parent_id}-${r.id}`, source: r.parent_id, target: r.id,
                     style: { stroke: "rgba(148,163,214,0.45)" } });
      for (const lk of r.links ?? [])
        edges.push({ id: `l-${lk.target}-${r.id}-${lk.type}`, source: lk.target, target: r.id,
                     style: { stroke: "rgba(148,163,214,0.25)", strokeDasharray: "5 4" },
                     label: lk.type, labelStyle: { fill: "#636b85", fontSize: 9 },
                     labelBgStyle: { fill: "transparent" } });
    }
    return { nodes, edges };
  }, [corpus, selected, impacted, flagged]);

  const onNodeClick: NodeMouseHandler = (_e, node) => onSelect(node.id);

  return (
    <div className="h-[440px] rounded-xl border border-edge bg-surface">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodeClick={onNodeClick}
        fitView
        proOptions={{ hideAttribution: true }}
        nodesDraggable={false}
        nodesConnectable={false}
        colorMode="dark"
        style={{ background: "transparent" }}
      >
        <Background color="rgba(148,163,214,0.12)" gap={24} />
      </ReactFlow>
      <p className="flex flex-wrap gap-3 px-3 py-2 text-[11px] text-fg-faint">
        {LEVELS.map((l, i) => (
          <span key={l} className="flex items-center gap-1.5">
            <span className="inline-block h-2 w-2 rounded-full"
                  style={{ background: NIVEAU_COLORS[i] }} />
            {l}
          </span>
        ))}
        <span className="ml-auto">bordure ambre = impacté · rouge = signalé</span>
      </p>
    </div>
  );
}
