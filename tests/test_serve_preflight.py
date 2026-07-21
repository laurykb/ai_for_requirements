"""Pre-flight de serve.py : collect_missing est pure et testable hors-ligne."""
import os
from pathlib import Path

import pytest

from serve import check_setup, collect_missing


def _env(tmp_path, **extra):
    base = {"CROSS_ENCODER_LOCAL_PATH": str(tmp_path / "reranker")}
    base.update(extra)
    return base


def _reranker_ok(tmp_path):
    d = tmp_path / "reranker"
    d.mkdir()
    (d / "model.safetensors").touch()
    return d


def test_tout_present_rien_a_signaler(tmp_path):
    _reranker_ok(tmp_path)
    missing = collect_missing(
        tmp_path, _env(tmp_path, EMBED_MODEL="bge-m3:567m", GEN_MODEL="mistral-small3.2:latest"),
        spacy_ok=True, ollama_tags=["bge-m3:567m", "mistral-small3.2:latest"], node_ok=True,
    )
    assert missing == []


def test_spacy_absent(tmp_path):
    _reranker_ok(tmp_path)
    missing = collect_missing(tmp_path, _env(tmp_path), spacy_ok=False,
                              ollama_tags=None, node_ok=True)
    assert any("spaCy" in label for label, _ in missing)
    assert any("spacy download fr_core_news_sm" in cmd for _, cmd in missing)


def test_reranker_absent_ou_vide(tmp_path):
    # dossier inexistant
    missing = collect_missing(tmp_path, _env(tmp_path), spacy_ok=True,
                              ollama_tags=None, node_ok=True)
    assert any("reranker" in label for label, _ in missing)
    # dossier présent mais vide
    (tmp_path / "reranker").mkdir()
    missing = collect_missing(tmp_path, _env(tmp_path), spacy_ok=True,
                              ollama_tags=None, node_ok=True)
    assert any("huggingface-cli download" in cmd for _, cmd in missing)


def test_modele_ollama_manquant(tmp_path):
    _reranker_ok(tmp_path)
    missing = collect_missing(
        tmp_path, _env(tmp_path, EMBED_MODEL="bge-m3:567m", GEN_MODEL="mistral-small3.2:latest"),
        spacy_ok=True, ollama_tags=["bge-m3:567m"], node_ok=True,
    )
    assert [c for _, c in missing] == ["ollama pull mistral-small3.2:latest"]


def test_ollama_injoignable_pas_de_faux_positif(tmp_path):
    # ollama_tags=None (service down) : on ne signale PAS les modèles.
    _reranker_ok(tmp_path)
    missing = collect_missing(
        tmp_path, _env(tmp_path, EMBED_MODEL="bge-m3:567m", GEN_MODEL="mistral-small3.2:latest"),
        spacy_ok=True, ollama_tags=None, node_ok=True,
    )
    assert missing == []


def test_comparaison_modeles_ignore_le_tag(tmp_path):
    # EMBED_MODEL "bge-m3:567m" doit matcher le tag installé "bge-m3:latest".
    _reranker_ok(tmp_path)
    missing = collect_missing(
        tmp_path, _env(tmp_path, EMBED_MODEL="bge-m3:567m"),
        spacy_ok=True, ollama_tags=["bge-m3:latest"], node_ok=True,
    )
    assert missing == []


def test_node_absent(tmp_path):
    _reranker_ok(tmp_path)
    missing = collect_missing(tmp_path, _env(tmp_path), spacy_ok=True,
                              ollama_tags=None, node_ok=False)
    assert any("Node" in label for label, _ in missing)


def test_reranker_illisible_compte_comme_manquant(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("running as root; permissions ignored")
    d = tmp_path / "reranker"
    d.mkdir()
    (d / "model.safetensors").touch()
    try:
        os.chmod(d, 0o000)
        missing = collect_missing(tmp_path, _env(tmp_path), spacy_ok=True,
                                  ollama_tags=None, node_ok=True)
        assert any("reranker" in label for label, _ in missing)
    finally:
        os.chmod(d, 0o755)


def test_check_setup_ne_leve_jamais(tmp_path, monkeypatch):
    _reranker_ok(tmp_path)
    def raising_collect(*args, **kwargs):
        raise RuntimeError("simulated error")
    monkeypatch.setattr("serve.collect_missing", raising_collect)
    # Ne doit pas lever d'exception.
    assert check_setup() is None
