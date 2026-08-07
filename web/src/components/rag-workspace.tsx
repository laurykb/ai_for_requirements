"use client";

import { Chat } from "@/components/chat";
import { PanelTitle } from "@/components/ui";

export function RagWorkspace() {
  return <div className="rise-in py-4">
    <div className="mx-auto max-w-4xl">
      <PanelTitle kicker="RAG documentaire" title="Outil RAG"
        hint="Interroger vos documents ingérés : réponses sourcées et raisonnement visible." />
    </div>
    <Chat />
  </div>;
}
