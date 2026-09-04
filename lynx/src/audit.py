"""Audit global de la matrice de traçabilité.

Scanne l'ensemble du corpus (pas seulement une édition) pour produire un score de
fiabilité et la liste des points faibles : liens manquants, doublons d'ID,
cycles, dépassements de budget (déterministe), puis rédaction, pertinence,
couverture, redondance et pertinence aval par exigence (une passe LLM par exigence,
en parallèle), plus la cohérence des co-références trans-matrice.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set

from . import debate, embeddings, llm
from .config import (ALLOCATION_TOLERANCE, AUDIT_BATCH_SIZE, EMBED_DUP_THRESHOLD,
                     LATENT_TOPK, LLM_MAX_CONCURRENCY)
from .extract import allocation_rollup, from_base
from .tree import RequirementTree

# Pénalités de score par gravité.
_PENALTY = {"BLOQUANT": 9, "WARNING": 3, "INFO": 0}


@dataclass
class MatrixFinding:
    req_id: str
    axis: str        # LIEN | DOUBLON | CYCLE | ALLOCATION | REDACTION | PERTINENCE | COUVERTURE | REDONDANCE | PERTINENCE_AVAL | COHERENCE_REF
    severity: str    # INFO | WARNING | BLOQUANT
    message: str
    # Débat contradictoire (BLOQUANT sémantiques uniquement) : {statut, plaidoyer, jugement}.
    debate: Optional[dict] = None


@dataclass
class MatrixReport:
    n: int
    score: int
    findings: List[MatrixFinding] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)
    requested_n: int = 0
    audited_n: int = 0
    coverage: float = 0.0
    mode: str = "deterministic"
    degraded_reasons: List[str] = field(default_factory=list)
    score_meaningful: bool = True

    @property
    def flagged_ids(self) -> List[str]:
        return sorted({f.req_id for f in self.findings if f.severity != "INFO"})

    @property
    def n_non_audite(self) -> int:
        return sum(1 for f in self.findings if f.axis == "NON_AUDITE")


# --------------------------------------------------------------------------
# Déterministe (instantané)
# --------------------------------------------------------------------------
def _structural_findings(corpus: List[dict]) -> List[MatrixFinding]:
    findings: List[MatrixFinding] = []
    ids = [r.get("id") for r in corpus]
    id_set = set(ids)

    # doublons d'ID
    seen: set = set()
    for rid in ids:
        if rid in seen:
            findings.append(MatrixFinding(rid, "DOUBLON", "BLOQUANT", f"Identifiant dupliqué : {rid}."))
        seen.add(rid)

    graph: Dict[str, Set[str]] = {rid: set() for rid in id_set if rid}
    for r in corpus:
        rid = r.get("id")  # corpus brut (via API) : le champ id peut manquer
        pid = r.get("parent_id")
        # lien manquant
        if pid and pid not in id_set:
            findings.append(MatrixFinding(rid, "LIEN", "BLOQUANT",
                                          f"{rid} référence un parent inexistant ({pid})."))
        elif rid and pid:
            graph[rid].add(pid)
        # intégrité des liens typés transverses
        for lk in (r.get("links") or []):
            tgt = lk.get("target") if isinstance(lk, dict) else getattr(lk, "target", None)
            raw_type = lk.get("type") if isinstance(lk, dict) else getattr(lk, "type", "?")
            ltype = getattr(raw_type, "value", raw_type)
            if tgt and tgt not in id_set:
                findings.append(MatrixFinding(rid, "LIEN", "BLOQUANT",
                                              f"Lien {ltype} de {rid} vers une cible inexistante ({tgt})."))
            elif rid and tgt and ltype in {"DERIVE", "REFINES", "SATISFIES"}:
                graph[rid].add(tgt)

    # Tarjan : un seul constat par composante cyclique, liens typés inclus.
    index = 0
    indices: Dict[str, int] = {}
    lowlinks: Dict[str, int] = {}
    stack: List[str] = []
    on_stack: Set[str] = set()
    cycles: List[List[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in graph[node]:
            if target not in graph:
                continue
            if target not in indices:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])
        if lowlinks[node] == indices[node]:
            component: List[str] = []
            while True:
                target = stack.pop()
                on_stack.remove(target)
                component.append(target)
                if target == node:
                    break
            if len(component) > 1 or node in graph[node]:
                cycles.append(sorted(component))

    for node in sorted(graph):
        if node not in indices:
            visit(node)
    for component in cycles:
        findings.append(MatrixFinding(component[0], "CYCLE", "BLOQUANT",
                                      "Cycle de traçabilité : " + " → ".join(component) + "."))

    # allocation : dépassement de budget par parent (conversion + tolérance)
    tree = RequirementTree(corpus) if len(id_set) == len(ids) else None
    if tree:
        for parent in tree.all():
            roll = allocation_rollup(parent.texte, [(c.id, c.texte) for c in tree.children(parent.id)])
            if not roll or not roll["contributions"]:
                continue
            limit = roll["budget_base"] * (1.0 + ALLOCATION_TOLERANCE)
            if roll["total_base"] > limit + 1e-9:
                budget = roll["budget"]
                total = round(from_base(roll["total_base"], budget.unit), 3)
                findings.append(MatrixFinding(
                    parent.id, "ALLOCATION", "BLOQUANT",
                    f"Dépassement : enfants = {total} {budget.unit} > plafond "
                    f"{budget.value} {budget.unit} sur {parent.id}."))
    return findings


# --------------------------------------------------------------------------
# Sémantique (une passe LLM par exigence)
# --------------------------------------------------------------------------
def _audit_one(tree: RequirementTree, req) -> List[MatrixFinding]:
    parent = tree.get(req.parent_id) if req.parent_id else None
    payload = {
        "exigence": req.short(),
        "parent": parent.short() if parent else None,
        "soeurs": [s.short() for s in tree.siblings(req.id)],
        "filles": [c.short() for c in tree.children(req.id)],
    }
    resp = llm.call_skill("audit_exigence", payload)
    if resp.get("error"):
        # On ne fait pas passer une exigence non auditée pour saine.
        return [MatrixFinding(req.id, "NON_AUDITE", "INFO",
                              f"Non audité (LLM indisponible : {resp.get('error')}).")]
    out: List[MatrixFinding] = []
    sev = str(resp.get("gravite", "WARNING")).upper()
    if sev not in _PENALTY:
        sev = "WARNING"
    red = resp.get("redaction") or {}
    if red.get("conforme") is False:
        out.append(MatrixFinding(req.id, "REDACTION", "WARNING",
                                 f"Rédaction : {red.get('probleme') or 'non conforme'}"))
    per = resp.get("pertinence") or {}
    if per.get("coherent") is False:
        out.append(debate.contest(
            MatrixFinding(req.id, "PERTINENCE", "BLOQUANT",
                          f"Pertinence : {per.get('probleme') or 'incohérence avec le parent'}"),
            tree, req.id))
    cov = resp.get("couverture") or {}
    if cov.get("complet") is False:
        manques = ", ".join(map(str, cov.get("manques") or [])) or "concepts manquants"
        out.append(MatrixFinding(req.id, "COUVERTURE", "WARNING",
                                 f"Couverture incomplète : {manques}"))
    rdd = resp.get("redondance") or {}
    if rdd.get("redondant") is True:
        avec = ", ".join(map(str, rdd.get("avec") or [])) or "une sœur"
        out.append(debate.contest(
            MatrixFinding(req.id, "REDONDANCE", "BLOQUANT", f"Redondante avec {avec}."),
            tree, req.id))
    pav = resp.get("pertinence_aval") or {}
    if pav.get("coherent") is False:
        avec = ", ".join(map(str, pav.get("avec") or [])) or "une fille"
        out.append(debate.contest(
            MatrixFinding(req.id, "PERTINENCE_AVAL", "BLOQUANT",
                          f"Pertinence aval : {pav.get('probleme') or f'incohérence avec {avec}'}"),
            tree, req.id))
    return out


def _embedding_duplicates(corpus: List[dict]) -> List[MatrixFinding]:
    """Détecte les doublons quasi-identiques dans toute la matrice (embeddings)."""
    if not embeddings.embeddings_available():
        return []
    items = [(r.get("id"), r.get("texte", "")) for r in corpus if r.get("texte")]
    vlist = embeddings.get_embeddings([t for _, t in items])  # un seul appel (batch)
    if not vlist:
        return []
    findings: List[MatrixFinding] = []
    for i, j, s in embeddings.duplicate_pairs(vlist, EMBED_DUP_THRESHOLD):
        id1, id2 = items[i][0], items[j][0]
        findings.append(MatrixFinding(id1, "REDONDANCE", "BLOQUANT",
            f"Doublon probable : {id1} ≈ {id2} (similarité {s:.2f})."))
    return findings


def _coreference_findings(corpus: List[dict],
                          tree: Optional[RequirementTree] = None) -> List[MatrixFinding]:
    """Cohérence trans-matrice : exigences partageant un référent concret (acronyme,
    code, interface) qui se contredisent. Borné : groupes les plus partagés d'abord.
    """
    from collections import defaultdict
    from .analyzers import _referents

    reqs = [r for r in corpus if r.get("texte") and r.get("id")]
    by_ref: Dict[str, list] = defaultdict(list)
    for r in reqs:
        toks, units = _referents(r["texte"])
        for ref in (toks | units):
            by_ref[ref].append(r)
    groups = sorted(((ref, g) for ref, g in by_ref.items() if len(g) >= 2),
                    key=lambda x: len(x[1]), reverse=True)
    out: List[MatrixFinding] = []
    seen_pairs: set = set()
    for tok, g in groups[:2 * LATENT_TOPK]:  # cap dur du nombre d'appels LLM
        cible = g[0]
        # Chaîne verticale exclue : déjà couverte par PERTINENCE/PERTINENCE_AVAL
        # (sinon le même défaut est signalé deux fois, sous deux axes).
        vertical = set()
        if tree is not None and cible["id"] in tree:
            vertical = ({a.id for a in tree.ancestors(cible["id"])}
                        | {d.id for d in tree.descendants(cible["id"])})
        autres = [r for r in g[1:] if r["id"] not in vertical]
        if not autres:
            continue
        payload = {
            "exigence_cible": {"id": cible["id"], "niveau": cible.get("niveau"), "texte": cible["texte"]},
            "co_references": [{"id": r["id"], "niveau": r.get("niveau"), "texte": r["texte"],
                               "referents_partages": [tok]} for r in autres[:LATENT_TOPK]],  # cap taille de groupe
        }
        resp = llm.call_skill("coherence_coreference", payload)
        if resp.get("error") or resp.get("coherent", True):
            continue
        for c in (resp.get("conflits") or []):
            cid = c.get("id") if isinstance(c, dict) else c
            probleme = c.get("probleme") if isinstance(c, dict) else "incohérence"
            key = tuple(sorted((str(cible["id"]), str(cid))))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            out.append(debate.contest(
                MatrixFinding(cible["id"], "COHERENCE_REF", "BLOQUANT",
                              f"Co-référence « {tok} » : {probleme} (avec {cid})."),
                tree, cible["id"]))
    return out


def audit_matrix(corpus: List[dict], deep: bool = True,
                 on_event: Optional[Callable[[int, int], None]] = None,
                 scope: Optional[Set[str]] = None) -> MatrixReport:
    """Audite le corpus et distingue résultat, couverture et dégradation."""
    def _in_scope(f: MatrixFinding) -> bool:
        return scope is None or f.req_id in scope

    requested_ids = [r.get("id") for r in corpus
                     if r.get("id") and (scope is None or r.get("id") in scope)]
    findings: List[MatrixFinding] = [f for f in _structural_findings(corpus) if _in_scope(f)]
    findings += [f for f in _embedding_duplicates(corpus) if _in_scope(f)]
    degraded_reasons: List[str] = []
    audited_ids: Set[str] = set()

    try:
        tree = RequirementTree(corpus)
    except ValueError:
        tree = None

    if deep:
        if tree is None:
            degraded_reasons.append("Graphe invalide : audit sémantique impossible.")
        elif not llm.llm_available():
            degraded_reasons.append("LLM indisponible : audit déterministe uniquement.")
        else:
            findings += [f for f in _coreference_findings(corpus, tree) if _in_scope(f)]
            reqs = [r for r in tree.all() if scope is None or r.id in scope]
            total = len(reqs)
            done = 0
            with ThreadPoolExecutor(max_workers=LLM_MAX_CONCURRENCY) as pool:
                for start in range(0, total, max(1, AUDIT_BATCH_SIZE)):
                    batch = reqs[start:start + max(1, AUDIT_BATCH_SIZE)]
                    futures = {pool.submit(_audit_one, tree, req): req.id for req in batch}
                    for fut in as_completed(futures):
                        req_id = futures[fut]
                        try:
                            result = fut.result()
                        except Exception as exc:
                            result = [MatrixFinding(req_id, "NON_AUDITE", "INFO",
                                                    f"Audit en erreur : {exc}")]
                        findings += result
                        if not any(f.axis == "NON_AUDITE" for f in result):
                            audited_ids.add(req_id)
                        done += 1
                        if on_event:
                            try:
                                on_event(done, total)
                            except Exception:
                                pass

        missing = set(requested_ids) - audited_ids
        existing = {f.req_id for f in findings if f.axis == "NON_AUDITE"}
        reason = degraded_reasons[0] if degraded_reasons else "Audit sémantique non abouti."
        findings += [MatrixFinding(rid, "NON_AUDITE", "INFO", reason)
                     for rid in sorted(missing - existing)]

    penalty = sum(_PENALTY.get(f.severity, 0) for f in findings)
    evaluated_n = max(1, len(requested_ids))
    score = max(0, round(100 * (1 - penalty / (evaluated_n * _PENALTY["BLOQUANT"]))))
    counts: Dict[str, int] = {}
    for f in findings:
        counts[f.axis] = counts.get(f.axis, 0) + 1
    requested_n = len(requested_ids)
    audited_n = len(audited_ids) if deep else 0
    coverage = round(100.0 * audited_n / requested_n, 1) if requested_n else 100.0
    mode = "deterministic" if not deep else ("full" if coverage == 100.0 else "degraded")
    return MatrixReport(
        n=len(corpus), score=score, findings=findings, counts=counts,
        requested_n=requested_n, audited_n=audited_n, coverage=coverage, mode=mode,
        degraded_reasons=degraded_reasons,
        score_meaningful=not deep or coverage > 0.0,
    )
