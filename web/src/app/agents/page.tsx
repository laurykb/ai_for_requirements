import { AgentsView } from "@/components/agents";
import { PanelTitle } from "@/components/ui";

export default function AgentsPage() {
  return (
    <div className="rise-in py-4">
      <div className="mx-auto max-w-4xl">
        <PanelTitle kicker="Mode Expert" title="Prompts métier"
          hint="Inspecter, versionner et adapter les instructions utilisées par les différents traitements." />
        <AgentsView />
      </div>
    </div>
  );
}
