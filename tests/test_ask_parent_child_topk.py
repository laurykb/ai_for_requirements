# tests/test_ask_parent_child_topk.py
import core.ask as A
import env_config as C


def test_topk_follows_real_pc_gate_when_caller_neutral(monkeypatch):
    """Quand le caller ne force rien (None), le top-k doit suivre PARENT_CHILD_ENABLED,
    pas supposer PC actif."""
    monkeypatch.setattr(A, "PARENT_CHILD_ENABLED", False, raising=False)
    monkeypatch.setattr(C, "PARENT_CHILD_ENABLED", False, raising=False)
    assert A._effective_topk(parent_child_on=None) == C.NUM_CHUNKS

    monkeypatch.setattr(A, "PARENT_CHILD_ENABLED", True, raising=False)
    monkeypatch.setattr(C, "PARENT_CHILD_ENABLED", True, raising=False)
    assert A._effective_topk(parent_child_on=None) == C.NUM_CHUNKS_PARENT_CHILD


def test_explicit_override_wins():
    assert A._effective_topk(parent_child_on=True) == C.NUM_CHUNKS_PARENT_CHILD
    assert A._effective_topk(parent_child_on=False) == C.NUM_CHUNKS
