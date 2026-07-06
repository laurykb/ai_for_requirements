import { Banner, PanelTitle } from "@/components/ui";

/** AI for Requirements (LynX) — vue en construction (étape 5 de la migration). */

export default function RequirementsPage() {
  return (
    <div className="rise-in mx-auto max-w-2xl py-12">
      <PanelTitle
        kicker="Vérification d'exigences"
        title="AI for Requirements"
        hint="Relecture de la matrice d'exigences par des agents : verdicts, corrections, boîte de verre."
      />
      <Banner tone="neutral">
        Cette vue arrive dans une prochaine étape de la migration. L&apos;outil reste
        disponible dans l&apos;interface actuelle : <code>python serve.py</code> depuis la
        racine du projet.
      </Banner>
    </div>
  );
}
