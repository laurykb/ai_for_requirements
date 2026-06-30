"""
Application Streamlit de l'outil RAG (point d'entrée).

Une seule page, plusieurs vues accessibles depuis la barre latérale. Chaque vue vit
dans son propre module (app/chat.py, app/documents.py, app/settings_view.py) ; les
helpers partagés sont dans app/common.py et la file d'ingestion dans app/ingestion.py.

Lancer : streamlit run app/main.py  (le fichier ne peut pas s'appeler app.py :
le stem collisionnerait avec le package app/ → "'app' is not a package").
"""
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st
from pymongo import MongoClient

from env_config import (
    MONGO_URI, OLLAMA_HOST, GEN_MODEL, EMBED_MODEL, NUM_CHUNKS, SELF_RAG_ENABLED,
)
from core.llm_answer import get_system_prompt
from core.chat_sessions import create_session, list_sessions, delete_session

from app.common import ALL_DOCS
from app.ingestion import _ingest_active, _render_ingest_queue
from app.chat import view_chat
from app.documents import view_documents
from app.settings_view import view_traces, view_settings

st.set_page_config(page_title="AI for SSH", layout="centered",
                   initial_sidebar_state="expanded")

# -- Polish CSS minimal et ciblé (sélecteurs stables uniquement) ---------------
st.markdown(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
      :root { --accent: #7c83ff; --accent-soft: rgba(124,131,255,0.14); }

      html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stSidebar"] {
        font-family: 'Inter', system-ui, -apple-system, sans-serif;
      }
      header[data-testid="stHeader"] { background: transparent; }
      .block-container { max-width: 860px; padding-top: 2.2rem; padding-bottom: 5rem; }

      /* Boutons : arrondis, transition douce */
      .stButton > button {
        border-radius: 10px; font-weight: 500; transition: transform .12s ease, filter .12s ease, background .12s ease;
        border: 1px solid rgba(255,255,255,0.09);
      }
      .stButton > button:hover { transform: translateY(-1px); }
      .stButton > button[kind="primary"],
      .stButton > button[data-testid="stBaseButton-primary"] {
        background: var(--accent); border: 0; color: #fff;
        box-shadow: 0 4px 16px rgba(124,131,255,0.32);
      }
      .stButton > button[kind="primary"]:hover { filter: brightness(1.08); }

      /* Barre latérale : séparateur fin + nav plate alignée à gauche */
      section[data-testid="stSidebar"] { border-right: 1px solid rgba(255,255,255,0.06); }
      section[data-testid="stSidebar"] .stButton > button {
        justify-content: flex-start; text-align: left; font-weight: 400;
        border: 0; background: transparent; padding: 8px 12px; border-radius: 8px;
      }
      section[data-testid="stSidebar"] .stButton > button:hover {
        background: rgba(255,255,255,0.06); transform: none;
      }
      section[data-testid="stSidebar"] .stButton > button[kind="primary"],
      section[data-testid="stSidebar"] .stButton > button[data-testid="stBaseButton-primary"] {
        background: var(--accent-soft); box-shadow: none;
      }

      /* Chat : bulles en cartes douces + saisie arrondie */
      [data-testid="stChatMessage"] {
        background: rgba(255,255,255,0.025); border: 1px solid rgba(255,255,255,0.05);
        border-radius: 14px; padding: 0.5rem 1rem; margin-bottom: 0.5rem;
      }
      [data-testid="stChatInput"] { border-radius: 14px; }

      /* Accueil : héros centré, titre en dégradé */
      .ssh-hero { text-align: center; }
      .ssh-hero h1 {
        font-weight: 700; letter-spacing: -1.2px; font-size: 3.1rem; margin-bottom: 0.3rem;
        background: linear-gradient(135deg, #d6d8ff 0%, var(--accent) 55%, #a78bfa 100%);
        -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
      }
      .ssh-hero p { color: #9aa0aa; font-size: 1.18rem; min-height: 1.7rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


# -- État ----------------------------------------------------------------------

def _init_state():
    ss = st.session_state
    ss.setdefault("view", "home")
    ss.setdefault("home_streamed", False)
    # Mode SIMPLE (défaut) : interface épurée pour un usage sans connaissance du RAG.
    # Mode EXPERT : tous les réglages (modes Auto/RAG/Agent, options de recherche, traces).
    ss.setdefault("expert_mode", False)
    ss.setdefault("scope", ALL_DOCS)
    ss.setdefault("system_prompt", get_system_prompt())
    ss.setdefault("use_memory", True)
    ss.setdefault("chat_mode", "auto")     # auto (routeur) | rag | agent (forcés)
    ss.setdefault("parent_child_on", False)
    ss.setdefault("self_rag_enabled", SELF_RAG_ENABLED)    # par-requête (popover d'options)
    ss.setdefault("num_chunks", NUM_CHUNKS)
    ss.setdefault("embed_model", EMBED_MODEL)
    ss.setdefault("gen_model", GEN_MODEL)
    ss.setdefault("active_sid", None)
    if not ss.active_sid:
        # Tolère une base indisponible : l'accueil et l'UI restent affichables,
        # la session sera créée à la première interaction de chat.
        try:
            sessions = list_sessions(limit=1)
            ss.active_sid = sessions[0]["session_id"] if sessions else create_session()
        except Exception:
            ss.active_sid = None


# -- Barre latérale ------------------------------------------------------------

def _nav_button(label: str, icon: str, view: str):
    active = st.session_state.view == view
    if st.button(label, icon=icon, use_container_width=True,
                 key=f"nav_{view}", type="primary" if active else "secondary"):
        st.session_state.view = view
        if view == "home":
            st.session_state.home_streamed = False  # re-streame le message d'accueil
        st.rerun()


def _sidebar_ingestion_badge():
    """Indicateur d'ingestion PERSISTANT, visible dans TOUTES les vues. Montre les barres
    des documents en cours / en file (compact), avec un renvoi vers l'onglet Documents."""
    if not _ingest_active():
        return
    with st.container(border=True):
        _render_ingest_queue(compact=True, key="sidebar_ingest")
        if st.button("Voir l'ingestion", icon=":material/description:",
                     use_container_width=True, key="sidebar_to_docs"):
            st.session_state.view = "documents"
            st.session_state._open_ingestion = True
            st.rerun()


def _sidebar():
    ss = st.session_state
    with st.sidebar:
        st.markdown("##### AI for SSH")
        _sidebar_ingestion_badge()

        if st.button("Nouvelle conversation", icon=":material/edit_square:",
                     use_container_width=True, key="new_conv"):
            ss.active_sid = create_session(source_filter=None)
            ss.view = "chat"
            st.rerun()

        st.caption("Récents")
        try:
            recents = list_sessions(limit=12)
        except Exception:
            recents = []
            st.caption("_(base indisponible)_")
        for s in recents:
            sid = s["session_id"]
            title = (s.get("title") or "Nouvelle conversation")[:30]
            c1, c2 = st.columns([6, 1])
            with c1:
                if st.button(title, key=f"s_{sid}", use_container_width=True,
                             type="primary" if (sid == ss.active_sid and ss.view == "chat") else "secondary"):
                    ss.active_sid = sid
                    ss.view = "chat"
                    st.rerun()
            with c2:
                if st.button("", icon=":material/close:", key=f"d_{sid}"):
                    delete_session(sid)
                    if sid == ss.active_sid:
                        ss.active_sid = None
                    st.rerun()

        st.divider()
        _nav_button("Accueil", ":material/home:", "home")
        _nav_button("Chat", ":material/forum:", "chat")
        _nav_button("Documents", ":material/description:", "documents")
        # Vue d'opérateur (observabilité) : réservée au mode Expert pour ne pas
        # noyer un utilisateur novice.
        if ss.expert_mode:
            _nav_button("Observabilité", ":material/timeline:", "traces")
        _nav_button("Paramètres", ":material/tune:", "settings")

        st.divider()
        # Bascule Simple/Expert - discrète, en bas de la barre latérale.
        _exp = st.toggle("Mode expert", value=ss.expert_mode, key="expert_toggle",
                         help="Affiche les réglages avancés (modes RAG/Agent, options de "
                              "recherche, observabilité). Désactivé = interface simple.")
        if _exp != ss.expert_mode:
            ss.expert_mode = _exp
            # En repassant en simple, on revient à un état neutre et sûr.
            if not _exp:
                ss.chat_mode = "auto"
                if ss.view == "traces":
                    ss.view = "chat"
            st.rerun()


# -- Vue : Accueil -------------------------------------------------------------

def view_home():
    ss = st.session_state
    # Landing épurée : on masque la barre latérale ET son bouton d'expansion.
    st.markdown(
        """
        <style>
          section[data-testid="stSidebar"],
          [data-testid="stSidebarCollapsedControl"],
          [data-testid="collapsedControl"] { display: none !important; }
          .ssh-hero h1 { font-size: 4.6rem !important; }
          .ssh-hero p { font-size: 1.5rem !important; }
          [data-testid="stMain"] .stButton > button {
            padding: 1.15rem 1rem !important; font-size: 1.25rem !important;
            font-weight: 600 !important; border-radius: 14px !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    _, mid, _ = st.columns([1, 3, 1])
    with mid:
        st.write("")
        st.write("")
        st.write("")
        greeting = "Bienvenue dans AI for SSH, que puis-je faire pour vous ?"
        head = "<div class='ssh-hero'><h1>AI for SSH</h1>"
        ph = st.empty()
        if not ss.home_streamed:
            acc = ""
            for word in greeting.split(" "):
                acc += word + " "
                ph.markdown(f"{head}<p>{acc.strip()}</p></div>", unsafe_allow_html=True)
                time.sleep(0.05)
            ss.home_streamed = True
        else:
            ph.markdown(f"{head}<p>{greeting}</p></div>", unsafe_allow_html=True)

        st.write("")
        st.write("")
        _, midb, _ = st.columns([1, 2, 1])
        with midb:
            if st.button("Outil RAG", icon=":material/search:", type="primary",
                         use_container_width=True, key="home_rag"):
                ss.view = "chat"
                st.rerun()
            st.write("")
            if st.button("AI for Requirements", icon=":material/account_tree:", type="primary",
                         use_container_width=True, key="home_req"):
                ss.view = "requirements"
                st.rerun()
        st.write("")
        st.caption(
            "<div style='text-align:center'>RAG documentaire (Outil RAG) - "
            "vérification d'exigences (AI for Requirements)</div>", unsafe_allow_html=True)


# -- Vue : AI for Requirements (LynX, chargé tel quel) -------------------------

_LYNX_DARK_CSS = """
<style>
/* « AI for Requirements » (LynX) aligné sur le thème SOMBRE de l'hôte : on corrige
   seulement ce que LynX code en clair/quasi-noir, sans toucher à son code. */
[data-testid="stChatMessage"] { background: #15171f !important; }
[data-testid="stAppViewContainer"] input, [data-testid="stAppViewContainer"] textarea,
[data-baseweb="input"] input, [data-baseweb="textarea"] textarea,
[data-baseweb="base-input"] input, [data-baseweb="select"] > div {
  background: #1b1e27 !important; color: #e7e8ec !important;
  border: 1px solid #2c2f3a !important; }
[style*="#111827"] { color: #e7e8ec !important; }
[style*="#374151"] { color: #cbd0da !important; }
[style*="#15803D"] { color: #22c55e !important; }
[style*="#B45309"] { color: #f59e0b !important; }
[style*="#B91C1C"] { color: #f87171 !important; }
</style>
"""


def _load_lynx():
    """Charge l'app « AI for Requirements » (LynX, dossier ./lynx) SANS la modifier.
    Importée une fois via importlib sous un nom unique (`lynx_main`) pour éviter toute
    collision avec le package `app` de l'hôte ; son dossier est mis sur sys.path pour que
    ses `from src import ...` se résolvent dans lynx/src."""
    import importlib.util
    if "lynx_main" in sys.modules:
        return sys.modules["lynx_main"]
    lynx_dir = _ROOT / "lynx"
    if str(lynx_dir) not in sys.path:
        sys.path.insert(0, str(lynx_dir))
    spec = importlib.util.spec_from_file_location("lynx_main", str(lynx_dir / "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lynx_main"] = mod
    spec.loader.exec_module(mod)
    return mod


def view_requirements():
    """Vue « AI for Requirements » : rend l'app LynX en plein écran, dans le MÊME process.
    On neutralise son `st.set_page_config` (déjà appelé par l'hôte) le temps du rendu."""
    st.markdown(_LYNX_DARK_CSS, unsafe_allow_html=True)
    try:
        mod = _load_lynx()
    except Exception as e:
        st.error(f"Impossible de charger « AI for Requirements » : {type(e).__name__}: {e}")
        return
    _orig_spc = st.set_page_config
    st.set_page_config = lambda *a, **k: None       # set_page_config ne doit être appelé qu'une fois
    try:
        mod.main()
    except Exception as e:
        st.error(f"Erreur dans « AI for Requirements » : {type(e).__name__}: {e}")
    finally:
        st.set_page_config = _orig_spc


# -- Santé des services + dispatch ---------------------------------------------

@st.cache_data(ttl=15, show_spinner=False)
def _services_health() -> dict:
    """État des services (mis en cache 15 s pour ne pas pinger à chaque rerun)."""
    import requests
    h = {"ollama": False, "mongo": False}
    try:
        requests.get(f"{OLLAMA_HOST}/api/tags", timeout=2)
        h["ollama"] = True
    except Exception:
        pass
    try:
        MongoClient(MONGO_URI, serverSelectionTimeoutMS=1500).admin.command("ping")
        h["mongo"] = True
    except Exception:
        pass
    return h


def _health_banner():
    """Bannière claire si un service requis est hors ligne (robustesse mono-poste)."""
    h = _services_health()
    down = [n for n, ok in (("Ollama", h["ollama"]), ("MongoDB", h["mongo"])) if not ok]
    if down:
        st.error(f":material/error: **{' et '.join(down)} hors ligne.** Lance les services "
                 "avec `python serve.py` - l'ingestion et les réponses ne fonctionneront pas "
                 "tant qu'ils ne sont pas démarrés.")


def main():
    _init_state()
    # Accueil ET « AI for Requirements » sont en plein écran (pas la barre latérale du RAG :
    # LynX a sa propre navigation interne).
    if st.session_state.view not in ("home", "requirements"):
        _sidebar()
        _health_banner()
    view = st.session_state.view
    if view == "home":
        view_home()
    elif view == "requirements":
        view_requirements()
    elif view == "documents":
        view_documents()
    elif view == "traces":
        view_traces()
    elif view == "settings":
        view_settings()
    else:
        view_chat()


main()
