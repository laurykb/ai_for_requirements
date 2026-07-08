"""Isolation des tests : jamais le cache disque LLM réel.

Sans ça, un test qui mocke le backend écrirait ses fausses réponses dans
``corpus/llm_cache/`` (servies ensuite en production), et lirait les vraies.
Les tests du cache disque lui-même réactivent LLM_CACHE_DISK sur un tmp_path.
"""

import pytest

from src import llm
from src import embed_store


@pytest.fixture(autouse=True)
def _cache_disque_isole(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "LLM_CACHE_DISK", False)
    monkeypatch.setattr(llm, "LLM_CACHE_DIR", tmp_path / "llm_cache")
    llm.clear_cache()
    yield
    llm.clear_cache()


@pytest.fixture(autouse=True)
def _embed_store_isole(monkeypatch, tmp_path):
    """Aucun test n'écrit dans le vrai corpus/lynx_store.sqlite."""
    monkeypatch.setattr(embed_store, "EMBED_CACHE_DB", tmp_path / "lynx_store.sqlite")
    embed_store.reset()
    yield
    embed_store.reset()
