import { Requirements } from "@/components/requirements";
import { PanelTitle } from "@/components/ui";

/** AI for Requirements (LynX) : seconde lecture de la matrice d'exigences. */

export default function RequirementsPage() {
  return (
    <div className="rise-in py-4">
      <PanelTitle
        kicker="Vérification d'exigences"
        title="AI for Requirements"
        hint="Des agents relisent votre matrice : impact d'une action, audit complet, corrections suggérées — raisonnement visible."
      />
      <Requirements />
    </div>
  );
}
