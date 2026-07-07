"use client";

/** Panneaux de la vue LynX : formulaires d'édition de liens, création
 * d'exigence fille et section Audit de la matrice. La logique (analyse,
 * audit) reste dans index.tsx et arrive par props. */

import { useMemo, useState } from "react";

import { Dot, Hint, Meter, Pill, Spinner, type Tone } from "@/components/ui";
import { type Req } from "@/components/req-graph";
import {
  DebateBadge, GlassBox, RoleChip, SEV_TONE, btnGhost, btnPrimary,
  type AuditFinding, type AuditReport, type FixItem, type FixProgress, type FixRecap,
} from "@/components/requirements/blocks";

/** Statuts finaux de la correction en lot (miroir de lynx/src/autofix.py). */
const STATUT_STYLE: Record<string, { label: string; tone: Tone }> = {
  corrigee: { label: "corrigée", tone: "good" },
  amelioree: { label: "améliorée", tone: "warn" },
  recalcitrante: { label: "récalcitrante", tone: "bad" },
  echec_suggestion: { label: "échec", tone: "neutral" },
  inchangee: { label: "inchangée", tone: "neutral" },
};

/** Une correction est proposable si l'agent a produit un texte différent. */
const isSelectable = (it: FixItem) =>
  it.statut !== "echec_suggestion" && it.statut !== "inchangee";

/** Formulaire d'ajout de lien DERIVE (mère au niveau N-1 ou fille au niveau N+1). */
export function LinkForm({ sel, corpus, disabled, onLink }: {
  sel: Req; corpus: Req[]; disabled: boolean;
  onLink: (action: Record<string, unknown>) => void;
}) {
  const [direction, setDirection] = useState<"mere" | "fille">("mere");
  const [other, setOther] = useState("");
  const candidates = corpus.filter((r) =>
    r.id !== sel.id && r.niveau === sel.niveau + (direction === "mere" ? -1 : 1));
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-edge pt-2">
      <select value={direction}
              onChange={(e) => { setDirection(e.target.value as "mere" | "fille"); setOther(""); }}
              className="rounded-md border border-edge bg-surface px-2 py-1 text-[11px] text-foreground">
        <option value="mere">Rattacher à une mère (L{sel.niveau - 1})</option>
        <option value="fille">Adopter une fille (L{sel.niveau + 1})</option>
      </select>
      <select value={other} onChange={(e) => setOther(e.target.value)}
              className="rounded-md border border-edge bg-surface px-2 py-1 font-mono text-[11px] text-foreground">
        <option value="">choisir…</option>
        {candidates.map((r) => (
          <option key={r.id} value={r.id}>{r.id}</option>
        ))}
      </select>
      <button
        onClick={() => {
          if (!other) return;
          const [child, parent] = direction === "mere" ? [sel.id, other] : [other, sel.id];
          onLink({ action_type: "LINK", target_id: child, link_target: parent,
                   link_type: "DERIVE" });
        }}
        disabled={disabled || !other}
        className="cursor-pointer rounded-md border border-accent/50 px-2 py-1 text-[11px] text-accent-bright transition-colors hover:bg-accent/10 disabled:opacity-40"
      >
        Lier (DERIVE)
      </button>
    </div>
  );
}

/** Créer une exigence FILLE de la sélection (id optionnel, niveau N+1 par défaut). */
export function CreateChildForm({ sel, disabled, onCreate }: {
  sel: Req; disabled: boolean;
  onCreate: (action: Record<string, unknown>) => void;
}) {
  // Remonté via key={sel.id} : l'état repart proprement à chaque sélection.
  const [text, setText] = useState("");
  const [customId, setCustomId] = useState("");
  const [niveau, setNiveau] = useState(Math.min(sel.niveau + 1, 5));
  return (
    <details className="chat-details">
      <summary>
        Créer une exigence fille
        <Hint text="Nouvelle exigence rattachée à la sélection (parent principal). L'impact est analysé avant application." />
      </summary>
      <div className="mt-2 space-y-2">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={2}
          placeholder="Texte de la nouvelle exigence…"
          className="w-full rounded-lg border border-edge bg-surface px-3 py-2 text-xs leading-relaxed text-foreground placeholder:text-fg-faint focus:border-accent focus:outline-none"
        />
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={customId}
            onChange={(e) => setCustomId(e.target.value)}
            placeholder="ID (vide = auto)"
            className="w-36 rounded-md border border-edge bg-surface px-2 py-1 font-mono text-[11px] text-foreground placeholder:text-fg-faint focus:outline-none"
          />
          <label className="flex items-center gap-1 text-[11px] text-fg-muted">
            niveau
            <input type="number" min={0} max={5} value={niveau}
                   onChange={(e) => setNiveau(Number(e.target.value))}
                   className="w-14 rounded-md border border-edge bg-surface px-2 py-1 font-mono text-[11px] text-foreground focus:outline-none" />
          </label>
          <button
            onClick={() => {
              if (!text.trim()) return;
              onCreate({
                action_type: "CREATE",
                target_id: customId.trim() || `REQ-NEW-${Math.random().toString(16).slice(2, 8).toUpperCase()}`,
                new_text: text.trim(), parent_id: sel.id, niveau,
                domaine: sel.domaine ?? "Général",
              });
              setText("");
              setCustomId("");
            }}
            disabled={disabled || !text.trim()}
            className="cursor-pointer rounded-md border border-accent/50 px-2 py-1 text-[11px] text-accent-bright transition-colors hover:bg-accent/10 disabled:opacity-40"
          >
            Analyser la création
          </button>
        </div>
      </div>
    </details>
  );
}

// ─── Génération descendante de filles (L n+1) ──────────────────────────────

export type GenFille = { id_propose: string; texte: string; justification: string;
                         aspect_couvert: string; findings_restants: AuditFinding[];
                         statut: string };
export type GenRecap = { filles: GenFille[]; aspects_non_couverts: string[];
                         niveau_filles: number };
export type GenProgress = { phase: string; passe?: number; done?: number;
                            total?: number; req_id?: string | null };

const GEN_STATUT_STYLE: Record<string, { label: string; tone: Tone }> = {
  conforme: { label: "conforme", tone: "good" },
  ...STATUT_STYLE,
};

function genPhaseLabel(p: GenProgress): string {
  if (p.phase === "generation") return "l'agent propose des exigences filles…";
  if (p.phase === "reecriture")
    return `réécriture des filles signalées (passe ${p.passe ?? 1}) — ${p.done ?? 0}/${p.total ?? 0}`;
  return `auto-audit de la matrice candidate — ${p.done ?? 0}/${p.total ?? 0} exigences`;
}

/** Bouton + progression + récap sélectif de la génération de filles :
 * même gestuelle que la correction en lot (rien n'est créé sans validation). */
export function GenerateChildrenBlock({ sel, llmOk, disabled, genRunning, genProgress,
                                        genRecap, genError, onRun, onCancel, onApply,
                                        onClose }: {
  sel: Req; llmOk: boolean; disabled: boolean;
  genRunning: boolean; genProgress: GenProgress | null;
  genRecap: GenRecap | null; genError: string | null;
  onRun: () => void; onCancel: () => void;
  onApply: (items: { id: string; texte: string; niveau: number }[]) => void;
  onClose: () => void;
}) {
  const [unchecked, setUnchecked] = useState<Set<string>>(new Set());
  const plafond = sel.niveau >= 5;
  const selection = (genRecap?.filles ?? []).filter((f) => !unchecked.has(f.id_propose));
  return (
    <div className="space-y-2">
      {!genRunning && !genRecap && (
        <p className="flex items-center gap-1.5">
          <button onClick={onRun} disabled={disabled || plafond || !llmOk}
                  title={plafond ? "Niveau plancher L5 : une L5 ne se décline pas." : undefined}
                  className="cursor-pointer rounded-md border border-accent/50 px-2 py-1 text-[11px] text-accent-bright transition-colors hover:bg-accent/10 disabled:opacity-40">
            Générer des exigences filles (L{Math.min(sel.niveau + 1, 5)})
          </button>
          <Hint text="Un agent propose 2 à 7 exigences filles qui déclinent la sélection (en évitant la redondance avec les filles existantes). Elles sont auto-auditées sur une copie de la matrice — débat contradictoire inclus — et réécrites si signalées, avant validation sélective. Rien n'est créé sans votre accord." />
        </p>
      )}
      {genRunning && (
        <p className="flex flex-wrap items-center gap-2 text-xs text-fg-muted">
          <Spinner /> {genProgress ? genPhaseLabel(genProgress) : "démarrage…"}
          <button onClick={onCancel} className={btnGhost}>Annuler</button>
        </p>
      )}
      {genError && <p className="text-xs text-bad">{genError}</p>}
      {genRecap && !genRunning && (
        <div className="rise-in space-y-2">
          <p className="text-xs font-semibold text-foreground">
            {genRecap.filles.length} fille(s) proposée(s) — niveau L{genRecap.niveau_filles}
          </p>
          {genRecap.filles.map((f) => {
            const s = GEN_STATUT_STYLE[f.statut] ?? { label: f.statut, tone: "neutral" as Tone };
            return (
              <div key={f.id_propose}
                   className="rounded-lg border border-edge bg-surface-2 px-3 py-2.5 text-xs">
                <p className="flex flex-wrap items-center gap-2">
                  <input type="checkbox" checked={!unchecked.has(f.id_propose)}
                         onChange={(e) => setUnchecked((prev) => {
                           const next = new Set(prev);
                           if (e.target.checked) next.delete(f.id_propose);
                           else next.add(f.id_propose);
                           return next;
                         })}
                         className="accent-(--accent)" />
                  <span className="font-mono text-foreground">{f.id_propose}</span>
                  <Pill tone={s.tone}>{s.label}</Pill>
                  {f.aspect_couvert && (
                    <span className="rounded bg-muted px-1 py-px font-mono text-[10px] text-fg-faint">
                      {f.aspect_couvert}
                    </span>
                  )}
                </p>
                <p className="mt-1.5 leading-relaxed text-foreground">{f.texte}</p>
                {f.findings_restants.length > 0 && (
                  <ul className="mt-1 space-y-0.5 text-[11px] text-fg-muted">
                    {f.findings_restants.map((fd, i) => (
                      <li key={i}>
                        <span className="rounded bg-muted px-1 py-px font-mono text-[10px] text-fg-faint">
                          {fd.axis}
                        </span>{" "}
                        {fd.message}
                      </li>
                    ))}
                  </ul>
                )}
                {f.justification && (
                  <details className="chat-details mt-1.5">
                    <summary>Justification de l&apos;agent</summary>
                    <p className="mt-1 flex items-start gap-2 text-[11px] leading-relaxed text-fg-muted">
                      <RoleChip role="IA" />
                      <span>{f.justification}</span>
                    </p>
                  </details>
                )}
              </div>
            );
          })}
          {genRecap.aspects_non_couverts.length > 0 && (
            <p className="text-[11px] text-warn">
              Aspects de la mère non couverts (transparence de couverture) :{" "}
              {genRecap.aspects_non_couverts.join(" · ")}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2 pt-1">
            <button
              onClick={() => onApply(selection.map((f) => ({
                id: f.id_propose, texte: f.texte, niveau: genRecap.niveau_filles })))}
              disabled={selection.length === 0}
              className={btnPrimary}
            >
              Valider la sélection ({selection.length})
            </button>
            <button onClick={onClose} className={btnGhost}>Annuler</button>
          </div>
        </div>
      )}
    </div>
  );
}

/** L'audit : jauge + défauts détaillés, conformes comptés, correction en lot. */
export function AuditPanel({ auditRunning, auditProgress, audit, deep, setDeep, onRun, onSelect,
                             llmOk, fixing, fixProgress, fixRecap, onFix, onCancelFix,
                             onApplyFix, onCloseRecap }: {
  auditRunning: boolean;
  auditProgress: [number, number] | null;
  audit: AuditReport | null;
  deep: boolean;
  setDeep: (v: boolean) => void;
  onRun: () => void;
  onSelect: (id: string) => void;
  llmOk: boolean;
  fixing: boolean;
  fixProgress: FixProgress | null;
  fixRecap: FixRecap | null;
  onFix: () => void;
  onCancelFix: () => void;
  onApplyFix: (items: { req_id: string; texte: string }[]) => void;
  onCloseRecap: () => void;
}) {
  const nBloquant = audit?.findings.filter((f) => f.severity === "BLOQUANT").length ?? 0;
  return (
    <div className="rounded-xl border border-edge bg-surface px-5 py-4">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="text-sm font-semibold text-foreground">Audit de la matrice</h3>
        <label className="flex items-center gap-1.5 text-xs text-fg-muted">
          <input type="checkbox" checked={deep} onChange={(e) => setDeep(e.target.checked)}
                 className="accent-(--accent)" />
          Audit IA par exigence
          <Hint text="Décoché : règles structurelles et doublons vectoriels seulement (rapide)." />
        </label>
        <button onClick={onRun} disabled={auditRunning || fixing}
                className={`ml-auto ${btnPrimary}`}>
          {auditRunning ? "Audit en cours…" : "Auditer"}
        </button>
      </div>
      {auditRunning && (
        <div className="mt-3">
          <p className="flex items-center gap-2 text-xs text-fg-muted">
            <Spinner />
            {auditProgress
              ? `${auditProgress[0]}/${auditProgress[1]} exigences auditées`
              : "Règles structurelles et doublons vectoriels…"}
          </p>
          {auditProgress && (
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
              <div className="h-full rounded-full bg-accent transition-[width] duration-500"
                   style={{ width: `${(100 * auditProgress[0]) / (auditProgress[1] || 1)}%` }} />
            </div>
          )}
        </div>
      )}
      {audit && (
        <div className="rise-in mt-3 space-y-3">
          <div className="flex flex-wrap items-end gap-6">
            <div>
              <p className="text-[10px] font-medium uppercase tracking-[0.18em] text-fg-faint">
                Score de santé
              </p>
              <p className="font-mono text-3xl font-bold tabular-nums"
                 style={{ color: audit.score >= 80 ? "var(--good)"
                          : audit.score >= 50 ? "var(--warn)" : "var(--bad)" }}>
                {audit.score}
                <span className="text-sm text-fg-faint">/100</span>
              </p>
            </div>
            <div className="min-w-44 flex-1">
              <Meter label="Santé de la matrice" value={audit.score / 100} invert
                     hint="100 − pénalités (BLOQUANT −9, ATTENTION −3)." />
            </div>
            <p className="text-[11px] text-fg-faint">
              Matrice ▸ Règles structurelles · Doublons vectoriels
              {deep ? ` · ${audit.n} audits IA` : ""} ▸ Score
            </p>
          </div>
          {audit.flagged_ids.length === 0 ? (
            <p className="flex items-center gap-2 text-xs text-fg-muted">
              <Dot tone="good" /> Aucune exigence signalée —{" "}
              <span className="font-mono tabular-nums">{audit.n}</span> conformes.
            </p>
          ) : (
            <>
              <p className="flex items-center gap-2 text-xs text-fg-muted">
                <Dot tone={nBloquant > 0 ? "bad" : "warn"} />
                <span className="font-mono tabular-nums">{audit.flagged_ids.length}</span>
                exigence(s) en défaut —{" "}
                <span className="font-mono tabular-nums">
                  {audit.n - audit.flagged_ids.length}
                </span>{" "}
                conformes
                {audit.n_non_audite > 0 && ` · ${audit.n_non_audite} non auditées`}
              </p>
              <div className="space-y-1.5">
                {audit.flagged_ids.map((rid) => (
                  <details key={rid} className="chat-details">
                    <summary className="text-xs">
                      <button
                        onClick={(e) => { e.preventDefault(); onSelect(rid); }}
                        className="cursor-pointer font-mono transition-colors hover:text-accent-bright"
                      >
                        {rid}
                      </button>
                      <span className="text-fg-faint">
                        · {audit.findings.filter((f) => f.req_id === rid).length} constat(s)
                      </span>
                    </summary>
                    <ul className="mt-1.5 space-y-1 text-xs text-fg-muted">
                      {audit.findings.filter((f) => f.req_id === rid).map((f, i) => (
                        <li key={i} className="flex items-start gap-2">
                          <span className="mt-1"><Dot tone={SEV_TONE[f.severity] ?? "neutral"} /></span>
                          <span>
                            <span className="rounded bg-muted px-1 py-px font-mono text-[10px] text-fg-faint">
                              {f.axis}
                            </span>{" "}
                            {f.message}
                            <DebateBadge debate={f.debate} />
                          </span>
                        </li>
                      ))}
                    </ul>
                  </details>
                ))}
              </div>

              {/* Correction en lot : corriger → ré-auditer (3 passes max),
                  puis validation sélective — rien n'est appliqué sans accord. */}
              {llmOk && !fixing && !fixRecap && (
                <div className="flex items-center gap-1.5 border-t border-edge pt-3">
                  <button onClick={onFix} disabled={auditRunning} className={btnPrimary}>
                    Corriger tout ({audit.flagged_ids.length})
                  </button>
                  <Hint text="Pour chaque exigence signalée (bloquante ou warning), l'agent de rédaction propose une réécriture ; la matrice candidate est ré-auditée, et les exigences encore signalées sont retentées (3 passes max). Rien n'est appliqué sans votre validation dans le récapitulatif." />
                </div>
              )}
              {fixing && (
                <div className="flex flex-wrap items-center gap-3 border-t border-edge pt-3 text-xs text-fg-muted">
                  <Spinner />
                  {fixProgress
                    ? fixProgress.phase === "audit"
                      ? `ré-audit (passe ${fixProgress.passe}) — ${fixProgress.done}/${fixProgress.total} exigences`
                      : `passe ${fixProgress.passe} — exigence ${fixProgress.done}/${fixProgress.total}` +
                        (fixProgress.req_id ? ` · ${fixProgress.req_id}` : "")
                    : "Correction en lot…"}
                  <button onClick={onCancelFix} className={btnGhost}>Annuler</button>
                </div>
              )}
              {fixRecap && !fixing && (
                <FixRecapView recap={fixRecap} onApply={onApplyFix} onClose={onCloseRecap} />
              )}
            </>
          )}
          <GlassBox
            exchanges={audit.exchanges.map((x) => ({
              agent: `Audit ${x.req_id}`, role: "IA",
              input: x.input, output: x.output,
            }))}
            title="Comment LynX a audité — boîte de verre"
          />
        </div>
      )}
    </div>
  );
}

/** Récapitulatif de la correction en lot : une ligne par exigence (diff
 * avant/après, statut, justification dépliable), validation sélective. */
function FixRecapView({ recap, onApply, onClose }: {
  recap: FixRecap;
  onApply: (items: { req_id: string; texte: string }[]) => void;
  onClose: () => void;
}) {
  // Cochées par défaut ; on ne mémorise que les DÉcochées (id).
  const [unchecked, setUnchecked] = useState<Set<string>>(new Set());
  const selection = useMemo(
    () => recap.recap.filter((it) => isSelectable(it) && !unchecked.has(it.req_id)),
    [recap.recap, unchecked]);
  const c = recap.compteurs;
  const resume = [
    c.corrigees ? `${c.corrigees} corrigée(s)` : "",
    c.ameliorees ? `${c.ameliorees} améliorée(s)` : "",
    c.recalcitrantes ? `${c.recalcitrantes} récalcitrante(s)` : "",
    c.echecs ? `${c.echecs} échec(s)` : "",
    c.inchangees ? `${c.inchangees} inchangée(s)` : "",
  ].filter(Boolean).join(" · ");
  return (
    <div className="rise-in space-y-2 border-t border-edge pt-3">
      <p className="flex items-center gap-1.5 text-xs text-fg-muted">
        <span className="font-semibold text-foreground">
          Récapitulatif — {recap.passes} passe(s)
        </span>
        {resume && <span className="text-fg-faint">· {resume}</span>}
        {recap.score_apres != null && (
          <span className="text-fg-faint">
            · score projeté <span className="font-mono tabular-nums">{recap.score_apres}</span>/100
          </span>
        )}
        <Hint text="Cochez les corrections à retenir : seules les lignes validées seront appliquées à la matrice (les échecs et textes inchangés ne sont pas applicables). L'audit devra être relancé ensuite." />
      </p>
      <div className="space-y-1.5">
        {recap.recap.map((it) => {
          const s = STATUT_STYLE[it.statut] ?? { label: it.statut, tone: "neutral" as Tone };
          const selectable = isSelectable(it);
          return (
            <div key={it.req_id}
                 className="rounded-lg border border-edge bg-surface-2 px-3 py-2.5 text-xs">
              <p className="flex flex-wrap items-center gap-2">
                <input
                  type="checkbox"
                  checked={selectable && !unchecked.has(it.req_id)}
                  disabled={!selectable}
                  onChange={(e) => setUnchecked((prev) => {
                    const next = new Set(prev);
                    if (e.target.checked) next.delete(it.req_id);
                    else next.add(it.req_id);
                    return next;
                  })}
                  className="accent-(--accent) disabled:opacity-40"
                />
                <span className="font-mono text-foreground">{it.req_id}</span>
                <Pill tone={s.tone}>{s.label}</Pill>
                <span className="text-[11px] text-fg-faint">
                  {it.findings_avant.length} constat(s) → {it.findings_apres.length}
                  {it.erreur ? ` · ${it.erreur}` : ""}
                </span>
              </p>
              {it.texte_apres !== it.texte_avant ? (
                <>
                  <p className="mt-1.5 leading-relaxed text-fg-faint">
                    <span className="mr-1.5 rounded bg-muted px-1 py-px font-mono text-[10px]">avant</span>
                    <span className="line-through decoration-bad/50">{it.texte_avant}</span>
                  </p>
                  <p className="mt-1 leading-relaxed text-foreground">
                    <span className="mr-1.5 rounded bg-muted px-1 py-px font-mono text-[10px]">après</span>
                    {it.texte_apres}
                  </p>
                </>
              ) : (
                <p className="mt-1.5 leading-relaxed text-fg-faint">{it.texte_avant}</p>
              )}
              {it.justification && (
                <details className="chat-details mt-1.5">
                  <summary>Justification de l&apos;agent</summary>
                  <p className="mt-1 flex items-start gap-2 text-[11px] leading-relaxed text-fg-muted">
                    <RoleChip role="IA" />
                    <span>{it.justification}</span>
                  </p>
                </details>
              )}
            </div>
          );
        })}
      </div>
      <div className="flex flex-wrap items-center gap-2 pt-1">
        <button
          onClick={() => onApply(selection.map((it) => ({ req_id: it.req_id, texte: it.texte_apres })))}
          disabled={selection.length === 0}
          className={btnPrimary}
        >
          Valider la sélection ({selection.length})
        </button>
        <button onClick={onClose} className={btnGhost}>Annuler</button>
      </div>
    </div>
  );
}
