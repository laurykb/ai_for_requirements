"use client";

import type { Req } from "@/components/req-explorer";
import { Spinner } from "@/components/ui";

export type Traceability = { upstream: Req[]; downstream: Req[]; path: Req[] };


export function TraceabilityPanel({ selected, traceability, loading = false, onSelect }: {
  selected: Req;
  traceability: Traceability | null;
  loading?: boolean;
  onSelect: (id: string) => void;
}) {
  if (loading || !traceability) return (
    <section className="flex items-center gap-2 rounded-lg border border-edge bg-surface-2 p-3 text-[11px] text-fg-faint">
      <Spinner /> Chargement de la traçabilité…
    </section>
  );
  return (
    <section className="rounded-lg border border-edge bg-surface-2 p-3">
      <div className="flex items-center justify-between">
        <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-fg-faint">Traçabilité</p>
        <span className="font-mono text-[10px] text-fg-faint">
          {traceability.upstream.length} amont · {traceability.downstream.length} aval
        </span>
      </div>
      {traceability.path.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1 text-[10px] text-fg-faint">
          {traceability.path.map((requirement) => (
            <span key={requirement.id} className="contents">
              <RequirementLink requirement={requirement} onSelect={onSelect} bordered />
              <span>›</span>
            </span>
          ))}
          <span className="font-mono text-accent-bright">{selected.id}</span>
        </div>
      )}
      <div className="mt-2 grid gap-2 sm:grid-cols-2 xl:grid-cols-1">
        <RequirementGroup title="Amont" empty="racine" requirements={traceability.upstream}
                          prefix="← " onSelect={onSelect} />
        <RequirementGroup title="Aval" empty="aucune exigence aval" requirements={traceability.downstream}
                          suffix=" →" onSelect={onSelect} scrollable />
      </div>
    </section>
  );
}


function RequirementGroup({ title, empty, requirements, prefix = "", suffix = "", onSelect, scrollable = false }: {
  title: string;
  empty: string;
  requirements: Req[];
  prefix?: string;
  suffix?: string;
  onSelect: (id: string) => void;
  scrollable?: boolean;
}) {
  return (
    <div>
      <p className="mb-1 text-[10px] text-fg-faint">{title}</p>
      <div className={`flex flex-wrap gap-1 ${scrollable ? "max-h-24 overflow-y-auto" : ""}`}>
        {requirements.map((requirement) => (
          <RequirementLink key={requirement.id} requirement={requirement} onSelect={onSelect}
                           prefix={prefix} suffix={suffix} />
        ))}
        {!requirements.length && <span className="text-[10px] text-fg-faint">{empty}</span>}
      </div>
    </div>
  );
}


function RequirementLink({ requirement, onSelect, prefix = "", suffix = "", bordered = false }: {
  requirement: Req;
  onSelect: (id: string) => void;
  prefix?: string;
  suffix?: string;
  bordered?: boolean;
}) {
  return (
    <button onClick={() => onSelect(requirement.id)} title={requirement.texte}
            className={`cursor-pointer rounded px-1.5 py-0.5 font-mono text-[10px] hover:text-foreground ${
              bordered ? "border border-edge hover:border-accent" : "bg-muted text-fg-muted"
            }`}>
      {prefix}{requirement.id}{suffix}
    </button>
  );
}
