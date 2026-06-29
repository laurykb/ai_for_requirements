"""Outils d'affichage compact pour le debug du retrieval."""

from typing import Dict, Any, List


def _short(txt: str, n: int = 120) -> str:
    """Tronque un texte en une seule ligne."""
    t = (txt or "").replace("\n", " ")
    return t[:n] + ("…" if len(t) > n else "")


def _format_value(value):
    """Formate une valeur de colonne pour l'affichage tabulaire."""
    if value is None:
        return "   -   "
    if isinstance(value, float):
        return f"{value:7.3f}"
    return str(value)[:7].ljust(7)


def print_simple_results(title: str, items: List[Dict[str, Any]], max_items: int = 10, fields=None):
    """Affiche les résultats du retrieval en tableau lisible."""
    print(f"\n=== {title} (top {min(len(items), max_items)}) ===")
    if not items:
        print("Aucun résultat.")
        return
    if fields is None:
        fields = [
            ("score_global", "Score"),
            ("bm25_norm", "BM25n"),
            ("sim_norm", "Simn"),
            ("rrf", "RRF"),
            ("ce_score", "CE"),
            ("bm25", "BM25"),
            ("sim_est", "Sim"),
            ("id", "ID"),
        ]
    header = " | ".join([f"{label:>7}" for _, label in fields] + ["Résumé"])
    print(header)
    print("-" * len(header))
    for it in items[:max_items]:
        vals = [_format_value(it.get(key)) for key, _ in fields]
        vals.append(_short(it.get("doc", "")))
        print(" | ".join(vals))

