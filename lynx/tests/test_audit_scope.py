"""Parité golden : audit scopé == audit complet filtré au scope. Sans Ollama :
on mocke _audit_one (sémantique) et les embeddings, on teste la mécanique de scope."""
from src import audit
from src.audit import MatrixFinding


def _fake_audit_one(tree, req):
    # Un constat déterministe par exigence (indépendant des autres) : suffit à
    # tester le filtrage de la boucle sémantique.
    return [MatrixFinding(req.id, "REDACTION", "WARNING", f"pseudo-constat {req.id}")]


CORPUS = [
    {"id": "R0", "niveau": 0, "texte": "Le système doit voler.", "parent_id": None},
    {"id": "R1", "niveau": 1, "texte": "Le système doit décoller en 5 s.", "parent_id": "R0"},
    {"id": "R2", "niveau": 1, "texte": "Le système doit atterrir en 8 s.", "parent_id": "R0"},
]


def test_scope_none_matches_legacy(monkeypatch):
    monkeypatch.setattr(audit, "_audit_one", _fake_audit_one)
    monkeypatch.setattr(audit.embeddings, "embeddings_available", lambda: False)
    monkeypatch.setattr(audit.llm, "llm_available", lambda: True)
    monkeypatch.setattr(audit, "_coreference_findings", lambda corpus, tree: [])
    rep = audit.audit_matrix(CORPUS, deep=True)
    ids = sorted(f.req_id for f in rep.findings)
    assert ids == ["R0", "R1", "R2"]  # un pseudo-constat par exigence


def _key(f):
    return (f.req_id, f.axis, f.severity, f.message)


def test_scoped_equals_full_filtered(monkeypatch):
    monkeypatch.setattr(audit, "_audit_one", _fake_audit_one)
    monkeypatch.setattr(audit.embeddings, "embeddings_available", lambda: False)
    monkeypatch.setattr(audit.llm, "llm_available", lambda: True)
    monkeypatch.setattr(audit, "_coreference_findings", lambda corpus, tree: [])

    full = audit.audit_matrix(CORPUS, deep=True)
    scope = {"R1"}
    scoped = audit.audit_matrix(CORPUS, deep=True, scope=scope)

    full_in_scope = sorted((_key(f) for f in full.findings if f.req_id in scope))
    scoped_keys = sorted(_key(f) for f in scoped.findings)
    assert scoped_keys == full_in_scope
    # La boucle LLM n'a produit un constat QUE pour R1.
    assert all(f.req_id == "R1" for f in scoped.findings)


def test_scoped_semantic_loop_only_visits_scope(monkeypatch):
    visited = []
    def spy(tree, req):
        visited.append(req.id)
        return []
    monkeypatch.setattr(audit, "_audit_one", spy)
    monkeypatch.setattr(audit.embeddings, "embeddings_available", lambda: False)
    monkeypatch.setattr(audit.llm, "llm_available", lambda: True)
    monkeypatch.setattr(audit, "_coreference_findings", lambda corpus, tree: [])
    audit.audit_matrix(CORPUS, deep=True, scope={"R2"})
    assert visited == ["R2"]  # aucune exigence hors scope n'a été auditée par LLM
