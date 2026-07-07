"""Isolation des tests : jamais le cache disque LLM réel.

Sans ça, un test qui mocke le backend écrirait ses fausses réponses dans
``corpus/llm_cache/`` (servies ensuite en production), et lirait les vraies.
Les tests du cache disque lui-même réactivent LLM_CACHE_DISK sur un tmp_path.
"""

import pytest

from src import llm


@pytest.fixture(autouse=True)
def _cache_disque_isole(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "LLM_CACHE_DISK", False)
    monkeypatch.setattr(llm, "LLM_CACHE_DIR", tmp_path / "llm_cache")
    llm.clear_cache()
    yield
    llm.clear_cache()
