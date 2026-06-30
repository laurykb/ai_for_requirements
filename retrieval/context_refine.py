"""
Affinage du CONTEXTE envoyé à la génération - réduit le bruit, pas l'information.

Diagnostic mesuré du projet : le retrieval est bon (hit@k 0.93) mais la GÉNÉRATION
est le maillon faible (precision 0.66) - on envoie trop de bruit/redondance au modèle.
Deux passes déterministes (0 appel LLM, 0 coût réseau) affinent le contexte juste avant
la génération :

1. dedup_chunks       : retire les passages quasi-redondants (parent-child + RAPTOR
                        produisent des recouvrements) -> plus de diversité utile.
2. reorder_long_context : place les passages les plus pertinents aux EXTRÉMITÉS du
                        contexte (« lost in the middle » : les LLM exploitent mieux le
                        début et la fin d'un long contexte que son milieu).

Les deux préservent l'information (on ne tronque rien) et sont activables par config
pour permettre un A/B mesuré (faithfulness/precision) - la discipline du projet.
"""
from __future__ import annotations

import re


def _tokens(text: str) -> set:
    return set(re.findall(r"\w+", (text or "").lower(), flags=re.UNICODE))


def _overlap(a: set, b: set) -> float:
    """Recouvrement = |a  inter  b| / |plus petit ensemble| (détecte qu'un passage est
    largement INCLUS dans un autre, typique d'un chunk enfant vs sa section parente)."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def dedup_chunks(chunks: list[dict], threshold: float = 0.85) -> list[dict]:
    """Retire les passages quasi-redondants, en gardant le PREMIER (donc le mieux classé,
    les chunks arrivant triés par pertinence décroissante). Préserve l'ordre.

    Un passage est écarté si son contenu recouvre à plus de `threshold` un passage déjà
    retenu (recouvrement sur le plus petit des deux -> attrape l'inclusion enfant subset parent).
    """
    kept: list[dict] = []
    kept_tokens: list[set] = []
    for c in chunks or []:
        toks = _tokens(c.get("doc", ""))
        if not toks:
            kept.append(c)            # rien à comparer (doc vide) -> on n'invente pas de doublon
            kept_tokens.append(toks)
            continue
        if any(_overlap(toks, kt) >= threshold for kt in kept_tokens):
            continue                  # redondant avec un passage déjà retenu
        kept.append(c)
        kept_tokens.append(toks)
    return kept


def reorder_long_context(chunks: list[dict]) -> list[dict]:
    """Réordonne pour atténuer le « lost in the middle » : entrée triée par pertinence
    DÉCROISSANTE -> sortie où les meilleurs sont aux extrémités, les moins bons au milieu.

    Ex. [A,B,C,D,E] (A=meilleur) -> [A,C,E,D,B] : A en tête, B (2e) en queue, E au centre.
    """
    if not chunks or len(chunks) <= 2:
        return list(chunks or [])
    reordered: list[dict] = []
    for i, c in enumerate(reversed(chunks)):   # du moins bon au meilleur
        if i % 2 == 1:
            reordered.append(c)
        else:
            reordered.insert(0, c)
    return reordered
