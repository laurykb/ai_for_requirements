"""Contrats de réponse et dossier de preuves, sans appel LLM.

Le mode d'exécution (RAG, Agent, Synthèse) décide comment trouver les preuves.
Ce module décide indépendamment comment présenter la réponse, puis contrôle les
propriétés observables de la sortie. Il ne prétend pas juger la vérité
sémantique : celle-ci reste du ressort d'une vérification explicite.
"""
from __future__ import annotations

import re
import unicodedata


def _norm(text: str) -> str:
    value = unicodedata.normalize("NFD", (text or "").casefold())
    return "".join(char for char in value if unicodedata.category(char) != "Mn")


_RULES = (
    ("comparison", re.compile(r"\b(compar|difference|differen|versus|\bvs\b|avantage.*inconvenient)")),
    ("procedure", re.compile(r"\b(comment faire|procedure|etapes?|mettre en oeuvre|mise en oeuvre|configur|deployer)")),
    ("enumeration", re.compile(r"\b(liste|lister|recens|enumer|quels? sont|toutes? les|exhaustif)")),
    ("explanation", re.compile(r"\b(explique|expliquer|pourquoi|definition|que signifie|en quoi)")),
    ("analysis", re.compile(r"\b(analyse|synthese|vue d ensemble|impacts?|risques?|menaces?|objectifs?)")),
)


_CONTRACTS = {
    "factual": {
        "label": "Réponse factuelle",
        "sections": ["Réponse", "Limites"],
        "instructions": "Réponds d'abord directement en une à trois phrases, puis indique uniquement les limites utiles.",
    },
    "explanation": {
        "label": "Explication technique",
        "sections": ["Réponse courte", "Explication", "Limites"],
        "instructions": "Donne d'abord la conclusion, puis explique le mécanisme, la portée et les conditions sans extrapoler.",
    },
    "enumeration": {
        "label": "Liste structurée",
        "sections": ["Résultat", "Éléments non couverts"],
        "instructions": "Produis une liste numérotée, un élément atomique par ligne, en conservant identifiants, valeurs et exceptions.",
    },
    "comparison": {
        "label": "Comparaison",
        "sections": ["Conclusion", "Comparaison", "Différences importantes", "Limites"],
        "instructions": "Commence par la conclusion puis utilise un tableau Markdown. Compare selon des critères homogènes et rends les absences de preuve explicites.",
    },
    "procedure": {
        "label": "Procédure",
        "sections": ["Objectif", "Étapes", "Prérequis et limites"],
        "instructions": "Présente des étapes numérotées dans l'ordre. N'invente aucun prérequis ni aucune action absente des preuves.",
    },
    "analysis": {
        "label": "Analyse structurée",
        "sections": ["Synthèse exécutive", "Analyse", "Points de vigilance", "Informations manquantes"],
        "instructions": "Hiérarchise les constats par thème, distingue faits, contradictions et informations absentes, puis termine par les points de vigilance.",
    },
}


def classify_answer_contract(question: str) -> dict:
    normalized = _norm(question)
    kind = next((name for name, rule in _RULES if rule.search(normalized)), "factual")
    contract = _CONTRACTS[kind]
    return {"id": kind, **contract}


def contract_prompt(question: str, citation_style: str = "numeric") -> str:
    contract = classify_answer_contract(question)
    headings = "\n".join(f"## {section}" for section in contract["sections"])
    citations = (
        "Chaque affirmation factuelle doit porter un marqueur numérique [n] valide."
        if citation_style == "numeric"
        else "Chaque constat doit conserver au moins une étiquette [src: document] fournie."
    )
    return (
        "[CONTRAT DE RÉPONSE — PRIORITAIRE POUR LA FORME]\n"
        f"Type : {contract['label']}\n{contract['instructions']}\n"
        "Utilise exactement ces sections, sans afficher les présentes consignes :\n"
        f"{headings}\n{citations}\n"
        "Si une section ne peut pas être étayée, écris-le explicitement au lieu de la supprimer."
    )


def build_evidence_dossier(question: str, chunks: list[dict], mode: str,
                           plan: list[dict] | None = None) -> dict:
    contract = classify_answer_contract(question)
    evidence = []
    for index, chunk in enumerate(chunks or [], 1):
        meta = chunk.get("meta") or {}
        text = str(chunk.get("doc") or "").strip()
        evidence.append({
            "id": index,
            "source": meta.get("source") or "inconnu",
            "page": meta.get("page_number"),
            "excerpt": text[:500],
        })
    return {
        "mode": mode,
        "contract": contract,
        "points_to_cover": [step.get("sous_question", "") for step in (plan or []) if step.get("sous_question")]
                           or list(contract["sections"]),
        "evidence": evidence,
        "evidence_count": len(evidence),
    }


def validate_answer(answer: str, dossier: dict, citation_style: str = "numeric", enforce_structure: bool = True) -> dict:
    contract = dossier["contract"]
    lower = _norm(answer)
    present = [section for section in contract["sections"] if _norm(section) in lower]
    missing = [section for section in contract["sections"] if section not in present]
    if citation_style == "numeric":
        raw_refs = [int(value) for value in re.findall(r"\[(\d+)\]", answer)]
        valid_refs = [value for value in raw_refs if 1 <= value <= dossier["evidence_count"]]
        invalid_refs = sorted(set(raw_refs) - set(valid_refs))
    else:
        raw_refs = re.findall(r"\[src:\s*([^\]]+)\]", answer, flags=re.IGNORECASE)
        valid_refs = raw_refs
        invalid_refs = []
    paragraphs = [part.strip() for part in re.split(r"\n+|(?<=[.!?])\s+", answer)
                  if part.strip() and not part.lstrip().startswith("#")]
    cited = sum(bool(re.search(r"\[(?:\d+|src:)", part, flags=re.IGNORECASE)) for part in paragraphs)
    citation_coverage = cited / len(paragraphs) if paragraphs else 0.0
    return {
        "contract_id": contract["id"],
        "contract_label": contract["label"],
        "required_sections": contract["sections"],
        "present_sections": present,
        "missing_sections": missing,
        "structure_complete": not missing,
        "enforcement": "enforced" if enforce_structure else "shadow",
        "citation_count": len(valid_refs),
        "invalid_citations": invalid_refs,
        "citation_coverage": round(citation_coverage, 3),
        "evidence_count": dossier["evidence_count"],
        "completion": "complete" if (not missing or not enforce_structure) and valid_refs and not invalid_refs else "partial",
    }
