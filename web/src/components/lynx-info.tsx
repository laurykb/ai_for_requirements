"use client";

/** LynX — onglet Informations : la salle des machines.
 * 1. Prompts des agents (skills/*.md) : lisibles et ÉDITABLES depuis l'UI,
 *    rechargés à chaud (effet dès l'analyse suivante).
 * 2. Orchestration : ordre d'exécution et activation des agents d'analyse
 *    (déterministes = séquentiels ; sémantiques = lancés en parallèle). */

import { useEffect, useRef, useState } from "react";

import { API_BASE, getJSON } from "@/lib/api";
import { Banner, Hint, Spinner } from "@/components/ui";
import { streamPost } from "@/components/requirements/blocks";

type Skill = { name: string; content: string };
type OrchEntry = { name: string; label: string; enabled: boolean };
type Orchestration = { deterministic: OrchEntry[]; semantic: OrchEntry[] };

type AxisScore = { tp?: number; fp?: number; fn?: number; precision: number; recall: number };
type EvalScores = {
  cases: number; precision: number; recall: number; f1: number;
  per_axis: Record<string, AxisScore>;
};

const btn =
  "cursor-pointer rounded-md border border-edge px-2 py-1 text-[11px] text-fg-muted " +
  "transition-colors hover:border-accent/60 hover:text-foreground disabled:opacity-40";

/** Éditeur d'un prompt d'agent (sauvegarde explicite, statut inline).
 * « Tester ce prompt sur le golden set » : sauvegarde d'abord si besoin
 * (le harnais lit les prompts depuis le disque), puis lance l'éval. */
function SkillEditor({ s, onTest, evalRunning }: {
  s: Skill; onTest: () => void; evalRunning: boolean;
}) {
  const [content, setContent] = useState(s.content);
  const [status, setStatus] = useState<string | null>(null);
  const dirty = content !== s.content && status !== "enregistré";

  const save = async (): Promise<boolean> => {
    const res = await fetch(`${API_BASE}/api/lynx/skills/${encodeURIComponent(s.name)}`, {
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
        <button
          onClick={async () => {
            if (dirty && !(await save())) return;
            onTest();
          }}
          disabled={evalRunning}
          className={btn}
          title="Sauvegarde le prompt si besoin, puis mesure précision/rappel sur le jeu d'évaluation"
        >
          Tester ce prompt sur le golden set
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

/** Delta vs baseline : vert si ≥, ambre si baisse (rien si pas de baseline). */
function Delta({ now, before }: { now: number; before?: number }) {
  if (before == null) return null;
  const d = now - before;
  const cls = d >= 0 ? "text-good" : "text-warn";
  return (
    <span className={`ml-1 font-mono text-[10px] tabular-nums ${cls}`}>
      {d >= 0 ? "+" : ""}{d.toFixed(3)}
    </span>
  );
}

/** Résultats d'éval : micro + tableau par axe, deltas vs baseline. */
function EvalScoresView({ scores, baseline, title }: {
  scores: EvalScores; baseline: EvalScores | null; title: string;
}) {
  const axes = Object.keys(scores.per_axis);
  const micro: [string, number, number | undefined][] = [
    ["Précision", scores.precision, baseline?.precision],
    ["Rappel", scores.recall, baseline?.recall],
    ["F1", scores.f1, baseline?.f1],
  ];
  return (
    <div className="space-y-2">
      <p className="text-xs text-fg-muted">
        {title} — <span className="font-mono tabular-nums">{scores.cases}</span> cas
      </p>
      <div className="flex flex-wrap gap-4">
        {micro.map(([label, val, base]) => (
          <span key={label} className="text-xs text-foreground">
            {label}{" "}
            <span className="font-mono tabular-nums">{val.toFixed(3)}</span>
            <Delta now={val} before={base} />
          </span>
        ))}
      </div>
      <table className="w-full text-left text-xs">
        <thead>
          <tr className="text-[10px] uppercase tracking-wide text-fg-faint">
            <th className="py-1 pr-2 font-medium">Axe</th>
            <th className="py-1 pr-2 font-medium">Précision</th>
            <th className="py-1 font-medium">Rappel</th>
          </tr>
        </thead>
        <tbody>
          {axes.map((a) => {
            const ax = scores.per_axis[a];
            const base = baseline?.per_axis?.[a];
            return (
              <tr key={a} className="border-t border-edge">
                <td className="py-1 pr-2 font-mono text-[11px] text-fg-muted">{a}</td>
                <td className="py-1 pr-2 font-mono tabular-nums text-foreground">
                  {ax.precision.toFixed(3)}
                  <Delta now={ax.precision} before={base?.precision} />
                </td>
                <td className="py-1 font-mono tabular-nums text-foreground">
                  {ax.recall.toFixed(3)}
                  <Delta now={ax.recall} before={base?.recall} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
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
  const [skills, setSkills] = useState<Skill[] | null>(null);
  const [orch, setOrch] = useState<Orchestration | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Éval golden set : baseline (dernier run), run en cours, résultat.
  const [evalBaseline, setEvalBaseline] = useState<EvalScores | null>(null);
  const [evalFast, setEvalFast] = useState(true);
  const [evalProgress, setEvalProgress] = useState<{ done: number; total: number } | null>(null);
  const [evalScores, setEvalScores] = useState<EvalScores | null>(null);
  const [evalError, setEvalError] = useState<string | null>(null);
  const evalAbort = useRef<AbortController | null>(null);
  const evalRunning = evalProgress !== null;

  const runEval = async () => {
    if (evalRunning) return;
    setEvalError(null);
    setEvalScores(null);
    setEvalProgress({ done: 0, total: 0 });
    const ctrl = new AbortController();
    evalAbort.current = ctrl;
    let result: EvalScores | null = null;
    let failed: string | null = null;
    try {
      await streamPost("/api/lynx/eval", { fast: evalFast }, (ev) => {
        if (ev.type === "progress")
          setEvalProgress({ done: ev.done as number, total: ev.total as number });
        else if (ev.type === "result") result = ev as unknown as EvalScores;
        else if (ev.type === "error") failed = ev.message as string;
      }, ctrl.signal);
    } catch (e) {
      // Annulation volontaire : pas une erreur.
      if (!(e instanceof DOMException && e.name === "AbortError"))
        failed = "flux interrompu — API redémarrée ?";
    }
    evalAbort.current = null;
    setEvalProgress(null);
    if (failed) setEvalError(failed);
    else if (result) {
      setEvalScores(result);
      // Le run devient la nouvelle baseline du prochain (le harnais a
      // réécrit last_eval.json) ; on garde l'ancienne pour l'affichage.
    }
  };

  useEffect(() => {
    const t = setTimeout(async () => {
      try {
        const [s, o] = await Promise.all([
          getJSON<{ skills: Skill[] }>("/api/lynx/skills"),
          getJSON<Orchestration>("/api/lynx/orchestration"),
        ]);
        setSkills(s.skills);
        setOrch(o);
        // Baseline non bloquante : absente au premier lancement.
        getJSON<EvalScores & { exists: boolean }>("/api/lynx/eval/last")
          .then((b) => { if (b.exists) setEvalBaseline(b); })
          .catch(() => null);
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
      <section>
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
              const res = await fetch(`${API_BASE}/api/lynx/orchestration`, {
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
      </section>

      <section>
        <h3 className="text-sm font-semibold text-foreground">
          Prompts des agents{" "}
          <Hint text="Le markdown exact envoyé à chaque agent IA (skills/*.md). Modifiable ici ; rechargé du disque à chaque appel — effet dès l'analyse suivante." />
        </h3>
        <p className="mt-1 text-xs text-fg-muted">{skills.length} prompts.</p>

        <div className="mt-3 rounded-lg border border-edge bg-surface-2 px-3 py-2.5">
          <p className="flex items-center gap-1.5 text-xs font-medium text-foreground">
            Évaluation sur le golden set
            <Hint text="Mesure précision / rappel / F1 des agents d'analyse sur le jeu d'évaluation étalonné (cas à verdict connu). Chaque éditeur de prompt a un bouton « Tester ce prompt sur le golden set » : le prompt est sauvegardé puis le harnais tourne avec. Le delta est calculé contre le dernier run." />
          </p>
          <label className="mt-2 flex w-fit cursor-pointer items-center gap-1.5 text-xs text-fg-muted">
            <input type="checkbox" checked={evalFast} disabled={evalRunning}
                   onChange={(e) => setEvalFast(e.target.checked)}
                   className="accent-(--accent)" />
            mode rapide (sans LLM)
            <Hint text="Coché : agents déterministes seuls — retour immédiat, mais les axes portés par les agents IA restent muets. Décoché : éval complète avec appels LLM (plusieurs minutes)." />
          </label>
          {evalRunning && (
            <div className="mt-2 flex items-center gap-3 text-xs text-fg-muted">
              <Spinner />
              {evalProgress!.total
                ? <>cas <span className="font-mono tabular-nums">{evalProgress!.done}/{evalProgress!.total}</span></>
                : "démarrage…"}
              <button onClick={() => evalAbort.current?.abort()} className={btn}>Annuler</button>
            </div>
          )}
          {evalError && <p className="mt-2 text-xs text-bad">{evalError}</p>}
          {evalScores && !evalRunning && (
            <div className="mt-3">
              <EvalScoresView scores={evalScores} baseline={evalBaseline}
                              title={`Run terminé · mode ${evalFast ? "rapide" : "complet"}`} />
            </div>
          )}
          {!evalScores && !evalRunning && evalBaseline && (
            <div className="mt-3">
              <EvalScoresView scores={evalBaseline} baseline={null} title="Dernier run (baseline)" />
            </div>
          )}
        </div>

        <div className="mt-3 space-y-2">
          {skills.map((s) => (
            <SkillEditor key={s.name} s={s} onTest={runEval} evalRunning={evalRunning} />
          ))}
        </div>
      </section>
    </div>
  );
}
