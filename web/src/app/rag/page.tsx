import { Banner, PanelTitle } from "@/components/ui";

/** Outil RAG — vue en construction (étape 3 de la migration). */

export default function RagPage() {
  return (
    <div className="rise-in mx-auto max-w-2xl py-12">
      <PanelTitle
        kicker="RAG documentaire"
        title="Outil RAG"
        hint="Interroger vos documents ingérés : réponses sourcées et raisonnement visible."
      />
      <Banner tone="neutral">
        Cette vue arrive dans une prochaine étape de la migration. L&apos;outil reste
        disponible dans l&apos;interface actuelle : <code>python serve.py</code> depuis la
        racine du projet.
      </Banner>
    </div>
  );
}
