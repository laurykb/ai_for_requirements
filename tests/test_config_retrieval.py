"""Vérifie que les nouveaux réglages de retrieval (élastiques) sont exportés et typés."""
import env_config as ec


def test_new_retrieval_knobs_exist():
    # Pool de candidats ÉLASTIQUE : bornes + incrément par document.
    assert isinstance(ec.CANDIDATE_POOL_MIN, int) and ec.CANDIDATE_POOL_MIN >= ec.NUM_CHUNKS
    assert isinstance(ec.CANDIDATE_POOL_MAX, int) and ec.CANDIDATE_POOL_MAX >= ec.CANDIDATE_POOL_MIN
    assert isinstance(ec.CANDIDATE_POOL_PER_DOC, int) and ec.CANDIDATE_POOL_PER_DOC >= 0
    assert isinstance(ec.PER_DOC_FLOOR, int) and ec.PER_DOC_FLOOR >= 1
    assert isinstance(ec.MAX_CHUNKS, int) and ec.MAX_CHUNKS >= ec.NUM_CHUNKS


def test_rebalanced_defaults():
    # Poids rééquilibrés (départ 0.5/0.5) et somme cohérente.
    assert abs((ec.WEIGHT_SEMANTIC + ec.WEIGHT_BM25) - 1.0) < 1e-6
    assert ec.WEIGHT_SEMANTIC >= 0.4
    # Seuil calibré juste au-dessus du plancher de bruit du CE (~0.50) pour que le
    # hors-sujet s'abstienne (les questions exploratoires contournent ce seuil via
    # retrieval.intent.is_exploratory, donc elles ne sont pas affectées).
    assert ec.CE_RELEVANCE_THRESHOLD <= 0.505


def test_vague1_flags_exist_and_default_off():
    import env_config as C
    assert C.HYPE_ENABLED is False
    assert C.CONTEXT_HEADERS_ENABLED is False
    assert isinstance(C.HYPE_MAX_QUESTIONS, int) and C.HYPE_MAX_QUESTIONS >= 1
