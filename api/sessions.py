"""Sessions de conversation (persistées en Mongo) : liste, lecture,
renommage, suppression. Imports core.* paresseux (démarrage rapide)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


@router.get("/api/sessions")
def sessions() -> dict:
    try:
        from core.chat_sessions import list_sessions
        return {"available": True,
                "sessions": [{"id": s["session_id"], "title": s.get("title", ""),
                              "updated_at": s.get("updated_at", ""),
                              "source_filter": s.get("source_filter")}
                             for s in list_sessions()]}
    except Exception:
        return {"available": False, "sessions": []}


@router.get("/api/sessions/{sid}/messages")
def session_messages(sid: str) -> dict:
    from core.chat_sessions import get_messages, get_session
    sess = get_session(sid)
    if not sess:
        raise HTTPException(404, "Session introuvable.")
    return {"title": sess.get("title", ""), "source_filter": sess.get("source_filter"),
            "messages": get_messages(sid)}


class RenameBody(BaseModel):
    title: str


@router.patch("/api/sessions/{sid}")
def rename(sid: str, body: RenameBody) -> dict:
    from core.chat_sessions import rename_session
    rename_session(sid, body.title.strip() or "Conversation")
    return {"ok": True}


@router.delete("/api/sessions/{sid}")
def delete_sess(sid: str) -> dict:
    from core.chat_sessions import delete_session
    delete_session(sid)
    return {"ok": True}
