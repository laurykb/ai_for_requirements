/** Kit UI sobre : panneaux, jauges, pastilles, bulles d'aide. Aucune dépendance.
 * Porté d'AI_for_geopolitics — c'est le langage visuel de toute l'interface. */

import type { ReactNode } from "react";

import { fmt } from "@/lib/format";

export function Panel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-edge bg-surface p-5 ${className}`}>
      {children}
    </section>
  );
}

/** Titre de panneau : surtitre discret + intitulé + bulle d'aide (jargon masqué). */
export function PanelTitle({
  kicker,
  title,
  hint,
  right,
}: {
  kicker?: string;
  title: string;
  hint?: string;
  right?: ReactNode;
}) {
  return (
    <header className="mb-4 flex items-start justify-between gap-3">
      <div>
        {kicker && (
          <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-fg-faint">
            {kicker}
          </p>
        )}
        <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          {title}
          {hint && <Hint text={hint} />}
        </h2>
      </div>
      {right}
    </header>
  );
}

/** Bulle d'aide : icône « ? » + infobulle native (title). */
export function Hint({ text }: { text: string }) {
  return (
    <span
      title={text}
      aria-label={text}
      className="inline-flex h-4 w-4 cursor-help items-center justify-center rounded-full border border-edge-strong text-[10px] leading-none text-fg-muted"
    >
      ?
    </span>
  );
}

export type Tone = "good" | "warn" | "bad" | "neutral" | "accent";

const TONE_TEXT: Record<Tone, string> = {
  good: "text-good",
  warn: "text-warn",
  bad: "text-bad",
  neutral: "text-fg-muted",
  accent: "text-accent-bright",
};

const TONE_BAR: Record<Tone, string> = {
  good: "bg-good",
  warn: "bg-warn",
  bad: "bg-bad",
  neutral: "bg-accent",
  accent: "bg-accent-bright",
};

export function Pill({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border border-edge bg-surface-2 px-2.5 py-0.5 text-xs ${TONE_TEXT[tone]}`}
    >
      {children}
    </span>
  );
}

export function Dot({ tone = "neutral", pulse = false }: { tone?: Tone; pulse?: boolean }) {
  return (
    <span
      className={`inline-block h-1.5 w-1.5 rounded-full ${TONE_BAR[tone]} ${pulse ? "animate-pulse" : ""}`}
    />
  );
}

/** Jauge horizontale [0,1] : libellé, barre, valeur. Le ton peut suivre la valeur. */
export function Meter({
  label,
  value,
  tone,
  invert = false,
  hint,
}: {
  label: string;
  value: number;
  tone?: Tone;
  invert?: boolean; // true : une valeur haute est bonne (vert)
  hint?: string;
}) {
  const v = Math.max(0, Math.min(1, value));
  const risk = invert ? 1 - v : v;
  const auto: Tone = risk < 0.34 ? "good" : risk < 0.67 ? "warn" : "bad";
  const t = tone ?? auto;
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <span className="flex items-center gap-1.5 text-xs text-fg-muted">
          {label}
          {hint && <Hint text={hint} />}
        </span>
        <span className={`font-mono text-xs tabular-nums ${TONE_TEXT[t]}`}>{fmt(v)}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className={`h-full rounded-full transition-[width] duration-300 ease-out ${TONE_BAR[t]}`}
          style={{ width: `${v * 100}%` }}
        />
      </div>
    </div>
  );
}

export function Banner({
  tone = "warn",
  children,
}: {
  tone?: "warn" | "bad" | "neutral";
  children: ReactNode;
}) {
  const border =
    tone === "bad" ? "border-bad/40" : tone === "warn" ? "border-warn/40" : "border-edge-strong";
  return (
    <div
      role="status"
      className={`rounded-lg border ${border} bg-surface-2 px-4 py-3 text-sm text-fg-muted`}
    >
      {children}
    </div>
  );
}

export function Spinner() {
  return (
    <span
      aria-hidden
      className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-fg-faint border-t-accent-bright"
    />
  );
}
