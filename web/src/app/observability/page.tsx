import { Observability } from "@/components/observability";
import { PanelTitle } from "@/components/ui";

/** Observabilité (mode expert) : perfs d'inférence + traces des requêtes. */

export default function ObservabilityPage() {
  return (
    <div className="rise-in py-4">
      <div className="mx-auto max-w-3xl">
        <PanelTitle
          kicker="Vue d'opérateur"
          title="Observabilité"
          hint="Performance d'inférence mesurée côté serving et traces chronométrées de chaque requête."
        />
      </div>
      <Observability />
    </div>
  );
}
