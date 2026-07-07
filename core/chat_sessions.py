# core/chat_sessions.py
"""
Gestion des sessions de conversation persistées dans MongoDB.
Collection : ragdb.chat_sessions

Chaque session contient :
  - _id         : ObjectId MongoDB
  - session_id  : str (raccourci lisible)
  - title       : str (auto-généré depuis la 1ère question, max 60 chars)
  - source_filter : str | None  (document filtré lors de la session)
  - created_at  : ISO timestamp
  - updated_at  : ISO timestamp
  - messages    : liste de {"role": "user"|"assistant", "content": str, "citations": list}
"""
from __future__ import annotations

import uuid
import time
from typing import Optional
from pymongo import MongoClient, DESCENDING
from env_config import MONGO_URI as _MONGO_URI, MONGO_DB as _DB_NAME


_COL_NAME  = "chat_sessions"
_CLIENT = None  # client singleton : un pool partagé, jamais un client par appel


def _col():
    """Retourne la collection des sessions chat.

    Client SINGLETON avec timeout court : l'ancien code créait un MongoClient
    par appel (fuite de sockets/threads au fil des requêtes) et, sans
    serverSelectionTimeoutMS, chaque opération gelait 30 s quand Mongo était
    éteint (dont create_session au tout début du flux SSE du chat)."""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = MongoClient(_MONGO_URI, serverSelectionTimeoutMS=1500)
    return _CLIENT[_DB_NAME][_COL_NAME]


# -----------------------------------------------------------------------------
#  CRUD
# -----------------------------------------------------------------------------

def create_session(source_filter: str = None) -> str:
    """
    Crée une nouvelle session vide et retourne son session_id (str uuid court).
    """
    session_id = uuid.uuid4().hex[:12]
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    _col().insert_one({
        "session_id":    session_id,
        "title":         "Nouvelle conversation",
        "source_filter": source_filter,
        "created_at":    now,
        "updated_at":    now,
        "messages":      [],
    })
    return session_id


def get_session(session_id: str) -> Optional[dict]:
    """Retourne la session complète (avec messages) ou None si introuvable."""
    doc = _col().find_one({"session_id": session_id})
    if doc:
        doc["_id"] = str(doc["_id"])
    return doc


def list_sessions(limit: int = 50) -> list[dict]:
    """
    Retourne les sessions triées par date décroissante (sans les messages)
    pour affichage dans la barre latérale.
    """
    sessions = []
    for doc in _col().find({}, {"messages": 0}).sort("updated_at", DESCENDING).limit(limit):
        doc["_id"] = str(doc["_id"])
        sessions.append(doc)
    return sessions


def add_message(session_id: str, role: str, content: str, citations: list = None,
                reasoning: str = None, chunks: list = None):
    """
    Ajoute un message à la session et met à jour le titre si c'est le 1er message user.
    `reasoning` (optionnel) : trace de raisonnement de l'agent, affichée repliée.
    `chunks` (optionnel) : passages récupérés (contenu + méta), pour inspection a posteriori.
    """
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    msg = {"role": role, "content": content, "citations": citations or []}
    if reasoning:
        msg["reasoning"] = reasoning
    if chunks:
        msg["chunks"] = chunks

    # Auto-titre : uniquement sur le premier message utilisateur.
    if role == "user":
        session = _col().find_one({"session_id": session_id}, {"messages": 1, "title": 1})
        first_user_message = session and not any(m["role"] == "user" for m in session.get("messages", []))
        if first_user_message:
            title = content[:60] + ("..." if len(content) > 60 else "")
            _col().update_one(
                {"session_id": session_id},
                {"$set": {"title": title, "updated_at": now}, "$push": {"messages": msg}},
            )
            return

    _col().update_one(
        {"session_id": session_id},
        {"$set": {"updated_at": now}, "$push": {"messages": msg}},
    )


def update_session_source(session_id: str, source_filter: Optional[str]):
    """Met à jour le filtre document d'une session."""
    _col().update_one(
        {"session_id": session_id},
        {"$set": {"source_filter": source_filter,
                  "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}},
    )


def rename_session(session_id: str, title: str):
    """Renomme une session (titre saisi par l'utilisateur)."""
    title = (title or "").strip()[:80] or "Nouvelle conversation"
    _col().update_one(
        {"session_id": session_id},
        {"$set": {"title": title, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}},
    )


def add_session_document(session_id: str, doc_name: str):
    """Mémorise un document ingéré durant la session (liste dédupliquée `documents`)."""
    if not session_id or not doc_name:
        return
    _col().update_one(
        {"session_id": session_id},
        {"$addToSet": {"documents": doc_name},
         "$set": {"updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}},
    )


def get_session_documents(session_id: str) -> list[str]:
    """Documents retenus en mémoire pour cette session (uploadés pendant la session)."""
    doc = _col().find_one({"session_id": session_id}, {"documents": 1})
    return (doc or {}).get("documents", []) if doc else []


def delete_session(session_id: str):
    """Supprime définitivement une session."""
    _col().delete_one({"session_id": session_id})


def clear_session_messages(session_id: str):
    """Efface tous les messages d'une session (garde la session)."""
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    _col().update_one(
        {"session_id": session_id},
        {"$set": {"messages": [], "title": "Nouvelle conversation", "updated_at": now}},
    )


def get_messages(session_id: str) -> list[dict]:
    """Retourne uniquement les messages d'une session."""
    doc = _col().find_one({"session_id": session_id}, {"messages": 1})
    return doc.get("messages", []) if doc else []


def truncate_last_exchange(session_id: str) -> list[dict]:
    """Retire le DERNIER message utilisateur et tout ce qui le suit.

    Support de « modifier le dernier prompt » : l'échange (question + réponse)
    est retiré de la session avant de re-poser la question éditée — le
    rechargement de la conversation reste ainsi cohérent avec l'affichage.
    Renvoie les messages restants (inchangés si aucun message utilisateur)."""
    doc = _col().find_one({"session_id": session_id}, {"messages": 1})
    if not doc:
        return []
    msgs = doc.get("messages", [])
    idx = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=None)
    if idx is None:
        return msgs
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    msgs = msgs[:idx]
    _col().update_one(
        {"session_id": session_id},
        {"$set": {"messages": msgs, "updated_at": now}},
    )
    return msgs


def set_last_assistant_attribution(session_id: str, attribution: dict):
    """Attache l'attribution par affirmation au DERNIER message assistant.

    La passe d'attribution est post-hoc (elle tourne APRÈS la persistance du
    message, en fin de flux SSE) : on met à jour le message en place pour que
    le rechargement d'une conversation retrouve marqueurs + attribution."""
    doc = _col().find_one({"session_id": session_id}, {"messages": 1})
    if not doc:
        return
    msgs = doc.get("messages", [])
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].get("role") == "assistant":
            _col().update_one(
                {"session_id": session_id},
                {"$set": {f"messages.{i}.attribution": attribution,
                          "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}},
            )
            break


def replace_last_assistant_message(session_id: str, content: str, citations: list = None,
                                   chunks: list = None):
    """Remplace le contenu/citations (et passages) du dernier message assistant (régénération)."""
    doc = _col().find_one({"session_id": session_id}, {"messages": 1})
    if not doc:
        return
    msgs = doc.get("messages", [])
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].get("role") == "assistant":
            msgs[i]["content"] = content
            msgs[i]["citations"] = citations or []
            if chunks is not None:
                msgs[i]["chunks"] = chunks
            break
    _col().update_one(
        {"session_id": session_id},
        {"$set": {"messages": msgs, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}},
    )
