"use client";

/** Paramètres : comportement de recherche (préférences locales), modèles
 * (changement à chaud + VRAM), réglages .env (retrieval, enrichissement,
 * Self-RAG), system prompt, zone dangereuse (reset corpus). */

import { useCallback, useEffect, useState } from "react";

import { API_BASE, getJSON } from "@/lib/api";
import { loadPrefs, savePrefs, type Prefs } from "@/lib/prefs";
import { Banner, Hint, Spinner } from "@/components/ui";

type Settings = {
  values: Record<string, string | number | boolean>;
  default_system_prompt: string;
};
type Models = { models: string[]; routing: Record<string, string>; vectors: number | null };

function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="flex items-center justify-between gap-3 text-xs text-fg-muted">
      <span className="flex items-center gap-1.5">
        {label}
        {hint && <Hint text={hint} />}
      </span>
      {children}
    </label>
  );
}

const inputCls =
  "rounded-lg border border-edge bg-surface px-2 py-1.5 text-xs text-foreground focus:border-accent focus:outline-none";

const MODEL_ROLES = [
  ["EMBED_MODEL", "Embedding", "Vectorisation multilingue. Changer ce modèle impose de réindexer le corpus."],
  ["REWRITER_MODEL", "Réécriture", "Reformulation des requêtes et Self-RAG."],
  ["AGENT_MODEL", "Agent maître", "Orchestration ReAct et appels outils."],
  ["PLANNER_MODEL", "Planification / sous-agents", "Plans JSON et découpage multi-hop."],
  ["EXTRACTION_MODEL", "Extraction documentaire", "Passe MAP : extraction factuelle document par document."],
  ["SYNTHESIS_MODEL", "Synthèse multi-documents", "Passe REDUCE : croisement et fusion sur gros corpus."],
  ["GEN_MODEL", "Rédaction finale", "Réponse RAG finale en français."],
  ["JUDGE_MODEL", "Juge / attribution", "Évaluation et attribution des affirmations."],
  ["ENHANCEMENT_MODEL", "Enrichissement ingestion", "Mots-clés, questions et résumés RAPTOR."],
] as const;

/** Toggle trois états pour les options par requête : défaut (.env) / oui / non. */
function TriToggle({ value, onChange }: { value: boolean | null;
                                          onChange: (v: boolean | null) => void }) {
  const opts: [string, boolean | null][] = [["défaut", null], ["oui", true], ["non", false]];
  return (
    <span className="flex overflow-hidden rounded-lg border border-edge">
      {opts.map(([lbl, v]) => (
        <button
          key={lbl}
          onClick={() => onChange(v)}
          className={`cursor-pointer px-2.5 py-1 text-xs transition-colors ${
            value === v ? "bg-accent/20 text-accent-bright" : "text-fg-faint hover:text-fg-muted"
          }`}
        >
          {lbl}
        </button>
      ))}
    </span>
  );
}

export function SettingsView() {
  const [prefs, setPrefs] = useState<Prefs | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [models, setModels] = useState<Models | null>(null);
  const [env, setEnv] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);

  useEffect(() => {
    const t = setTimeout(async () => {
      setPrefs(loadPrefs());
      try {
        const [s, m] = await Promise.all([
          getJSON<Settings>("/api/settings"),
          getJSON<Models>("/api/models"),
        ]);
        setSettings(s);
        setModels(m);
        setEnv(Object.fromEntries(Object.entries(s.values).map(([k, v]) => [k, String(v)])));
      } catch {
        setError("API hors ligne — lancer python serve.py --web");
      }
    }, 0);
    return () => clearTimeout(t);
  }, []);

  const setPref = useCallback((p: Partial<Prefs>) => setPrefs(savePrefs(p)), []);

  const act = async (path: string, body: unknown, okMsg: string) => {
    setStatus(null);
    try {
      const res = await fetch(`${API_BASE}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setStatus(okMsg);
    } catch (e) {
      setStatus(`Échec : ${String(e)}`);
    }
  };

  if (error) return <Banner tone="bad">{error}</Banner>;
  if (!prefs || !settings || !models)
    return (
      <p className="flex items-center gap-2 text-xs text-fg-muted">
        <Spinner /> Chargement…
      </p>
    );

  const genModel = env.GEN_MODEL ?? "";
  const optionsFor = (selected: string) =>
    Array.from(new Set([selected, ...models.models].filter(Boolean)));

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6">
      {status && <Banner tone="neutral">{status}</Banner>}

      {/* Préférences locales — effet immédiat sur les prochaines questions. */}
      <section className="space-y-3">
        <h3 className="text-sm font-semibold text-foreground">Comportement de recherche</h3>
        <Row label="Mémoire de conversation"
             hint="Tient compte des échanges précédents de la conversation.">
          <input type="checkbox" checked={prefs.useMemory}
                 onChange={(e) => setPref({ useMemory: e.target.checked })}
                 className="accent-(--accent)" />
        </Row>
        <Row label="Contexte parent (parent-child)"
             hint="Renvoie la section parente entière du passage trouvé.">
          <TriToggle value={prefs.parentChild} onChange={(v) => setPref({ parentChild: v })} />
        </Row>
        <Row label="Auto-correction (Self-RAG)"
             hint="Si la fidélité aux sources est jugée faible, reformule et régénère avant d'afficher (qualité +, latence +).">
          <TriToggle value={prefs.selfRag} onChange={(v) => setPref({ selfRag: v })} />
        </Row>
        <p className="text-[11px] text-fg-faint">
          Effet immédiat sur vos prochaines questions (préférences de ce navigateur).
        </p>
      </section>

      {/* Modèles : à chaud. */}
      <section className="space-y-3">
        <h3 className="text-sm font-semibold text-foreground">Arsenal Ollama par tâche</h3>
        <p className="text-[11px] text-fg-faint">Chaque étape peut utiliser un modèle différent. Enregistrez ensuite dans .env et redémarrez l API.</p>
        <Row label="Hôte Ollama"><input value={env.OLLAMA_HOST ?? ""} onChange={(e) => setEnv({ ...env, OLLAMA_HOST: e.target.value })} className={inputCls + " w-56 font-mono"} /></Row>
        <Row label="Fenêtre de contexte"><input type="number" min={2048} step={1024} value={env.LLM_NUM_CTX ?? ""} onChange={(e) => setEnv({ ...env, LLM_NUM_CTX: e.target.value })} className={inputCls + " w-28"} /></Row>
        <div className="space-y-2 rounded-xl border border-edge bg-surface/40 p-3">
          {MODEL_ROLES.filter(([key]) => key !== "GEN_MODEL").map(([key, label, hint]) => (
            <Row key={key} label={label} hint={hint}>
              <select value={env[key] ?? ""} onChange={(e) => setEnv({ ...env, [key]: e.target.value })} className={inputCls + " max-w-64"}>
                {!env[key] && <option value="">Modèle hérité (défaut)</option>}
                {optionsFor(env[key] ?? "").map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </Row>
          ))}
        </div>
        <Row label="Modèle de génération"
             hint="Sert aux prochaines réponses. « Charger » l'épingle en VRAM et l'active à chaud.">
          <select value={genModel} onChange={(e) => setEnv({ ...env, GEN_MODEL: e.target.value })}
                  className={inputCls}>
            {(models.models.length ? models.models : [genModel]).map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        </Row>
        <div className="flex gap-2">
          <button
            onClick={() => act("/api/models/generate", { model: genModel, action: "load" },
                               `Modèle « ${genModel} » chargé et activé.`)}
            className="cursor-pointer rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-background hover:bg-accent-bright"
          >
            Charger le LLM (VRAM)
          </button>
          <button
            onClick={() => act("/api/models/generate", { model: genModel, action: "unload" },
                               "Modèle déchargé de la VRAM.")}
            className="cursor-pointer rounded-lg border border-edge px-3 py-1.5 text-xs text-fg-muted hover:text-foreground"
          >
            Décharger (VRAM)
          </button>
        </div>
        <details className="chat-details">
          <summary>Routage par rôle &amp; magasin vectoriel</summary>
          <table className="mt-2 w-full text-left text-xs text-fg-muted">
            <tbody>
              {Object.entries(models.routing).map(([role, model]) => (
                <tr key={role} className="border-t border-edge">
                  <td className="py-1 pr-3">{role}</td>
                  <td className="font-mono">{model}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {models.vectors != null && (
            <p className="mt-2 text-[11px] text-fg-faint">
              Magasin vectoriel : ChromaDB — {models.vectors.toLocaleString("fr-FR")} vecteurs indexés
            </p>
          )}
        </details>
      </section>

      {/* Réglages .env. */}
      <section className="space-y-3">
        <h3 className="text-sm font-semibold text-foreground">
          Réglages (.env){" "}
          <Hint text="Enregistrés dans le fichier .env — appliqués au prochain démarrage de l'API." />
        </h3>
        <Row label="Nombre de chunks récupérés">
          <input type="number" min={1} max={50} value={env.NUM_CHUNKS ?? ""}
                 onChange={(e) => setEnv({ ...env, NUM_CHUNKS: e.target.value })}
                 className={`${inputCls} w-20`} />
        </Row>
        <Row label="Poids recherche sémantique">
          <input type="number" step={0.1} min={0} max={1} value={env.WEIGHT_SEMANTIC ?? ""}
                 onChange={(e) => setEnv({ ...env, WEIGHT_SEMANTIC: e.target.value })}
                 className={`${inputCls} w-20`} />
        </Row>
        <Row label="Poids recherche BM25 (mots-clés)">
          <input type="number" step={0.1} min={0} max={1} value={env.WEIGHT_BM25 ?? ""}
                 onChange={(e) => setEnv({ ...env, WEIGHT_BM25: e.target.value })}
                 className={`${inputCls} w-20`} />
        </Row>
        <Row label="Seuil hors-scope (cross-encoder)"
             hint="Sous ce score de pertinence maximum, la question est jugée hors du corpus.">
          <input type="number" step={0.005} min={0.5} max={0.6}
                 value={env.CE_RELEVANCE_THRESHOLD ?? ""}
                 onChange={(e) => setEnv({ ...env, CE_RELEVANCE_THRESHOLD: e.target.value })}
                 className={`${inputCls} w-24`} />
        </Row>
        <Row label="Mots-clés / chunk (prochaines ingestions)">
          <input type="number" min={0} max={10} value={env.AUTO_KEYWORDS ?? ""}
                 onChange={(e) => setEnv({ ...env, AUTO_KEYWORDS: e.target.value })}
                 className={`${inputCls} w-20`} />
        </Row>
        <Row label="Questions / chunk (prochaines ingestions)">
          <input type="number" min={0} max={10} value={env.AUTO_QUESTIONS ?? ""}
                 onChange={(e) => setEnv({ ...env, AUTO_QUESTIONS: e.target.value })}
                 className={`${inputCls} w-20`} />
        </Row>
        <Row label="Self-RAG par défaut">
          <select value={env.SELF_RAG_ENABLED ?? "false"}
                  onChange={(e) => setEnv({ ...env, SELF_RAG_ENABLED: e.target.value })}
                  className={inputCls}>
            <option value="true">activé</option>
            <option value="false">désactivé</option>
          </select>
        </Row>
        <Row label="Seuil Self-RAG (sous lequel on retente)">
          <input type="number" step={0.05} min={0.1} max={0.9}
                 value={env.SELF_RAG_THRESHOLD ?? ""}
                 onChange={(e) => setEnv({ ...env, SELF_RAG_THRESHOLD: e.target.value })}
                 className={`${inputCls} w-20`} />
        </Row>
        <button
          onClick={() => act("/api/settings", { updates: env },
                             ".env mis à jour — redémarrez l'API pour tout appliquer.")}
          className="cursor-pointer rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-background hover:bg-accent-bright"
        >
          Enregistrer dans .env
        </button>
      </section>

      {/* System prompt (préférence locale, envoyé avec chaque question). */}
      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-foreground">
          System prompt{" "}
          <Hint text="Instructions données au modèle pour répondre. Vide = prompt par défaut de l'application." />
        </h3>
        <textarea
          value={prefs.systemPrompt ?? settings.default_system_prompt}
          onChange={(e) => setPref({ systemPrompt: e.target.value })}
          rows={8}
          className="w-full rounded-lg border border-edge bg-surface px-3 py-2 font-mono text-xs leading-relaxed text-foreground focus:border-accent focus:outline-none"
        />
        <button
          onClick={() => setPref({ systemPrompt: null })}
          className="cursor-pointer rounded-lg border border-edge px-3 py-1.5 text-xs text-fg-muted hover:text-foreground"
        >
          Réinitialiser le prompt
        </button>
      </section>

      {/* Zone dangereuse. */}
      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-bad">Zone dangereuse — corpus</h3>
        <p className="text-xs text-fg-muted">
          Réinitialise l&apos;index documentaire (Chroma + chunks/BM25 Mongo). N&apos;efface pas
          les traces. Irréversible — il faudra ré-ingérer les documents.
        </p>
        {confirmReset ? (
          <span className="flex items-center gap-2 text-xs">
            <span className="text-fg-muted">Tous les documents indexés seront supprimés.</span>
            <button
              onClick={async () => {
                setConfirmReset(false);
                await act("/api/corpus/reset", {}, "Corpus réinitialisé.");
              }}
              className="cursor-pointer rounded-md bg-bad/20 px-2 py-1 text-bad hover:bg-bad/30"
            >
              Oui, tout effacer
            </button>
            <button onClick={() => setConfirmReset(false)}
                    className="cursor-pointer rounded-md border border-edge px-2 py-1 text-fg-muted">
              Annuler
            </button>
          </span>
        ) : (
          <button
            onClick={() => setConfirmReset(true)}
            className="cursor-pointer rounded-lg border border-bad/40 px-3 py-1.5 text-xs text-bad hover:bg-bad/15"
          >
            Réinitialiser le corpus
          </button>
        )}
      </section>
    </div>
  );
}
