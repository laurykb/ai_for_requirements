"use client";

/** Introduction façon vidéo de marque Thales : une planète vue de loin, mise
 * en rotation pendant que la caméra plonge, puis dézoom — la planète vient se
 * loger sous le A de THALES (elle DEVIENT le point aqua du logo), les lettres
 * apparaissent, l'accueil se révèle.
 *
 * Jouée à CHAQUE retour à l'accueil, passée d'un clic,
 * `prefers-reduced-motion` : pas d'animation du tout. */

import { useEffect, useRef, useState } from "react";

// Géométrie du logotype (viewBox 484×57.4) : le point du A est centré en
// (209.5, 41.4), rayon 9.55. Le globe est posé À SA PLACE FINALE dès le
// départ — seules les transformations scale() racontent le voyage.
const LOGO_W = 340;
const LOGO_H = (LOGO_W * 57.4) / 484;
const DOT = { cx: 209.5 / 484, cy: 41.4 / 57.4, d: (2 * 9.55) / 484 };

const DOCK_MS = 4600; // départ du logo vers son perchoir (haut de page)
const TOTAL_MS = 5700; // fin : le logo statique de l'accueil prend le relais

function Globe({ size }: { size: number }) {
  // Planète aqua : disque, graticule discret, « continents » qui défilent
  // derrière un clip circulaire = illusion de rotation.
  return (
    <svg viewBox="0 0 100 100" width={size} height={size} aria-hidden>
      <defs>
        <clipPath id="globe-clip">
          <circle cx="50" cy="50" r="47" />
        </clipPath>
      </defs>
      <circle cx="50" cy="50" r="47" fill="#0b2531" stroke="#5EBFD4" strokeWidth="2.5" />
      <g clipPath="url(#globe-clip)">
        {/* Deux exemplaires du motif pour un défilement sans couture. */}
        <g className="intro-spin" fill="#5EBFD4" opacity="0.55">
          {[0, 100].map((dx) => (
            <g key={dx} transform={`translate(${dx} 0)`}>
              <ellipse cx="22" cy="34" rx="14" ry="9" />
              <ellipse cx="52" cy="62" rx="18" ry="10" />
              <ellipse cx="84" cy="30" rx="12" ry="8" />
              <ellipse cx="70" cy="78" rx="9" ry="5" />
            </g>
          ))}
        </g>
        {/* Graticule : 2 parallèles + 1 méridien, très discrets. */}
        <g fill="none" stroke="#5EBFD4" strokeWidth="0.8" opacity="0.35">
          <ellipse cx="50" cy="50" rx="47" ry="16" />
          <ellipse cx="50" cy="50" rx="47" ry="34" />
          <ellipse cx="50" cy="50" rx="18" ry="47" />
        </g>
      </g>
    </svg>
  );
}

export function Intro() {
  const [show, setShow] = useState<boolean | null>(null);
  const [docking, setDocking] = useState(false);
  const logoRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const timers: ReturnType<typeof setTimeout>[] = [];
    // Décision différée d'un tick (la règle set-state-in-effect n'aime pas
    // les setState synchrones, même pour un choix ne dépendant pas du rendu).
    timers.push(setTimeout(() => {
      const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      if (reduced) {
        setShow(false);
        return;
      }
      setShow(true);
      // Docking : le logo file se loger sur son perchoir statique (#home-thales),
      // mesuré au vol (FLIP) — pendant que le voile s'efface.
      timers.push(setTimeout(() => {
        const target = document.getElementById("home-thales");
        const el = logoRef.current;
        if (target && el) {
          const t = target.getBoundingClientRect();
          const w = el.getBoundingClientRect();
          const dx = t.left + t.width / 2 - (w.left + w.width / 2);
          const dy = t.top + t.height / 2 - (w.top + w.height / 2);
          el.style.transition = "transform 0.9s cubic-bezier(0.5, 0, 0.2, 1)";
          el.style.transform = `translate(${dx}px, ${dy}px) scale(${t.width / w.width})`;
        }
        setDocking(true);
      }, DOCK_MS));
      timers.push(setTimeout(() => setShow(false), TOTAL_MS));
    }, 0));
    return () => timers.forEach(clearTimeout);
  }, []);

  if (!show) return null;

  const dotSize = DOT.d * LOGO_W;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      onClick={() => setShow(false)}
      role="presentation"
    >
      {/* Voile : opaque pendant le voyage, s'efface pendant le docking. */}
      <div
        className={`absolute inset-0 bg-background transition-opacity duration-700 ${
          docking ? "opacity-0" : "opacity-100"
        }`}
      />
      <div ref={logoRef} className="relative" style={{ width: LOGO_W, height: LOGO_H }}>
        {/* Les lettres du logotype (sans le point : le globe le devient). */}
        <svg
          viewBox="0 0 484 57.4"
          width={LOGO_W}
          height={LOGO_H}
          className="intro-letters absolute inset-0"
          aria-label="Thales"
        >
          <g fill="#eef1f8">
            <path d="m484 42.1c0 7.8-2.3 10.1-8.5 12-6.7 2-18.6 3.3-25.7 3.3-8.4 0-18.9-0.5-27.8-2.6v-9h49.3v-12.3h-34.9c-10.5 0-14.4-2.9-14.4-13.1v-5.4c0-8.1 2.4-10.5 8.9-12.2 6.6-1.7 17.4-2.8 24.5-2.8 8.6 0 18.9 0.7 27.8 2.7v9h-48.5v10.3h34.9c10.5 0 14.4 2.8 14.4 13.1z" />
            <path d="m400 54.7c-10.2 2-20.5 2.6-30.5 2.6s-20.4-0.6-30.7-2.6v-52c10.2-2 20.6-2.7 30.5-2.7 10 0 20.1 0.6 30.3 2.7v9.3h-46.2v10.4h30.1v10.8h-30.1v12.3h46.4v9.2z" />
            <path d="m321 54.7c-9.2 2-18.4 2.6-27.3 2.6s-18.3-0.5-27.5-2.6v-53.4h14.7v43.5h40.1z" />
            <path d="m249 55.1c-4.9 1.4-11.4 1.9-16.2 2l-22.7-45.8h-1.3l-22.6 45.8c-4.8-0.1-10.5-0.6-15.4-2l28.7-53.7h20.5z" />
            <path d="m153 55.1c-4.7 1.4-9.7 1.8-14.7 1.9v-23.3h-36.5v23.3c-5-0.1-10-0.6-14.7-1.9v-52.8c4.7-1.4 9.7-1.8 14.7-1.9v22.5h36.5v-22.5c5 0.1 10 0.6 14.7 1.9z" />
            <path d="m66.2 12.1h-25.8v44h-14.6v-44h-25.8v-9.4c11.1-2 22.3-2.7 33.1-2.7s22 0.6 33.1 2.7z" />
          </g>
        </svg>

        {/* Le globe : posé à la place du point du A, le voyage = du scale. */}
        <div
          className="intro-globe absolute"
          style={{
            width: dotSize,
            height: dotSize,
            left: DOT.cx * LOGO_W - dotSize / 2,
            top: DOT.cy * LOGO_H - dotSize / 2,
          }}
        >
          <Globe size={dotSize} />
        </div>
      </div>

      {!docking && (
        <p className="absolute bottom-8 text-xs text-fg-faint">cliquer pour passer</p>
      )}
    </div>
  );
}
