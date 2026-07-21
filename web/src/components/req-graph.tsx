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

import { couleurNiveau, libelleNiveau, maxNiveau, type NiveauCat } from "@/components/req-levels";

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

// Couleurs d'état des nœuds — MIROIR des tokens de globals.css
// (--accent-bright, --foreground-bright, --warn, --bad) : three.js exige des
// hex bruts. Toute retouche du thème se répercute ici À LA MAIN.
export const STATE_HEX: Record<string, string> = {
  selected: "#7fd4e6",
  mentioned: "#eef1f8",
  impacted: "#fbbf24",
  "flagged-warn": "#fbbf24",
  "flagged-bad": "#f87171",
};

type NodeState = "none" | "selected" | "mentioned" | "flagged-bad" | "flagged-warn" | "impacted";
type ReqNodeData = { rid: string; niveau: number; couleur: string; state: NodeState };

const ring = (color: string, blur: number, glow: number) =>
  `0 0 0 2px ${color}, 0 0 ${blur}px color-mix(in srgb, ${color} ${glow}%, transparent)`;
const STATE_RING: Record<string, string> = {
  selected:
    "0 0 0 2px var(--accent-bright), 0 0 18px color-mix(in srgb, var(--accent) 45%, transparent)",
  mentioned: ring("var(--foreground-bright)", 16, 35),
  impacted: ring("var(--warn)", 14, 35),
  "flagged-warn": ring("var(--warn)", 14, 35),
  "flagged-bad": ring("var(--bad)", 16, 45),
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
            style={{ background: d.couleur }} />
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
  niveaux,
}: {
  corpus: Req[];
  selected: string | null;
  impacted: Set<string>;
  /** Signalées par l'audit, avec leur pire sévérité ("bad" | "warn"). */
  flaggedSev: Map<string, "bad" | "warn">;
  /** Citées par la synthèse LLM du verdict. */
  mentioned: Set<string>;
  onSelect: (id: string) => void;
  /** Catalogue de libellés de niveaux porté par le corpus (repli L0–L5 si absent). */
  niveaux?: NiveauCat[];
}) {
  const { nodes, edges } = useMemo(() => {
    const nMax = maxNiveau(corpus);
    const byLevel = new Map<number, Req[]>();
    for (const r of corpus) {
      const lvl = Math.max(0, r.niveau ?? 0);
      byLevel.set(lvl, [...(byLevel.get(lvl) ?? []), r]);
    }
    const nodes: Node[] = [];
    const levels = [...byLevel.keys()].sort((a, b) => a - b);
    for (const lvl of levels) {
      // Étiquette de niveau à gauche de la rangée.
      nodes.push({
        id: `lvl-${lvl}`, type: "level",
        position: { x: -215, y: lvl * 140 + 4 },
        data: { label: libelleNiveau(lvl, niveaux), color: couleurNiveau(lvl, nMax) },
        draggable: false, selectable: false, focusable: false,
      });
      const reqs = byLevel.get(lvl)!;
      reqs.sort((a, b) => a.id.localeCompare(b.id));
      reqs.forEach((r, i) => {
        nodes.push({
          id: r.id, type: "req",
          position: { x: i * 150, y: lvl * 140 },
          data: {
            rid: r.id, niveau: lvl, couleur: couleurNiveau(lvl, nMax),
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
  }, [corpus, selected, impacted, flaggedSev, mentioned, niveaux]);

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
        <span><span style={{ color: "var(--accent-bright)" }}>●</span> sélection</span>
        <span><span style={{ color: "var(--foreground-bright)" }}>●</span> citée par la synthèse</span>
        <span><span style={{ color: "var(--warn)" }}>●</span> impactée / attention</span>
        <span><span style={{ color: "var(--bad)" }}>●</span> bloquante</span>
      </p>
    </div>
  );
});
