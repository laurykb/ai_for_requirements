"""Tests des sorties structurées (schémas Pydantic des skills, httpx mocké).

Couvre le contrat de ``llm._chat`` avec schéma : réponse valide, réponse
invalide corrigée au retry, double échec -> SCHEMA_VALIDATION_ERROR, repli
json_schema -> json_object, cache jamais pollué et vote (sample_skill) qui
écarte les tirages invalides.
"""

import copy
import json

import httpx
import pytest

from src import llm
from src.schemas import SKILL_SCHEMAS, schema_for
from src.config import SKILLS_DIR


# --- outillage : faux backend OpenAI-compatible ----------------------------
class _Resp:
    """Réponse httpx minimale (succès JSON ou erreur HTTP)."""

    def __init__(self, payload=None, status=200):
        self.status_code = status
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("POST", "http://test")
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=req,
                response=httpx.Response(self.status_code, request=req))

    def json(self):
        return self._payload


def _completion(content: dict) -> dict:
    """Corps /chat/completions renvoyant ``content`` comme message JSON."""
    return {"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


class _FakeBackend:
    """File de réponses + journal des requêtes envoyées (body JSON)."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.bodies = []

    def post(self, url, headers=None, json=None, timeout=None):
        # copie : le repli json_schema -> json_object mute le body côté client
        self.bodies.append(copy.deepcopy(json))
        return self.replies.pop(0)


@pytest.fixture(autouse=True)
def _llm_isole(monkeypatch):
    """LLM activé, cache propre, sonde json_schema réinitialisée."""
    monkeypatch.setattr(llm, "LLM_DISABLED", False)
    monkeypatch.setattr(llm, "LLM_CACHE", True)
    monkeypatch.setattr(llm, "_json_schema_supported", None)
    llm.clear_cache()
    yield
    llm.clear_cache()


_VALIDE = {"est_coherent": False, "rupture_avec": ["P"], "niveau_gravite": "BLOCKING",
           "preuve": "30 minutes", "synthese": "La cible contredit son parent."}
_INVALIDE = {"est_coherent": "peut-être", "rupture_avec": "P"}  # bool et liste attendus
_PAYLOAD = {"exigence_cible": {"id": "C"}, "chaine_amont": [{"id": "P"}]}


# --- registre ---------------------------------------------------------------
def test_registre_couvre_les_skills_json():
    # Chaque entrée du registre correspond à un prompt de skill existant.
    for name in SKILL_SCHEMAS:
        assert (SKILLS_DIR / f"{name}.md").exists(), f"prompt manquant : {name}"
    # Les deux exclusions volontaires : sortie streamée / référentiel injecté.
    assert schema_for("synthese_message") is None
    assert schema_for("regles_redaction") is None
    assert schema_for(None) is None


# --- réponse valide ---------------------------------------------------------
def test_reponse_valide_validee_et_cachee(monkeypatch):
    backend = _FakeBackend([_Resp(_completion(_VALIDE))])
    monkeypatch.setattr(llm.httpx, "post", backend.post)
    llm.start_trace()
    out = llm.call_skill("coherence_pertinence", _PAYLOAD)
    assert out["est_coherent"] is False and out["rupture_avec"] == ["P"]
    # la génération a bien été contrainte par le schéma JSON du skill
    assert backend.bodies[0]["response_format"]["type"] == "json_schema"
    # boîte de verre : l'état de validation est tracé
    recs = llm.stop_trace()
    assert recs[0]["validation"] == "valide" and recs[0]["ok"] is True
    # deuxième appel identique : servi par le cache, aucun POST de plus
    out2 = llm.call_skill("coherence_pertinence", _PAYLOAD)
    assert out2 == out and len(backend.bodies) == 1


# --- réponse invalide corrigée au retry -------------------------------------
def test_reponse_invalide_corrigee_au_retry(monkeypatch):
    backend = _FakeBackend([_Resp(_completion(_INVALIDE)), _Resp(_completion(_VALIDE))])
    monkeypatch.setattr(llm.httpx, "post", backend.post)
    llm.start_trace()
    out = llm.call_skill("coherence_pertinence", _PAYLOAD)
    assert out["est_coherent"] is False and not out.get("error")
    assert len(backend.bodies) == 2
    # le retry réinjecte la réponse fautive + les erreurs de validation
    retry_msgs = backend.bodies[1]["messages"]
    assert retry_msgs[-2]["role"] == "assistant"
    assert "peut-être" in retry_msgs[-2]["content"]
    assert retry_msgs[-1]["role"] == "user"
    assert "Erreurs de validation" in retry_msgs[-1]["content"]
    assert "est_coherent" in retry_msgs[-1]["content"]
    recs = llm.stop_trace()
    assert recs[0]["validation"] == "valide_apres_retry" and recs[0]["retries"] == 1


# --- double échec -> SCHEMA_VALIDATION_ERROR, cache non pollué ---------------
def test_double_echec_schema_validation_error_et_cache_sain(monkeypatch):
    backend = _FakeBackend([_Resp(_completion(_INVALIDE)), _Resp(_completion(_INVALIDE)),
                            _Resp(_completion(_VALIDE))])
    monkeypatch.setattr(llm.httpx, "post", backend.post)
    llm.start_trace()
    out = llm.call_skill("coherence_pertinence", _PAYLOAD)
    assert out["error"] == "SCHEMA_VALIDATION_ERROR"
    assert "est_coherent" in out["detail"]
    assert "peut-être" in out["raw_output"]
    recs = llm.stop_trace()
    assert recs[0]["validation"] == "invalide" and recs[0]["ok"] is False
    # la réponse invalide n'a PAS été mise en cache : l'appel suivant repart
    # au LLM (3e POST) et obtient cette fois un résultat valide.
    out2 = llm.call_skill("coherence_pertinence", _PAYLOAD)
    assert not out2.get("error") and out2["est_coherent"] is False
    assert len(backend.bodies) == 3


# --- repli json_schema -> json_object ----------------------------------------
def test_repli_json_object_si_backend_rejette_json_schema(monkeypatch):
    backend = _FakeBackend([_Resp(status=400),                # json_schema rejeté
                            _Resp(_completion(_VALIDE)),      # rejoué en json_object
                            _Resp(_completion(_VALIDE))])     # appel suivant, direct
    monkeypatch.setattr(llm.httpx, "post", backend.post)
    out = llm.call_skill("coherence_pertinence", _PAYLOAD)
    assert not out.get("error") and out["est_coherent"] is False
    formats = [b["response_format"]["type"] for b in backend.bodies]
    assert formats == ["json_schema", "json_object"]
    # le refus est mémorisé : plus aucune tentative json_schema ensuite
    assert llm._json_schema_supported is False
    llm.clear_cache()
    llm.call_skill("coherence_pertinence", _PAYLOAD)
    assert backend.bodies[-1]["response_format"]["type"] == "json_object"
    # la validation Pydantic reste active malgré le repli
    assert not out.get("error")


def test_erreur_http_hors_response_format_non_avalee(monkeypatch):
    # Un 500 n'est pas un rejet de json_schema : pas de repli, erreur franche.
    backend = _FakeBackend([_Resp(status=500)])
    monkeypatch.setattr(llm.httpx, "post", backend.post)
    out = llm.call_skill("coherence_pertinence", _PAYLOAD)
    assert out["error"] == "LLM_INVOCATION_ERROR"
    assert len(backend.bodies) == 1
    assert llm._json_schema_supported is None  # verdict non figé sur un 500


# --- vote : les tirages invalides sont écartés, sans retry -------------------
def test_sample_skill_ecarte_tirages_invalides(monkeypatch):
    backend = _FakeBackend([_Resp(_completion(_VALIDE)), _Resp(_completion(_INVALIDE)),
                            _Resp(_completion(_VALIDE))])
    monkeypatch.setattr(llm.httpx, "post", backend.post)
    votes = llm.sample_skill("coherence_pertinence", _PAYLOAD, n=3)
    assert len(votes) == 2
    assert all(v["est_coherent"] is False for v in votes)
    # pas de retry sur un tirage de vote : exactement 3 POST
    assert len(backend.bodies) == 3


# --- génération libre : validation sans contrainte de grammaire --------------
def test_generation_libre_valide_sans_contraindre(monkeypatch):
    # `redondance_surspec` est validé/retryé mais généré en json_object
    # (contraindre sa grammaire dégrade la précision T3, cf. schemas.py).
    contenu = {"aspects_cible": [], "aspects_nouveaux": ["a"], "est_redondante": False,
               "est_sur_specifiee": False, "soeurs_en_conflit": [],
               "niveau_gravite": "INFO", "preuve": "", "synthese": "ok"}
    backend = _FakeBackend([_Resp(_completion(contenu)),
                            _Resp(_completion({"est_redondante": "?"})),   # invalide
                            _Resp(_completion(contenu))])                  # corrigé
    monkeypatch.setattr(llm.httpx, "post", backend.post)
    out = llm.call_skill("redondance_surspec", {"exigence_cible": {"id": "X"}})
    assert out["est_redondante"] is False
    assert backend.bodies[0]["response_format"] == {"type": "json_object"}
    llm.clear_cache()
    out2 = llm.call_skill("redondance_surspec", {"exigence_cible": {"id": "X"}})
    assert not out2.get("error") and len(backend.bodies) == 3  # retry validé quand même


# --- sans schéma : comportement historique inchangé --------------------------
def test_sans_schema_pas_de_validation(monkeypatch):
    contenu = {"nimporte": "quoi"}
    backend = _FakeBackend([_Resp(_completion(contenu))])
    monkeypatch.setattr(llm.httpx, "post", backend.post)
    llm.start_trace()
    out = llm.call_agent("prompt libre", {"x": 1})  # pas de label -> pas de schéma
    assert out == contenu
    assert backend.bodies[0]["response_format"] == {"type": "json_object"}
    assert "validation" not in llm.stop_trace()[0]
