import { Chat } from "@/components/chat";
import { PanelTitle } from "@/components/ui";

/** Outil RAG : chat sur les documents ingérés (réponses sourcées, boîte de verre). */

export default function RagPage() {
  return (
    <div className="rise-in py-4">
      <div className="mx-auto max-w-3xl">
        <PanelTitle
          kicker="RAG documentaire"
          title="Outil RAG"
          hint="Interroger vos documents ingérés : réponses sourcées et raisonnement visible."
        />
      </div>
      <Chat />
    </div>
  );
}
