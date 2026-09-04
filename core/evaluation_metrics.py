"""Métriques RAG déterministes, sans LLM ni stockage externe."""
from __future__ import annotations

import re
from typing import Optional


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().strip())


def tokens(text: str) -> set[str]:
    return set(re.findall(r"\w+", normalize(text), flags=re.UNICODE))


def exact_match(generated: str, reference: str) -> float:
    return float(normalize(reference) in normalize(generated))


def f1_token(generated: str, reference: str) -> float:
    generated_tokens = tokens(generated)
    reference_tokens = tokens(reference)
    if not reference_tokens or not generated_tokens:
        return 0.0
    common = generated_tokens & reference_tokens
    precision = len(common) / len(generated_tokens)
    recall = len(common) / len(reference_tokens)
    return 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)


def context_recall_lexical(chunks: list, reference: str) -> float:
    if not reference or not chunks:
        return 0.0
    reference_tokens = tokens(reference)
    covered: set[str] = set()
    for chunk in chunks:
        covered |= tokens(chunk.get("doc", ""))
    return len(reference_tokens & covered) / len(reference_tokens) if reference_tokens else 0.0


def context_precision_lexical(chunks: list, reference: str, topk: int = 5) -> float:
    if not reference or not chunks:
        return 0.0
    relevant = sum(
        f1_token(chunk.get("doc", ""), reference) > 0.1
        for chunk in chunks[:topk]
    )
    return relevant / min(topk, len(chunks))


def structured_axis_coverage(generated: str, expected_axes: list) -> Optional[float]:
    """Part des axes métier dont le minimum de mots-clés apparaît."""
    if not expected_axes:
        return None
    content = normalize(generated)
    covered = 0
    for axis in expected_axes:
        keywords = [str(value) for value in axis.get("keywords", []) if str(value).strip()]
        minimum = int(axis.get("min_hits", len(keywords) or 1))
        if sum(normalize(keyword) in content for keyword in keywords) >= minimum:
            covered += 1
    return covered / len(expected_axes)


def keyword_hit_rate(chunks: list, expected_keywords: list, topk: int = 10) -> Optional[float]:
    """Fraction des mots-clés attendus présents dans les passages récupérés."""
    if not expected_keywords:
        return None
    if not chunks:
        return 0.0
    content = normalize(" \n ".join(chunk.get("doc", "") for chunk in chunks[:topk]))
    return sum(normalize(keyword) in content for keyword in expected_keywords) / len(expected_keywords)
