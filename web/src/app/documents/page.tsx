import { Documents } from "@/components/documents";
import { PanelTitle } from "@/components/ui";

/** Documents : dépôt + file d'ingestion + exploration des passages indexés. */

export default function DocumentsPage() {
  return (
    <div className="rise-in py-4">
      <div className="mx-auto max-w-3xl">
        <PanelTitle
          kicker="RAG documentaire"
          title="Documents"
          hint="Déposer des documents à indexer, suivre l'ingestion, explorer ce que la base contient."
        />
      </div>
      <Documents />
    </div>
  );
}
