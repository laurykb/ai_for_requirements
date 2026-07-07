"""Audit global de la matrice de traçabilité.

Scanne TOUT le corpus (pas seulement une édition) pour produire un score de
fiabilité et la liste des points faibles : liens manquants, doublons d'ID,
cycles, dépassements de budget (déterministe), puis rédaction, pertinence,
couverture, redondance et pertinence aval par exigence (une passe LLM par exigence,
en parallèle), plus la cohérence des co-références trans-matrice.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from . import debate, embeddings, llm
from .config import ALLOCATION_TOLERANCE, EMBED_DUP_THRESHOLD, LATENT_TOPK, LLM_MAX_CONCURRENCY
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

    by_id = {r.get("id"): r for r in corpus}
    for r in corpus:
        pid = r.get("parent_id")
        # lien manquant
        if pid and pid not in id_set:
            findings.append(MatrixFinding(r["id"], "LIEN", "BLOQUANT",
                                          f"{r['id']} référence un parent inexistant ({pid})."))
        # intégrité des liens typés transverses
        for lk in (r.get("links") or []):
            tgt = lk.get("target") if isinstance(lk, dict) else getattr(lk, "target", None)
            ltype = lk.get("type") if isinstance(lk, dict) else getattr(lk, "type", "?")
            if tgt and tgt not in id_set:
                findings.append(MatrixFinding(r["id"], "LIEN", "BLOQUANT",
                                              f"Lien {ltype} de {r['id']} vers une cible inexistante ({tgt})."))
        # cycle
        cur, hops, broken = r.get("parent_id"), 0, False
        while cur and cur in by_id and hops <= len(corpus):
            if cur == r["id"]:
                broken = True
                break
            cur = by_id[cur].get("parent_id")
            hops += 1
        if broken:
            findings.append(MatrixFinding(r["id"], "CYCLE", "BLOQUANT",
                                          f"{r['id']} fait partie d'un cycle de traçabilité."))

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
    items = [(r["id"], r.get("texte", "")) for r in corpus if r.get("texte")]
    vlist = embeddings.get_embeddings([t for _, t in items])  # un seul appel (batch)
    if not vlist:
        return []
    vecs = {items[i][0]: vlist[i] for i in range(min(len(items), len(vlist)))}
    findings, seen = [], set()
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            id1, id2 = items[i][0], items[j][0]
            v1, v2 = vecs.get(id1), vecs.get(id2)
            if not v1 or not v2:
                continue
            s = embeddings.cosine(v1, v2)
            if s >= EMBED_DUP_THRESHOLD:
                key = (id1, id2)
                if key in seen:
                    continue
                seen.add(key)
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
        payload = {
            "exigence_cible": {"id": cible["id"], "niveau": cible.get("niveau"), "texte": cible["texte"]},
            "co_references": [{"id": r["id"], "niveau": r.get("niveau"), "texte": r["texte"],
                               "referents_partages": [tok]} for r in g[1:1 + LATENT_TOPK]],  # cap taille de groupe
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
                 on_event: Optional[Callable[[int, int], None]] = None) -> MatrixReport:
    """Audite tout le corpus. ``on_event(done, total)`` suit l'avancement sémantique."""
    findings: List[MatrixFinding] = list(_structural_findings(corpus))
    findings += _embedding_duplicates(corpus)  # doublons cross-corpus (déterministe)

    try:
        tree = RequirementTree(corpus)
    except ValueError:
        tree = None  # doublons : on s'arrête au structurel

    if deep and tree is not None and llm.llm_available():
        findings += _coreference_findings(corpus, tree)  # cohérence trans-matrice (borné)
        reqs = tree.all()
        total = len(reqs)
        done = 0
        with ThreadPoolExecutor(max_workers=LLM_MAX_CONCURRENCY) as pool:
            futures = [pool.submit(_audit_one, tree, r) for r in reqs]
            for fut in as_completed(futures):
                try:
                    findings += fut.result()
                except Exception as exc:
                    findings.append(MatrixFinding("?", "NON_AUDITE", "INFO",
                                                  f"Audit d'une exigence en erreur : {exc}"))
                done += 1
                if on_event:
                    try:
                        on_event(done, total)
                    except Exception:
                        pass

    penalty = sum(_PENALTY.get(f.severity, 0) for f in findings)
    score = max(0, 100 - penalty)
    counts: Dict[str, int] = {}
    for f in findings:
        counts[f.axis] = counts.get(f.axis, 0) + 1
    return MatrixReport(n=len(corpus), score=score, findings=findings, counts=counts)
