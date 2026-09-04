"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { apiFetch, getJSON } from "@/lib/api";

type Attachment = { name: string; pct: number; step: string };
type IngestJob = {
  name: string;
  status: string;
  pct: number;
  step: string;
  message: string | null;
};

/** Dépôt et suivi d'un lot documentaire, indépendant de la conversation. */
export function useDocumentAttachment(disabled: boolean, onIndexed: () => void) {
  const [attachment, setAttachment] = useState<Attachment | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
  }, []);
  useEffect(() => stopPolling, [stopPolling]);

  const attachFiles = useCallback(async (files: FileList | null) => {
    if (disabled || !files?.length || attachment) return;
    const names = Array.from(files, (file) => file.name);
    const label = names.length > 1 ? `${names[0]} (+${names.length - 1})` : names[0];
    setError(null);
    setAttachment({ name: label, pct: 0, step: "Dépôt du document…" });

    const defaults = await getJSON<{ params: Record<string, unknown> }>("/api/ingest/defaults")
      .catch(() => null);
    const params = defaults?.params ?? {
      nkw: 5, nq: 3, mode: "technical", raptor: true, enh_model: "",
    };
    const body = new FormData();
    Array.from(files).forEach((file) => body.append("files", file));
    for (const key of ["nkw", "nq", "mode", "raptor", "enh_model"]) {
      body.append(key, String(params[key] ?? ""));
    }
    const response = await apiFetch(`/api/documents`, { method: "POST", body })
      .catch(() => null);
    if (fileRef.current) fileRef.current.value = "";
    if (!response?.ok) {
      setAttachment(null);
      setError(`Dépôt impossible pour « ${label} » — type non accepté ou API indisponible.`);
      return;
    }

    let misses = 0;
    pollRef.current = setInterval(async () => {
      const status = await getJSON<{ jobs: IngestJob[] }>("/api/ingest/status")
        .catch(() => null);
      const jobs = names
        .map((name) => status?.jobs.filter((job) => job.name === name).at(-1))
        .filter((job): job is IngestJob => Boolean(job));
      if (!status || jobs.length < names.length) {
        if (++misses >= 5) {
          stopPolling();
          setAttachment(null);
          setError("Suivi d'indexation perdu (API redémarrée ?) — vérifiez l'onglet Documents.");
        }
        return;
      }
      misses = 0;
      const pending = jobs.filter((job) => ["queued", "running"].includes(job.status));
      if (pending.length) {
        const current = pending.find((job) => job.status === "running") ?? pending[0];
        const completed = jobs.length - pending.length;
        setAttachment({
          name: label,
          pct: Math.round((100 * completed + current.pct) / jobs.length),
          step: current.step,
        });
        return;
      }

      stopPolling();
      setAttachment(null);
      const successful = jobs.filter((job) => job.status === "success");
      const failed = jobs.filter((job) => job.status === "error");
      if (successful.length) onIndexed();
      if (failed.length) {
        setError(failed.map((job) =>
          `Indexation de « ${job.name} » échouée : ${job.message ?? "erreur."}`,
        ).join(" — "));
      }
    }, 1200);
  }, [attachment, disabled, onIndexed, stopPolling]);

  return {
    attachment,
    attaching: attachment !== null,
    error,
    dismissError: () => setError(null),
    fileRef,
    attachFiles,
  };
}
