"use client";

/** La matrice est la scène : DAG du cycle en V, nœuds custom sobres
 * (pastille de niveau + id mono), étiquettes de niveau dans le flux,
 * positions fixes. États : sélection aqua (halo), impacté ambre, signalé
 * rouge. Clic = sélection. */

import { memo, useMemo } from "react";
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeMouseHandler,
  type NodeProps,
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

// Couleurs par niveau L0..L5 (héritées de LynX, éclaircies pour le fond nuit).
export const NIVEAU_COLORS = ["#818cf8", "#60a5fa", "#22d3ee", "#34d399", "#fbbf24", "#fb7185"];
export const LEVEL_LABELS = ["L0 · Besoin", "L1 · Système", "L2 · Sous-système",
                             "L3 · Composant", "L4 · Configuration", "L5 · Test"];

type NodeState = "none" | "selected" | "mentioned" | "flagged-bad" | "flagged-warn" | "impacted";
type ReqNodeData = { rid: string; niveau: number; state: NodeState };

const STATE_RING: Record<string, string> = {
  selected: "0 0 0 2px #7fd4e6, 0 0 18px rgba(93,191,213,0.45)",
  mentioned: "0 0 0 2px #eef1f8, 0 0 16px rgba(238,241,248,0.35)",
  impacted: "0 0 0 2px #fbbf24, 0 0 14px rgba(251,191,36,0.35)",
  "flagged-warn": "0 0 0 2px #fbbf24, 0 0 14px rgba(251,191,36,0.35)",
  "flagged-bad": "0 0 0 2px #f87171, 0 0 16px rgba(248,113,113,0.45)",
};

/** Nœud exigence : pastille couleur de niveau + id mono sur surface sombre. */
function ReqNode({ data }: NodeProps) {
  const d = data as ReqNodeData;
  return (
    <div
      className="flex cursor-pointer items-center gap-1.5 rounded-lg border px-2 py-1 transition-shadow duration-200"
      style={{
        background: "var(--surface-2)",
        borderColor: d.state === "none" ? "rgba(148,163,214,0.25)" : "transparent",
        boxShadow: STATE_RING[d.state] ?? "none",
      }}
    >
      <Handle type="target" position={Position.Top} style={{ visibility: "hidden" }} />
      <span className="h-2 w-2 shrink-0 rounded-full"
            style={{ background: NIVEAU_COLORS[d.niveau] }} />
      <span className="font-mono text-[10px] tracking-tight"
            style={{ color: "var(--foreground)" }}>
        {d.rid}
      </span>
      <Handle type="source" position={Position.Bottom} style={{ visibility: "hidden" }} />
    </div>
  );
}

/** Étiquette de niveau, posée dans le repère du flux (suit zoom/pan). */
function LevelNode({ data }: NodeProps) {
  const d = data as { label: string; color: string };
  return (
    <div className="pointer-events-none flex items-center gap-2">
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: d.color }} />
      <span className="text-[10px] font-medium uppercase tracking-[0.18em]"
            style={{ color: "var(--foreground-faint)" }}>
        {d.label}
      </span>
    </div>
  );
}

const nodeTypes = { req: ReqNode, level: LevelNode };

/** memo : le graphe ne se re-rend pas pendant le streaming du verdict
 * (les props gardent la même identité tant que la matrice ne change pas). */
export const ReqGraph = memo(function ReqGraph({
  corpus,
  selected,
  impacted,
  flaggedSev,
  mentioned,
  onSelect,
}: {
  corpus: Req[];
  selected: string | null;
  impacted: Set<string>;
  /** Signalées par l'audit, avec leur pire sévérité ("bad" | "warn"). */
  flaggedSev: Map<string, "bad" | "warn">;
  /** Citées par la synthèse LLM du verdict. */
  mentioned: Set<string>;
  onSelect: (id: string) => void;
}) {
  const { nodes, edges } = useMemo(() => {
    const byLevel = new Map<number, Req[]>();
    for (const r of corpus) {
      const lvl = Math.max(0, Math.min(5, r.niveau ?? 0));
      byLevel.set(lvl, [...(byLevel.get(lvl) ?? []), r]);
    }
    const nodes: Node[] = [];
    const levels = [...byLevel.keys()].sort();
    for (const lvl of levels) {
      // Étiquette de niveau à gauche de la rangée.
      nodes.push({
        id: `lvl-${lvl}`, type: "level",
        position: { x: -215, y: lvl * 140 + 4 },
        data: { label: LEVEL_LABELS[lvl], color: NIVEAU_COLORS[lvl] },
        draggable: false, selectable: false, focusable: false,
      });
      const reqs = byLevel.get(lvl)!;
      reqs.sort((a, b) => a.id.localeCompare(b.id));
      reqs.forEach((r, i) => {
        nodes.push({
          id: r.id, type: "req",
          position: { x: i * 150, y: lvl * 140 },
          data: {
            rid: r.id, niveau: lvl,
            state: (selected === r.id ? "selected"
              : mentioned.has(r.id) ? "mentioned"
              : flaggedSev.get(r.id) === "bad" ? "flagged-bad"
              : flaggedSev.get(r.id) === "warn" ? "flagged-warn"
              : impacted.has(r.id) ? "impacted" : "none") as NodeState,
          },
          draggable: false, connectable: false,
        });
      });
    }
    const edges: Edge[] = [];
    for (const r of corpus) {
      if (r.parent_id)
        edges.push({ id: `p-${r.parent_id}-${r.id}`, source: r.parent_id, target: r.id,
                     style: { stroke: "rgba(148,163,214,0.35)", strokeWidth: 1.2 } });
      for (const lk of r.links ?? [])
        edges.push({ id: `l-${lk.target}-${r.id}-${lk.type}`, source: lk.target, target: r.id,
                     style: { stroke: "rgba(93,191,213,0.35)", strokeDasharray: "4 4" } });
    }
    return { nodes, edges };
  }, [corpus, selected, impacted, flaggedSev, mentioned]);

  const onNodeClick: NodeMouseHandler = (_e, node) => {
    if (node.type === "req") onSelect(node.id);
  };

  return (
    <div className="flex flex-col gap-1.5">
      {/* La vue centrale de LynX : le graphe prend la hauteur disponible. */}
      <div className="req-graph h-[calc(100vh-22rem)] min-h-[560px] overflow-hidden rounded-xl border border-edge bg-surface">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={onNodeClick}
        fitView
        fitViewOptions={{ padding: 0.12 }}
        minZoom={0.3}
        maxZoom={1.6}
        proOptions={{ hideAttribution: true }}
        nodesDraggable={false}
        nodesConnectable={false}
        colorMode="dark"
        style={{ background: "transparent" }}
      >
        <Background color="rgba(148,163,214,0.10)" gap={26} size={1} />
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>
      </div>
      {/* Légende des halos, sous le cadre (le niveau est étiqueté dans le graphe). */}
      <p className="flex flex-wrap gap-x-4 gap-y-1 px-1 text-[10px] text-fg-faint">
        <span><span style={{ color: "#7fd4e6" }}>●</span> sélection</span>
        <span><span style={{ color: "#eef1f8" }}>●</span> citée par la synthèse</span>
        <span><span style={{ color: "#fbbf24" }}>●</span> impactée / attention</span>
        <span><span style={{ color: "#f87171" }}>●</span> bloquante</span>
      </p>
    </div>
  );
});
