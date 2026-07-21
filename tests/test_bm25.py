"""Tests unitaires BM25 - focalisés sur le multi-document.

L'index « tous documents » est un BM25 global construit sur les chunks de
plusieurs sources (fusion des index par-document stockés dans MongoDB). Ces
tests vérifient que la recherche et surtout le *filtrage par source* restent
corrects sur cet index global - le cas qui désalignait auparavant les
positions (ids tronqués vs scores du corpus complet)."""
from dataclasses import dataclass

from indexing.keyword_index import build_bm25_index, bm25_search
from retrieval.keyword_bm25 import run_bm25_for_query


@dataclass
class _Doc:
    page_content: str
    metadata: dict


def _make_corpus():
    """Corpus multi-document : 2 chunks 'chiffrement' (A) + 2 chunks 'parefeu' (B).

    Un 5e chunk 'filler' (source C) casse la symetrie 50/50 : sans lui, un terme
    present dans exactement la moitie du corpus a une IDF nulle sous BM25Okapi
    (tous les scores a 0, ordre arbitraire) - artefact du mini-corpus, pas du code.
    On evite aussi les traits d'union : `_tokenize` (\\w+) couperait 'pare-feu'."""
    docs = [
        _Doc("Le chiffrement protege la confidentialite des donnees",
             {"id": "a1", "source": "A.md"}),
        _Doc("Algorithme de chiffrement symetrique et cles",
             {"id": "a2", "source": "A.md"}),
        _Doc("Le parefeu filtre le trafic reseau entrant",
             {"id": "b1", "source": "B.md"}),
        _Doc("Regles du parefeu et politique reseau",
             {"id": "b2", "source": "B.md"}),
        _Doc("Procedure de sauvegarde et journal des operations",
             {"id": "c1", "source": "C.md"}),
    ]
    return docs, build_bm25_index(docs)


def test_global_index_finds_each_document():
    """Sur l'index fusionne, chaque requete retrouve le bon document."""
    _, bm25_tuple = _make_corpus()

    ids_chiff, _ = run_bm25_for_query(bm25_tuple, "chiffrement", topn=2)
    assert set(ids_chiff) == {"a1", "a2"}

    ids_pf, _ = run_bm25_for_query(bm25_tuple, "parefeu reseau", topn=2)
    assert set(ids_pf) == {"b1", "b2"}


def test_source_filter_on_global_index():
    """Le filtrage par source sur l'index GLOBAL ne renvoie que ce document.

    Auparavant, run_bm25_for_query decoupait `ids` et reutilisait le BM25 global :
    les positions issues du corpus complet indexaient une liste tronquee
    (IndexError / mauvais mapping). Ce test garde le regression fixe."""
    _, bm25_tuple = _make_corpus()

    # Requete 'reseau' (cote B) mais filtree sur A : aucun resultat B ne doit fuir.
    ids, lookup = run_bm25_for_query(bm25_tuple, "reseau parefeu", topn=4, source_filter="A.md")
    assert all(lookup[i]["meta"]["source"] == "A.md" for i in ids)
    assert not any(i in ("b1", "b2") for i in ids)

    # Requete 'parefeu' filtree sur B : on recupere bien les chunks B.
    ids_b, _ = run_bm25_for_query(bm25_tuple, "parefeu", topn=4, source_filter="B.md")
    assert set(ids_b) == {"b1", "b2"}


def test_source_filter_list_multi_document():
    """source_filter accepte une LISTE -> recherche multi-document ciblee."""
    _, bm25_tuple = _make_corpus()

    # Filtre sur A + C : aucun chunk B ne doit apparaitre.
    ids, lookup = run_bm25_for_query(bm25_tuple, "chiffrement journal",
                                     topn=10, source_filter=["A.md", "C.md"])
    assert ids  # au moins un resultat
    assert all(lookup[i]["meta"]["source"] in ("A.md", "C.md") for i in ids)
    assert not any(i in ("b1", "b2") for i in ids)

    # Filtre sur A + B : 'chiffrement' (A) et 'parefeu' (B) tous deux atteignables.
    ids_ab, _ = run_bm25_for_query(bm25_tuple, "chiffrement parefeu",
                                   topn=10, source_filter=["A.md", "B.md"])
    assert ids_ab
    assert not any(i == "c1" for i in ids_ab)  # C exclu


def test_source_filter_unknown_source_returns_empty():
    _, bm25_tuple = _make_corpus()
    ids, lookup = run_bm25_for_query(bm25_tuple, "chiffrement", topn=4, source_filter="INCONNU.md")
    assert ids == []
    assert lookup == {}


def test_empty_query_returns_empty():
    _, bm25_tuple = _make_corpus()
    ids, lookup = run_bm25_for_query(bm25_tuple, "   ", topn=4)
    assert ids == []
    assert lookup == {}


def test_bm25_search_source_filter_positions_aligned():
    """bm25_search applique le filtre sans desaligner ids/scores."""
    docs, (bm25, ids, texts, metas) = _make_corpus()
    res = bm25_search(bm25, ids, texts, metas, "chiffrement parefeu", topn=10, source_filter="B.md")
    returned_ids = res["ids"][0]
    returned_metas = res["metadatas"][0]
    assert returned_ids  # au moins un resultat
    assert all(m["source"] == "B.md" for m in returned_metas)
    # Chaque id renvoye correspond bien a sa metadata (alignement preserve).
    assert all(rid == rm["id"] for rid, rm in zip(returned_ids, returned_metas))
