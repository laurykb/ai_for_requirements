"use client";

/** LynX — onglet Informations : la salle des machines.
 * 1. Prompts des agents (skills/*.md) : lisibles et ÉDITABLES depuis l'UI,
 *    rechargés à chaud (effet dès l'analyse suivante).
 * 2. Orchestration : ordre d'exécution et activation des agents d'analyse
 *    (déterministes = séquentiels ; sémantiques = lancés en parallèle). */

import { useEffect, useState } from "react";

import { apiFetch, getJSON } from "@/lib/api";
import { useExpert } from "@/components/expert-toggle";
import { Banner, Dot, Hint, Spinner } from "@/components/ui";

type Skill = { name: string; content: string };
type OrchEntry = { name: string; label: string; enabled: boolean; model?: string | null };
type Orchestration = { deterministic: OrchEntry[]; semantic: OrchEntry[] };

const btn =
  "cursor-pointer rounded-md border border-edge px-2 py-1 text-[11px] text-fg-muted " +
  "transition-colors hover:border-accent/60 hover:text-foreground disabled:opacity-40";

/** Éditeur d’un prompt d’agent (sauvegarde explicite, statut inline). */
function SkillEditor({ s }: { s: Skill }) {
  const [content, setContent] = useState(s.content);
  const [status, setStatus] = useState<string | null>(null);
  const dirty = content !== s.content && status !== "enregistré";

  const save = async (): Promise<boolean> => {
    const res = await apiFetch(`/api/lynx/skills/${encodeURIComponent(s.name)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }).catch(() => null);
    setStatus(res?.ok ? "enregistré" : "échec de l'enregistrement");
    return Boolean(res?.ok);
  };

  return (
    <details className="chat-details">
      <summary className="font-mono text-xs">{s.name}.md {dirty && <span className="text-warn">●</span>}</summary>
      <textarea
        value={content}
        onChange={(e) => { setContent(e.target.value); setStatus(null); }}
        rows={14}
        spellCheck={false}
        className="mt-2 w-full rounded-lg border border-edge bg-surface px-3 py-2 font-mono text-xs leading-relaxed text-foreground focus:border-accent focus:outline-none"
      />
      <div className="mt-2 flex items-center gap-2">
        <button onClick={save} className={btn}>
          Enregistrer
        </button>
        <button onClick={() => { setContent(s.content); setStatus(null); }} className={btn}>
          Annuler les modifications
        </button>
        {status && (
          <span className={`text-[11px] ${status === "enregistré" ? "text-good" : "text-bad"}`}>
            {status} {status === "enregistré" && "— effet dès la prochaine analyse."}
          </span>
        )}
      </div>
    </details>
  );
}

/** Liste ordonnable (▲▼) avec activation, pour un groupe d'agents. */
function OrchList({ title, hint, entries, onChange }: {
  title: string; hint: string; entries: OrchEntry[];
  onChange: (next: OrchEntry[]) => void;
}) {
  const move = (i: number, d: -1 | 1) => {
    const j = i + d;
    if (j < 0 || j >= entries.length) return;
    const next = [...entries];
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
  };
  return (
    <div>
      <p className="flex items-center gap-1.5 text-xs font-medium text-foreground">
        {title} <Hint text={hint} />
      </p>
      <div className="mt-2 space-y-1">
        {entries.map((e, i) => (
          <div key={e.name}
               className="flex items-center gap-2 rounded-lg border border-edge bg-surface-2 px-2.5 py-1.5 text-xs">
            <span className="font-mono text-[10px] text-fg-faint">{i + 1}.</span>
            <input type="checkbox" checked={e.enabled}
                   onChange={(ev) => onChange(entries.map((x, j) =>
                     j === i ? { ...x, enabled: ev.target.checked } : x))}
                   title="Agent actif dans le pipeline"
                   className="accent-(--accent)" />
            <span className={e.enabled ? "text-foreground" : "text-fg-faint line-through"}>
              {e.label}
            </span>
            <span className="font-mono text-[10px] text-fg-faint">{e.name}</span>
            <span className="ml-auto flex gap-1">
              <button onClick={() => move(i, -1)} disabled={i === 0} aria-label="Monter"
                      className={btn}>▲</button>
              <button onClick={() => move(i, 1)} disabled={i === entries.length - 1}
                      aria-label="Descendre" className={btn}>▼</button>
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function LynxInfo() {
  const expert = useExpert();
  const [skills, setSkills] = useState<Skill[] | null>(null);
  const [orch, setOrch] = useState<Orchestration | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(async () => {
      try {
        const [s, o] = await Promise.all([
          getJSON<{ skills: Skill[] }>("/api/lynx/skills"),
          getJSON<Orchestration>("/api/lynx/orchestration"),
        ]);
        setSkills(s.skills);
        setOrch(o);
      } catch {
        setError("API hors ligne — lancer python serve.py --web");
      }
    }, 0);
    return () => clearTimeout(t);
  }, []);

  if (error) return <Banner tone="bad">{error}</Banner>;
  if (!skills || !orch)
    return <p className="flex items-center gap-2 text-xs text-fg-muted"><Spinner /> Chargement…</p>;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      {/* Vue d'ensemble (toujours visible) : quels agents, quel modèle. */}
      <section>
        <h3 className="text-sm font-semibold text-foreground">
          Agents &amp; modèles{" "}
          <Hint text="Les agents du pipeline d'analyse et le LLM que chacun utilise (routage LYNX_MODEL_<AGENT> dans le .env ; les déterministes n'appellent aucun LLM). Modifier l'orchestration et les prompts : mode Expert." />
        </h3>
        <div className="mt-3 grid gap-1.5 sm:grid-cols-2">
          {[...orch.deterministic.map((e) => ({ ...e, kind: "déterministe" })),
            ...orch.semantic.map((e) => ({ ...e, kind: "sémantique (IA)" }))].map((e) => (
            <p key={e.name} className="flex items-center gap-2 text-xs">
              <Dot tone={e.enabled ? "good" : "neutral"} />
              <span className={e.enabled ? "text-foreground" : "text-fg-faint line-through"}>
                {e.label}
              </span>
              <span className="ml-auto text-right font-mono text-[10px] text-fg-faint">
                {e.kind === "déterministe" ? "sans LLM" : (e.model ?? "—")}
              </span>
            </p>
          ))}
        </div>
      </section>

      <section>
        <h3 className="text-sm font-semibold text-foreground">
          Chat sur la baseline{" "}
          <Hint text="Onglet Chat : un assistant conversationnel dont le périmètre est verrouillé sur la matrice d'exigences de l'arbre. Il réutilise le moteur de l'Outil RAG (routage auto, agent multi-étapes, synthèse, réponses sourcées) mais n'accède à AUCUN document du monde RAG." />
        </h3>
        <p className="mt-1 text-xs leading-relaxed text-fg-muted">
          L&apos;onglet Chat interroge la baseline d&apos;exigences en langage naturel :
          chaque réponse cite les exigences (identifiants et passages) qui la
          fondent, avec le même raisonnement visible que l&apos;Outil RAG. La
          baseline est indexée à la demande (bouton Synchroniser) ; un bandeau
          d&apos;état signale toute dérive entre l&apos;arbre et l&apos;index. Pour les
          questions structurelles (dérivations, chaînes, orphelines), l&apos;agent
          dispose d&apos;un outil déterministe d&apos;interrogation de l&apos;arbre
          (<span className="font-mono">baseline_tree</span>) : réponses exactes,
          sans recherche sémantique. Les prompts de ce moteur se règlent dans
          l&apos;espace RAG (mode Expert → Prompts métier).
        </p>
      </section>

      {!expert && (
        <Banner tone="neutral">
          Activez le mode <b>Expert</b> (en haut à droite) pour réordonner ou
          désactiver les agents et éditer leurs prompts.
        </Banner>
      )}

      {expert && <section>
        <h3 className="text-sm font-semibold text-foreground">
          Orchestration des agents{" "}
          <Hint text="Déterministes : exécutés l'un après l'autre, dans cet ordre. Sémantiques (IA) : lancés en parallèle, l'ordre = ordre de lancement. Décocher = retirer du pipeline." />
        </h3>
        <div className="mt-3 grid gap-5 sm:grid-cols-2">
          <OrchList title="Agents déterministes (séquentiels)"
                    hint="Règles sans LLM — instantanées."
                    entries={orch.deterministic}
                    onChange={(d) => { setOrch({ ...orch, deterministic: d }); setSaved(null); }} />
          <OrchList title="Agents sémantiques (parallèles)"
                    hint="Agents IA — un appel LLM chacun."
                    entries={orch.semantic}
                    onChange={(s) => { setOrch({ ...orch, semantic: s }); setSaved(null); }} />
        </div>
        <div className="mt-3 flex items-center gap-2">
          <button
            onClick={async () => {
              const res = await apiFetch(`/api/lynx/orchestration`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(orch),
              }).catch(() => null);
              if (res?.ok) { setOrch(await res.json()); setSaved("ok"); }
              else setSaved("ko");
            }}
            className="cursor-pointer rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-background transition-all hover:bg-accent-bright active:scale-[0.98]"
          >
            Enregistrer l&apos;orchestration
          </button>
          {saved === "ok" && (
            <span className="text-[11px] text-good">enregistrée — appliquée dès la prochaine analyse.</span>
          )}
          {saved === "ko" && <span className="text-[11px] text-bad">échec de l&apos;enregistrement.</span>}
        </div>
      </section>}

      {expert && <section>
        <h3 className="text-sm font-semibold text-foreground">
          Prompts des agents{" "}
          <Hint text="Le markdown exact envoyé à chaque agent IA (skills/*.md). Modifiable ici ; rechargé du disque à chaque appel — effet dès l'analyse suivante." />
        </h3>
        <p className="mt-1 text-xs text-fg-muted">{skills.length} prompts.</p>

        <div className="mt-3 rounded-lg border border-edge bg-surface-2 px-3 py-2.5">
          <p className="flex items-center gap-1.5 text-xs font-medium text-foreground">
            Débat contradictoire sur les BLOQUANT
            <Hint text="Fail-safe : toute erreur LLM pendant le débat conserve le BLOQUANT initial. Les constats factuels (doublons d'identifiants, liens cassés, cycles, dépassements d'allocation) ne passent jamais au débat." />
          </p>
          <p className="mt-1 text-xs leading-relaxed text-fg-muted">
            Avant d&apos;être rendu, chaque verdict BLOQUANT issu d&apos;un agent IA (audit comme
            analyse d&apos;impact) est contesté : <span className="font-mono">defense_exigence</span>,
            l&apos;avocat de la défense, tente de le réfuter à partir du contexte de traçabilité
            de l&apos;exigence accusée ; <span className="font-mono">juge_verdict</span>, le juge,
            tranche. Un verdict réfuté est rétrogradé en avertissement — jamais supprimé — et
            le débat (plaidoyer, motivation) reste dépliable sur le constat.
          </p>
        </div>

        <div className="mt-3 rounded-lg border border-edge bg-surface-2 px-3 py-2.5">
          <p className="flex items-center gap-1.5 text-xs font-medium text-foreground">
            Génération descendante d&apos;exigences filles
            <Hint text="Un niveau à la fois (pas de cascade), refusé au plancher L5. Les filles proposées sont auto-auditées sur une copie de la matrice (débat contradictoire inclus) et réécrites si signalées ; rien n'est créé sans validation sélective." />
          </p>
          <p className="mt-1 text-xs leading-relaxed text-fg-muted">
            Depuis une exigence sélectionnée, <span className="font-mono">generation_filles</span>{" "}
            propose 2 à 7 exigences filles L(n+1) qui la déclinent — en évitant la redondance
            avec les filles existantes et en suivant le référentiel de règles de rédaction. Il
            liste honnêtement les aspects de la mère qui restent non couverts.
          </p>
        </div>

        <div className="mt-3 space-y-2">
          {skills.map((s) => (
            <SkillEditor key={s.name} s={s} />
          ))}
        </div>
      </section>}
    </div>
  );
}
