import { displaySourceName } from "@/lib/api";
import type { ChatMessage } from "@/lib/types";

/** Construit le livrable Markdown sans dépendre de React ni du navigateur. */
export function conversationMarkdown(messages: ChatMessage[], baseline: boolean): string {
  const title = baseline ? "Chat baseline d'exigences (LynX)" : "Outil RAG";
  const lines = [
    `# ${title} — conversation`,
    `_Exportée le ${new Date().toLocaleString("fr-FR")} · AI for SSH (100 % local)_`,
    "",
  ];

  for (const message of messages) {
    if (message.role === "user") {
      lines.push(`## ${message.content}`, "");
      continue;
    }
    lines.push(message.content.trim(), "");
    const requirementIds = [...new Set(
      (message.chunks ?? []).map((chunk) => chunk.meta.req_id).filter(Boolean),
    )];
    if (requirementIds.length) {
      lines.push(`**Exigences citées** : ${requirementIds.join(", ")}`, "");
    }
    if (message.citations?.length) {
      lines.push("**Sources**", "");
      for (const citation of message.citations) {
        const section = citation.heading ?? citation.breadcrumb;
        lines.push(
          `- [${citation.idx}] ${displaySourceName(citation.source)}`
          + (section ? ` – ${section}` : "")
          + (citation.page ? ` – p. ${citation.page}` : ""),
        );
      }
      lines.push("");
    }
    lines.push("---", "");
  }
  return lines.join("\n");
}


export function downloadConversation(messages: ChatMessage[], baseline: boolean): void {
  const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-");
  const url = URL.createObjectURL(new Blob(
    [conversationMarkdown(messages, baseline)],
    { type: "text/markdown;charset=utf-8" },
  ));
  const link = document.createElement("a");
  link.href = url;
  link.download = `conversation-${baseline ? "baseline" : "rag"}-${stamp}.md`;
  link.click();
  URL.revokeObjectURL(url);
}
