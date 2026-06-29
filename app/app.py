"""
Assistant RAG — application unique, style ChatGPT/Claude.

Une seule page, quatre vues accessibles depuis la barre latérale :
  • Chat       — conversation, streaming, sources, périmètre documentaire
  • Documents  — ingestion (PDF→Markdown→index) + exploration (markdown + chunks)
  • Graphe     — graphe d'entités interactif (GraphRAG)
  • Paramètres — modèles, retrieval, Self-RAG, system prompt, écriture .env

Sans emoji (icônes Material en trait fin). Réutilise tout le backend existant.
Lancer :  streamlit run app/app.py
"""
import sys
import re
import json
import time
import threading
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st
from pymongo import MongoClient

from env_config import (
    MONGO_URI, MONGO_DB, OLLAMA_HOST, GEN_MODEL, EMBED_MODEL, NUM_CHUNKS,
    WEIGHT_SEMANTIC, WEIGHT_BM25, CE_RELEVANCE_THRESHOLD, GRAPH_RAG_ENABLED,
    SELF_RAG_ENABLED, SELF_RAG_THRESHOLD, SELF_RAG_MAX_RETRIES,
    AUTO_KEYWORDS, AUTO_QUESTIONS, CHUNKING_MODE, RAPTOR_SUMMARIES,
    GRAPH_USE_LLM_RELATIONS,
)
# NB : core.ask est importé paresseusement dans les vues qui en ont besoin —
# il charge torch/sentence-transformers (~15 s), inutile au rendu de la page.
from core.llm_answer import DEFAULT_SYSTEM_PROMPT, get_system_prompt
from core.chat_sessions import (
    create_session, list_sessions, get_session, get_messages, add_message,
    delete_session, clear_session_messages, update_session_source,
    replace_last_assistant_message,
)

ALL_DOCS = "Tous les documents"
OOS_PREFIX = "⚠️"
DOCS_OUT = _ROOT / "docs" / "out"
DOCS_PDF = _ROOT / "docs" / "PDF"

st.set_page_config(page_title="AI for SSH", layout="centered",
                   initial_sidebar_state="expanded")

# ── Polish CSS minimal et ciblé (sélecteurs stables uniquement) ───────────────
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


# ── Données ───────────────────────────────────────────────────────────────────

@st.cache_resource
def _chunks_col():
    return MongoClient(MONGO_URI)[MONGO_DB]["chunks"]


# ── Gestion VRAM du LLM (charger / décharger côté Ollama) ─────────────────────

def _ollama_set_keep_alive(model: str, keep_alive) -> tuple[bool, str]:
    """keep_alive=0 décharge le modèle de la VRAM ; -1 le garde résident."""
    import requests
    try:
        requests.post(f"{OLLAMA_HOST}/api/generate",
                      json={"model": model, "keep_alive": keep_alive}, timeout=30)
        return True, "OK"
    except Exception as e:
        return False, str(e)


# ── Ingestion en arrière-plan (thread + état partagé, UI non bloquante) ───────
# Dict module-level (partagé entre les reruns d'une session locale mono-utilisateur).
_INGEST = {"running": False, "pct": 0, "step": "", "result": None, "doc": "", "t0": 0.0}


def _run_ingest_bg(path: str, nkw: int, nq: int, mode: str, raptor: bool, graph_llm: bool,
                   enh_model: str = ""):
    """Convertit (si PDF) puis ingère depuis un chemin disque. Tourne dans un thread."""
    def cb(msg, pct):
        _INGEST["pct"] = min(100, int(pct))
        _INGEST["step"] = str(msg)
    try:
        DOCS_OUT.mkdir(parents=True, exist_ok=True)
        if not str(path).lower().endswith(".md"):
            _INGEST["step"] = "Conversion document → Markdown (Docling)…"
            from preprocessing.pdf_to_markdown import convert_and_clean
            md_path = convert_and_clean(str(path), out_dir=str(DOCS_OUT))
        else:
            md_path = str(path)
        from core.ingest import ingest_markdown
        stats = ingest_markdown(md_path, num_keywords=nkw, num_questions=nq,
                                enhancement_model=enh_model or None,
                                chunking_mode=mode, raptor_summaries=raptor,
                                graph_llm_relations=graph_llm, progress_callback=cb)
        if stats.get("status") == "success":
            from core.ask import clear_retrieval_caches
            clear_retrieval_caches()
        _INGEST["result"] = stats
    except Exception as e:
        _INGEST["result"] = {"status": "error", "message": str(e)}
    finally:
        _INGEST["running"] = False


def _remember_session_doc(name: str):
    """Associe un document déposé à la session courante (vue « Documents en mémoire »)."""
    sid = st.session_state.get("active_sid")
    if not sid:
        sid = st.session_state.active_sid = create_session(source_filter=None)
    try:
        from core.chat_sessions import add_session_document
        add_session_document(sid, name)
    except Exception:
        pass


def _launch_ingest(name: str, data: bytes) -> bool:
    """Écrit le document déposé et lance son ABSORPTION (ingestion) en arrière-plan avec
    les options choisies AU MOMENT DE L'UPLOAD (ss.ingest_*, cf. _pending_upload_panel).
    Retourne False si une ingestion tourne déjà."""
    if _INGEST["running"]:
        return False
    ss = st.session_state
    DOCS_OUT.mkdir(parents=True, exist_ok=True)
    DOCS_PDF.mkdir(parents=True, exist_ok=True)
    target = (DOCS_OUT / name) if name.lower().endswith(".md") else (DOCS_PDF / name)
    target.write_bytes(data)
    _INGEST.update(running=True, pct=0, step="Démarrage…", result=None, doc=name,
                   t0=time.time())
    # Trace le document dans la session courante (vue « Documents en mémoire »).
    _remember_session_doc(name)
    # Après absorption, scoper la conversation sur CE document (la réponse portera
    # dessus, façon Claude/ChatGPT). Appliqué dans view_chat une fois l'ingestion finie.
    st.session_state._scope_to_new_doc = name
    threading.Thread(
        target=_run_ingest_bg,
        args=(str(target), int(ss.ingest_nkw), int(ss.ingest_nq), ss.ingest_mode,
              bool(ss.ingest_raptor), bool(ss.ingest_graph_llm), ss.ingest_enh_model.strip()),
        daemon=True,
    ).start()
    return True


def _render_ingestion_activity():
    """Affiche l'activité d'ingestion dans le fil du chat, en menu déroulant (st.status) :
    progression tant qu'elle tourne, résumé une fois terminée. Lit l'état global _INGEST,
    donc visible quelle que soit la vue d'où l'ingestion a été lancée."""
    doc = _INGEST.get("doc") or "document"
    if _INGEST["running"]:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=1500, key="chat_ingest_poll")
        elapsed = int(time.time() - (_INGEST.get("t0") or time.time()))
        pending = st.session_state.get("pending_prompt")
        with st.status(f"Absorption de « {doc} » en cours… ({elapsed}s écoulées)",
                       state="running", expanded=True):
            st.progress(min(100, _INGEST["pct"]) / 100.0, text=_INGEST["step"] or "En cours…")
            if pending:
                st.caption(":material/schedule: Votre question est **en attente** — elle sera "
                           "traitée automatiquement dès que le document sera absorbé.")
            else:
                st.caption("Vous pouvez continuer à discuter ; suivez le détail dans la vue Documents.")
        return

    res = _INGEST.get("result")
    if res is None:
        return
    if res.get("status") == "success":
        with st.status(f"« {doc} » indexé — {res.get('num_chunks', '?')} chunks",
                       state="complete", expanded=False):
            st.write(res.get("message", "Ingestion terminée."))
            enh = res.get("enhancement", {}) or {}
            g = res.get("graph", {}) or {}
            st.caption(f"Entités : {g.get('nodes', '—')} · chunks enrichis : "
                       f"{enh.get('chunks_enhanced', '—')}. Exploration détaillée dans la vue Documents.")
        if st.button("OK", key="ack_ingest"):
            _INGEST["result"] = None
            st.rerun()
        return

    # Échec : le document n'a PAS été indexé. On ne répond pas silencieusement sans lui —
    # l'utilisateur choisit de poser la question quand même (sur l'index existant) ou d'abandonner.
    with st.status(f"Échec de l'absorption de « {doc} »", state="error", expanded=True):
        st.write(res.get("message", "Échec de l'ingestion."))
        st.caption("Le document n'a PAS été indexé. Redépose-le pour réessayer.")
    pend = st.session_state.get("pending_prompt")
    cols = st.columns(2)
    if pend and cols[0].button("Poser la question quand même", key="ask_anyway",
                               use_container_width=True):
        _INGEST["result"] = None        # libère la question → réponse sur l'index existant
        st.rerun()
    if cols[1].button("Abandonner", key="ack_ingest", use_container_width=True):
        _INGEST["result"] = None
        st.session_state.pop("pending_prompt", None)
        st.rerun()


# Types de documents acceptés au dépôt dans le chat / l'accueil (convertis via Docling).
_UPLOAD_TYPES = ["pdf", "docx", "pptx", "xlsx", "html", "md"]


def _handle_chat_files(files) -> None:
    """Démarre IMMÉDIATEMENT l'absorption du document joint (façon ChatGPT/Gemini : la pièce
    jointe se traite AVANT l'envoi ; le champ de saisie reste désactivé pendant ce temps).
    Options d'ingestion = valeurs par défaut (modifiables dans Paramètres)."""
    if not files:
        return
    # Un seul document à la fois : on refuse un nouveau dépôt pendant une absorption.
    if _INGEST["running"]:
        st.toast("Un seul document à la fois — attends la fin de l'absorption en cours.")
        return
    f = files[0]
    if len(files) > 1:
        st.toast("Un seul document à la fois : seul le premier joint est pris en compte.")
    _launch_ingest(f.name, f.getvalue())     # absorption immédiate (auto, sans étape manuelle)


def _pending_upload_panel() -> None:
    """Document déposé dans le chat, EN ATTENTE : choisir les OPTIONS d'ingestion puis lancer
    l'absorption. Les options d'ingestion vivent ICI (au moment de l'upload), et plus dans la
    popover « Options » (qui ne garde que la recherche)."""
    ss = st.session_state
    up = ss.get("pending_upload")
    if not up or _INGEST["running"]:
        return
    with st.container(border=True):
        st.markdown(f":material/upload_file: **Prêt à absorber : {up['name']}**")
        st.caption("Choisis les options d'ingestion puis lance l'absorption. Ta question "
                   "(si tu en as posé une) sera traitée juste après.")
        c1, c2 = st.columns(2)
        with c1:
            ss.ingest_mode = st.segmented_control(
                "Découpage", ["technical", "naive"], key="pu_mode",
                default=ss.ingest_mode if ss.ingest_mode in ("technical", "naive") else "technical",
                help="technical : hiérarchie normative (ANSSI/CC). naive : par titres.")
        with c2:
            ss.ingest_enh_model = st.text_input(
                "Modèle d'enrichissement (vide = défaut)", value=ss.ingest_enh_model,
                key="pu_enh", placeholder="ex: llama3.2:3b pour accélérer")
        c3, c4 = st.columns(2)
        ss.ingest_nkw = c3.number_input("Mots-clés / chunk", 0, 10, int(ss.ingest_nkw), key="pu_nkw")
        ss.ingest_nq = c4.number_input("Questions / chunk", 0, 10, int(ss.ingest_nq), key="pu_nq")
        ss.ingest_raptor = st.toggle("Résumés RAPTOR par section", value=ss.ingest_raptor,
                                     key="pu_raptor")
        ss.ingest_graph_llm = st.toggle("Relations LLM du graphe (+riche, +lent)",
                                        value=ss.ingest_graph_llm, key="pu_graph")
        b1, b2 = st.columns(2)
        if b1.button("Absorber le document", icon=":material/auto_awesome:", type="primary",
                     key="pu_go", use_container_width=True):
            _launch_ingest(up["name"], up["data"])
            ss.pop("pending_upload", None)
            st.rerun()
        if b2.button("Annuler", key="pu_cancel", use_container_width=True):
            ss.pop("pending_upload", None)
            ss.pop("pending_prompt", None)
            st.rerun()


def _options_popover():
    """Menu d'options accolé à la zone de saisie (façon Mistral / Gemini) : uniquement les
    leviers de RECHERCHE (appliqués à la question courante). Les options d'INGESTION se
    choisissent au dépôt d'un document (_pending_upload_panel). Valeurs en session_state."""
    ss = st.session_state
    with st.popover("Options", icon=":material/tune:"):
        st.markdown("**Recherche** — appliqué à vos questions")
        ss.rewrite_enabled = st.toggle("Réécriture de requête (LLM)", value=ss.rewrite_enabled,
                                       help="Reformule/condense la question avant la recherche (+latence).")
        ss.graph_rag_enabled = st.toggle("GraphRAG", value=ss.graph_rag_enabled,
                                         help="Exploite le graphe d'entités du document.")
        ss.parent_child_on = st.toggle("Contexte parent (parent-child)", value=ss.parent_child_on,
                                       help="Renvoie la section parente entière du passage trouvé.")
        ss.self_rag_enabled = st.toggle("Auto-correction (vérifie et retente)", value=ss.self_rag_enabled,
                                        help="Étend le vérificateur : si la fidélité aux sources est jugée "
                                             "faible, reformule et régénère automatiquement avant d'afficher "
                                             "(qualité +, latence +). En mode Auto, remplace la vérification a posteriori.")
        ss.use_memory = st.toggle("Mémoire de conversation", value=ss.use_memory,
                                  help="Tient compte des échanges précédents.")
        st.caption("Les options d'**ingestion** se choisissent au moment de **déposer un "
                   "document** (plus logique), pas ici.")


def list_sources() -> list[str]:
    try:
        return sorted(s for s in _chunks_col().distinct("source") if s)
    except Exception:
        return []


@st.cache_data(ttl=30, show_spinner=False)
def _graph_docs() -> set:
    """Documents qui ont un graphe d'entités (pour le badge statut)."""
    try:
        return set(MongoClient(MONGO_URI)[MONGO_DB]["entity_graph"].distinct("source_doc"))
    except Exception:
        return set()


# ── État ──────────────────────────────────────────────────────────────────────

def _init_state():
    ss = st.session_state
    ss.setdefault("view", "home")
    ss.setdefault("home_streamed", False)
    ss.setdefault("scope", ALL_DOCS)
    ss.setdefault("system_prompt", get_system_prompt())  # épuré en mode rapide
    ss.setdefault("use_memory", True)
    ss.setdefault("chat_mode", "auto")     # auto (routeur) | rag | agent (forcés)
    ss.setdefault("rewrite_enabled", False)
    ss.setdefault("graph_rag_enabled", GRAPH_RAG_ENABLED)  # OFF par défaut (A/B : latence sans gain)
    ss.setdefault("parent_child_on", False)
    ss.setdefault("self_rag_enabled", SELF_RAG_ENABLED)    # par-requête (popover d'options)
    # Options d'ingestion des documents déposés dans le chat/l'accueil (popover d'options).
    ss.setdefault("ingest_mode", CHUNKING_MODE if CHUNKING_MODE in ("technical", "naive") else "technical")
    ss.setdefault("ingest_raptor", RAPTOR_SUMMARIES)
    ss.setdefault("ingest_graph_llm", GRAPH_USE_LLM_RELATIONS)
    ss.setdefault("ingest_nkw", AUTO_KEYWORDS)
    ss.setdefault("ingest_nq", AUTO_QUESTIONS)
    ss.setdefault("ingest_enh_model", "")
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


# ── Barre latérale ────────────────────────────────────────────────────────────

def _nav_button(label: str, icon: str, view: str):
    active = st.session_state.view == view
    if st.button(label, icon=icon, use_container_width=True,
                 key=f"nav_{view}", type="primary" if active else "secondary"):
        st.session_state.view = view
        if view == "home":
            st.session_state.home_streamed = False  # re-streame le message d'accueil
        st.rerun()


def _sidebar_ingestion_badge():
    """Indicateur d'ingestion PERSISTANT, visible dans TOUTES les vues (via la barre
    latérale). Lit l'état global _INGEST : progression tant qu'elle tourne, statut final
    sinon. Complète _render_ingestion_activity() qui, lui, ne s'affiche que dans le fil
    du chat. Résout le « on ne sait pas si une ingestion est en cours »."""
    running = _INGEST["running"]
    res = _INGEST.get("result")
    if not running and res is None:
        return
    doc = _INGEST.get("doc") or "document"
    with st.container(border=True):
        if running:
            from streamlit_autorefresh import st_autorefresh
            st_autorefresh(interval=1500, key="sidebar_ingest_poll")
            st.caption(":material/sync: **Ingestion en cours**")
            st.progress(min(100, _INGEST["pct"]) / 100.0,
                        text=f"{doc} — {min(100, _INGEST['pct'])}%")
        elif res.get("status") == "success":
            st.caption(f":material/check_circle: « {doc} » indexé "
                       f"({res.get('num_chunks', '?')} chunks)")
        else:
            st.caption(f":material/error: Échec — « {doc} »")


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
        _nav_button("Graphe d'entités", ":material/hub:", "graph")
        _nav_button("Observabilité", ":material/timeline:", "traces")
        _nav_button("Paramètres", ":material/tune:", "settings")


# ── Vue : Accueil ─────────────────────────────────────────────────────────────

def view_home():
    ss = st.session_state
    # Landing épurée : on masque la barre latérale ET son bouton d'expansion « » ».
    st.markdown(
        """
        <style>
          section[data-testid="stSidebar"],
          [data-testid="stSidebarCollapsedControl"],
          [data-testid="collapsedControl"] { display: none !important; }
          /* Accueil : boutons plus grands et aérés. */
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
            "<div style='text-align:center'>RAG documentaire (Outil RAG) · "
            "vérification d'exigences (AI for Requirements)</div>", unsafe_allow_html=True)


# ── Vue : Chat ────────────────────────────────────────────────────────────────

def _render_sources(citations: list[dict]):
    if not citations:
        return
    with st.expander(f"Sources ({len(citations)})"):
        for c in citations:
            src = c.get("source", "document")
            loc = c.get("heading") or c.get("breadcrumb") or f"section {c.get('section', '?')}"
            page = f" · p. {c['page']}" if c.get("page") else ""
            st.markdown(f"`[{c.get('idx', '?')}]` {src} — {loc}{page}")


def _chat_eval_ui(messages: list):
    """Vérificateur inline de la dernière réponse (à la demande) : 1 appel LLM-as-judge
    fusionné → 3 axes + extraits problématiques cités. Sur demande = 0 latence ajoutée
    au flux de réponse normal."""
    ss = st.session_state
    if not (messages and messages[-1]["role"] == "assistant" and messages[-1].get("chunks")):
        return
    answer = messages[-1]["content"]
    chunks = messages[-1]["chunks"]
    q = messages[-2]["content"] if len(messages) >= 2 and messages[-2]["role"] == "user" else ""
    if not ss.get("eval_result"):
        if st.button("Vérifier la réponse", icon=":material/fact_check:",
                     help="Contrôle la fidélité aux sources (1 appel, à la demande)."):
            with st.spinner("Vérification (fidélité aux sources)…"):
                from core.evaluation import verify_answer
                ss.eval_result = verify_answer(q, answer, chunks)
            st.rerun()

    er = ss.get("eval_result")
    if er:
        st.caption("Vérification automatique (LLM-as-judge, 0 → 1)")
        cc = st.columns(3)
        cc[0].metric("Fidélité", f"{(er.get('faithfulness') or 0):.2f}")
        cc[1].metric("Pertinence réponse", f"{(er.get('answer_relevance') or 0):.2f}")
        cc[2].metric("Pertinence contexte", f"{(er.get('context_relevance') or 0):.2f}")
        issues = er.get("issues") or []
        if issues:
            with st.expander(f"Points à vérifier ({len(issues)})", expanded=False):
                for it in issues:
                    st.caption(f"• {it}")
        if st.button("Fermer la vérification"):
            ss.eval_result = None
            st.rerun()


def _trim_chunk(c: dict) -> dict:
    """Réduit un chunk aux champs utiles à l'affichage et à la régénération (persistance).

    On conserve le `doc` INTÉGRAL (jamais tronqué) et les métadonnées d'enrichissement
    (mots-clés / questions / entités / type) pour pouvoir montrer EXACTEMENT ce que le
    retriever est allé chercher, y compris sur les anciens messages rechargés."""
    meta = c.get("meta", {})
    keep = ("source", "page_number", "heading", "breadcrumb", "section_idx", "chunk_idx",
            "chunk_type", "keywords_str", "questions_str", "entities_str", "summary_num_chunks")
    return {"doc": c.get("doc", ""), "ce_score": c.get("ce_score"),
            "meta": {k: meta.get(k) for k in keep if meta.get(k) is not None}}


def _chunk_label(c: dict, i: int) -> str:
    """Libellé d'un passage récupéré : [n] source — section · page · score."""
    meta = c.get("meta", {})
    src = meta.get("source", "document")
    loc = (meta.get("heading") or meta.get("breadcrumb")
           or (f"section {meta.get('section_idx')}" if meta.get("section_idx") is not None else ""))
    page = f" · p.{meta.get('page_number')}" if meta.get("page_number") else ""
    ce = c.get("ce_score")
    score = f" · score {ce:.2f}" if isinstance(ce, (int, float)) else ""
    return f"[{i + 1}] {src}" + (f" — {loc}" if loc else "") + page + score


_CHUNK_TYPE_LABELS = {"table": "Tableau", "figure": "Figure", "mixed": "Tableau + figure"}


def _render_chunk_detail(c: dict):
    """Affiche le contenu INTÉGRAL d'un passage + ses métadonnées d'enrichissement
    (type, mots-clés, questions auto-générées, entités) — ce que l'indexation a réellement
    associé au chunk. Les métadonnées absentes sont simplement omises."""
    meta = c.get("meta", {})
    st.markdown(c.get("doc", "") or "_(contenu vide)_")

    bits = []
    ctype = meta.get("chunk_type")
    if ctype == "summary":
        n = meta.get("summary_num_chunks")
        bits.append(f"**Type** · Résumé RAPTOR{f' ({n} chunks)' if n else ''}")
    elif ctype in _CHUNK_TYPE_LABELS:
        bits.append(f"**Type** · {_CHUNK_TYPE_LABELS[ctype]}")
    if meta.get("keywords_str"):
        bits.append(f"**Mots-clés** · {meta['keywords_str']}")
    if meta.get("questions_str"):
        bits.append(f"**Questions** · {meta['questions_str']}")
    if meta.get("entities_str"):
        bits.append(f"**Entités** · {meta['entities_str']}")
    if bits:
        st.divider()
        for b in bits:
            st.caption(b)


def _chat_chunks_for_message(m: dict, idx: int, is_last: bool, question: str):
    """Panneau « Passages récupérés » d'UNE réponse : un menu déroulant par chunk
    (contenu intégral + métadonnées). Consultable sur TOUTE réponse de l'historique.
    Pour la DERNIÈRE réponse seulement : cases à cocher + régénération (la régénération
    remplace la dernière réponse, donc n'a de sens que là)."""
    ss = st.session_state
    chunks = m.get("chunks") or []
    if not chunks:
        return
    if not st.toggle(f"Passages récupérés ({len(chunks)})", key=f"show_chunks_{idx}",
                     help="Voir le contenu exact de chaque passage retrouvé dans la base."
                          + (" Cochez ceux à garder, puis régénérez." if is_last else "")):
        return

    prefix = f"chk_{idx}"
    checks = []
    for i, c in enumerate(chunks):
        if is_last:
            col_cb, col_exp = st.columns([1, 22])
            checks.append(col_cb.checkbox(" ", value=True, key=f"{prefix}_{i}",
                                          label_visibility="collapsed"))
            container = col_exp
        else:
            container = st.container()
        with container:
            with st.expander(_chunk_label(c, i), expanded=False):
                _render_chunk_detail(c)

    if is_last and st.button("Régénérer avec la sélection", icon=":material/refresh:",
                             key=f"regen_{idx}"):
        selected = [chunks[i] for i, ok in enumerate(checks) if ok]
        if not selected:
            st.warning("Cochez au moins un passage.")
        elif not question:
            st.warning("Question d'origine introuvable.")
        else:
            with st.spinner("Régénération…"):
                from core.ask import process_query
                rep, _ch, cits = process_query(question, selected_chunks=selected,
                                               system_prompt=ss.system_prompt)
            replace_last_assistant_message(ss.active_sid, rep or "", cits or [], chunks=selected)
            ss.eval_result = None
            st.rerun()


def _run_agent_ui(prompt: str, source_filter: str | None):
    """Mode Agent (ReAct) STREAMÉ. Le RAISONNEMENT (Pensée/Action/Observation) s'affiche
    dans un bloc repliable distinct (replié à la fin, façon Gemini) ; la RÉPONSE finale
    se streame en dessous, clairement séparée. Retourne (answer, citations, reasoning, chunks)
    — les chunks = passages INTÉGRAUX récupérés par l'agent, pour le panneau « Passages »."""
    from core.agent import ReActAgent
    from tools.rag_tool import run_tool as _rt

    def _scoped_runner(name, arguments):
        # L'agent respecte le périmètre documentaire choisi dans le chat.
        if source_filter and isinstance(arguments, dict) and not arguments.get("document"):
            arguments = {**arguments, "document": source_filter}
        return _rt(name, arguments)

    status = st.status("Réflexion en cours…", expanded=True)
    answer_ph = st.empty()  # la réponse finale se streame ici, SOUS le raisonnement
    parts: list[str] = []
    trace: list[str] = []
    result: dict = {}

    for ev in ReActAgent(tool_runner=_scoped_runner).run_stream(prompt):
        kind = ev.get("type")
        if kind == "thought":
            status.markdown(f"**Pensée** — {ev['text']}")
            trace.append(f"**Pensée** — {ev['text']}")
        elif kind == "action":
            q = ev["input"].get("query", "")
            status.markdown(f"&nbsp;&nbsp;↳ **Recherche** `{q}`")
            trace.append(f"↳ **Recherche** `{q}`")
        elif kind == "observation":
            status.caption(ev["text"])
            trace.append(f"_{ev['text']}_")
        elif kind == "answer_token":
            parts.append(ev["text"])
            answer_ph.markdown("".join(parts))
        elif kind == "done":
            result = ev.get("result", {})

    if not result.get("ok", False):
        status.update(label="Échec de l'agent", state="error")
        msg = result.get("error", "Échec de l'agent.")
        answer_ph.error(msg)
        return msg, [], None, []

    status.update(label=f"Raisonnement · {result.get('tool_calls', 0)} recherche(s) · "
                        f"{result.get('latency_s')}s",
                  state="complete", expanded=False)
    answer = result.get("answer", "")
    answer_ph.markdown(answer)  # la réponse finale, clairement distincte du raisonnement
    citations = result.get("sources", [])
    _render_sources(citations)
    return answer, citations, "\n\n".join(trace), result.get("chunks", [])


def _chat_session_header():
    """En-tête de conversation : titre renommable (#7) + compteur de documents en mémoire.
    L'exploration/visualisation des documents se fait juste en dessous, inline dans le chat
    (_chat_document_panel, #2/#3/#6)."""
    ss = st.session_state
    sid = ss.get("active_sid")
    if not sid:
        return
    from core.chat_sessions import get_session, rename_session, get_session_documents
    sess = get_session(sid) or {}
    title = sess.get("title") or "Nouvelle conversation"
    n_docs = len(get_session_documents(sid))

    h1, h2 = st.columns([3, 2])
    with h1:
        with st.popover(f":material/edit: {title[:46]}", use_container_width=True):
            new = st.text_input("Renommer la conversation", value=title, key=f"rn_{sid}")
            if st.button("Renommer", key=f"rnb_{sid}", icon=":material/check:"):
                rename_session(sid, new)
                st.rerun()
    with h2:
        st.caption(f":material/folder: {n_docs} document(s) en mémoire"
                   if n_docs else ":material/folder_off: Aucun document en mémoire")


def _chat_document_panel():
    """Visualisation + exploration du document DANS le chat, sans aller dans l'onglet
    Documents (#2/#3). Liste d'abord les documents en mémoire de la session (#6) ; à défaut,
    tous les documents indexés. Navigation par chunks et page blanche lisible conservées."""
    ss = st.session_state
    from core.chat_sessions import get_session_documents
    session_docs = get_session_documents(ss.get("active_sid")) if ss.get("active_sid") else []
    sources = session_docs or list_sources()
    if not sources:
        return
    label = (f":material/folder_open: {len(session_docs)} document(s) en mémoire — voir / explorer"
             if session_docs else ":material/description: Voir / explorer un document")
    with st.expander(label, expanded=False):
        src = st.selectbox("Document", sources, key="chatdoc_src",
                           label_visibility="collapsed")
        # Dans le chat : navigation par chunks UNIQUEMENT. Visualisation page entière + résumé
        # → onglet Documents (st.caption ci-dessous le rappelle).
        _render_doc_explorer(src, key_prefix="chatdoc", show_viewer=False)
        st.caption(":material/info: Visualisation complète et résumé du document : onglet **Documents**.")


def view_chat():
    ss = st.session_state
    _chat_session_header()
    # Snapshot COHÉRENT de l'état d'absorption pour TOUT ce rerun. Sans ça, _INGEST["running"]
    # était lu plusieurs fois et pouvait basculer (thread) ENTRE le vidage de la sélection et
    # le gate → la question partait SANS document sélectionné → échec du RAG. Tout le flux
    # upload→absorption→sélection→réponse devient strictement séquentiel et déterministe.
    _ingesting = _INGEST["running"]
    _staged = bool(ss.get("pending_upload"))
    _ingest_failed = (_INGEST.get("result") or {}).get("status") == "error"
    sources = list_sources()
    # Présélection du périmètre. Après absorption d'un doc déposé, on le coche d'office
    # (la réponse portera dessus, façon Claude/ChatGPT). On modifie l'état du widget AVANT
    # de l'instancier (clé `scope_multiselect`), donc sans conflit Streamlit.
    ss.setdefault("scope_multiselect", [])
    # Un document vient d'être déposé / est en cours d'absorption → DÉCOCHER l'ancien
    # document (on travaille sur un seul doc ; le nouveau sera coché après son absorption).
    if _staged or _ingesting:
        ss.scope_multiselect = []
    elif ss.get("_scope_to_new_doc"):
        _nd = ss.pop("_scope_to_new_doc")
        if _nd in sources:
            ss.scope_multiselect = [_nd]          # cocher le doc fraîchement absorbé
    ss.scope_multiselect = [d for d in ss.scope_multiselect if d in sources]  # purge disparus
    ss._prev_scope_sel = list(ss.scope_multiselect)   # « avant » pour détecter le nouveau coché
    _chat_document_panel()          # explorer/visualiser le document SANS quitter le chat

    # Sélecteur de document à interroger. UN SEUL document à la fois : si l'utilisateur en
    # coche un autre, on DÉCOCHE le précédent (le RAG est fiable sur un seul document, pas
    # sur du multi-document). Géré par un callback on_change qui peut modifier l'état du
    # widget avant le rerun.
    def _enforce_single_doc():
        sel = st.session_state.scope_multiselect
        if len(sel) > 1:
            prev = st.session_state.get("_prev_scope_sel", [])
            added = [d for d in sel if d not in prev]      # le document qu'on vient de cocher
            st.session_state.scope_multiselect = [added[-1]] if added else [sel[-1]]
            st.toast("Un seul document à la fois — le précédent a été décoché.")
        st.session_state._prev_scope_sel = list(st.session_state.scope_multiselect)

    top = st.columns([4, 1])
    with top[0]:
        selected = st.multiselect(
            "Document à interroger", sources, key="scope_multiselect",
            on_change=_enforce_single_doc, label_visibility="collapsed",
            placeholder="Coche LE document à interroger (un seul) — vide = tout l'index")
    with top[1]:
        st.caption(f"{ss.gen_model}")

    # Périmètre : 1 document = recherche précise et fiable ; 0 = tout l'index + avertissement.
    if len(selected) == 1:
        source_filter = selected[0]
        st.caption(f":material/check_circle: Réponses basées sur **{selected[0]}**")
    else:
        source_filter = None
        st.warning(
            ":material/warning: **Aucun document sélectionné** → recherche sur **tout l'index** "
            "(moins fiable). Pour une réponse précise et bien sourcée, **coche un document** "
            "ci-dessus.")

    # Voyant FLOTTANT (position fixe) du document actif : reste visible même quand la
    # conversation est longue (plus besoin de remonter pour savoir sur quel doc on travaille).
    if source_filter:
        _badge = (f"<div style='position:fixed;bottom:98px;right:26px;z-index:1000;"
                  f"background:#15171f;border:1px solid #2c2f3a;border-left:3px solid #22c55e;"
                  f"border-radius:10px;padding:7px 13px;font-size:0.85rem;color:#e7e8ec;"
                  f"box-shadow:0 4px 16px rgba(0,0,0,.45);max-width:330px;overflow:hidden;"
                  f"text-overflow:ellipsis;white-space:nowrap;'>"
                  f"🟢&nbsp; Document&nbsp;: <b>{source_filter}</b></div>")
    else:
        _badge = ("<div style='position:fixed;bottom:98px;right:26px;z-index:1000;"
                  "background:#15171f;border:1px solid #2c2f3a;border-left:3px solid #f59e0b;"
                  "border-radius:10px;padding:7px 13px;font-size:0.85rem;color:#e7e8ec;"
                  "box-shadow:0 4px 16px rgba(0,0,0,.45);white-space:nowrap;'>"
                  "🟠&nbsp; Aucun document — tout l'index</div>")
    st.markdown(_badge, unsafe_allow_html=True)

    try:
        col = _chunks_col()
        if source_filter:
            n = col.count_documents({"source": source_filter})
            has_g = source_filter in _graph_docs()
            st.caption(f"{n} chunks indexés · {'graphe disponible' if has_g else 'pas de graphe'}")
        else:
            st.caption(f"{col.count_documents({})} chunks indexés · {len(_graph_docs())} graphe(s)")
    except Exception:
        pass

    sess = get_session(ss.active_sid) or {}
    if sess.get("source_filter") != source_filter:
        update_session_source(ss.active_sid, source_filter)

    messages = get_messages(ss.active_sid)
    if not messages:
        st.markdown("##### Posez une question sur vos documents")
        st.caption("Recherche hybride (sémantique + mots-clés + graphe), réponses sourcées.")

    for idx, m in enumerate(messages):
        avatar = ":material/person:" if m["role"] == "user" else ":material/neurology:"
        with st.chat_message(m["role"], avatar=avatar):
            if m["role"] == "assistant" and m.get("reasoning"):
                with st.expander("Afficher le raisonnement", expanded=False):
                    st.markdown(m["reasoning"])
            st.markdown(m["content"])
            if m["role"] == "assistant":
                if m.get("citations"):
                    _render_sources(m["citations"])
                if m.get("chunks"):
                    q = (messages[idx - 1]["content"]
                         if idx > 0 and messages[idx - 1]["role"] == "user" else "")
                    _chat_chunks_for_message(m, idx, idx == len(messages) - 1, q)

    _chat_eval_ui(messages)

    # Document déposé en attente : panneau de choix des options d'ingestion + « Absorber ».
    _pending_upload_panel()
    # Question en attente (document staté ou en cours d'absorption) : bulle utilisateur,
    # pour que l'utilisateur voie que sa question est prise en compte et attend le document.
    if (_ingesting or _staged) and ss.get("pending_prompt"):
        with st.chat_message("user", avatar=":material/person:"):
            st.markdown(ss["pending_prompt"])
    # Activité d'ingestion (document déposé dans le chat / l'accueil) : progression repliable.
    _render_ingestion_activity()

    # Mode de traitement : Auto (le routeur choisit) ou forcé (RAG / Agent).
    _MODE_LBL = {"auto": "Auto", "rag": "RAG", "agent": "Agent"}
    _MODE_VAL = {v: k for k, v in _MODE_LBL.items()}
    tc = st.columns([2, 1])
    with tc[0]:
        choice = st.segmented_control(
            "Mode", list(_MODE_LBL.values()), default=_MODE_LBL.get(ss.chat_mode, "Auto"),
            label_visibility="collapsed",
            help="Auto : un routeur (sans appel LLM) choisit RAG ou Agent selon la question, "
                 "et décide d'une vérification pour les questions à enjeu. "
                 "RAG : réponse directe. Agent : raisonnement multi-étapes (plus lent).",
        )
        ss.chat_mode = _MODE_VAL.get(choice, "auto")
    with tc[1]:
        _options_popover()

    # PIÈCE JOINTE : bouton « Joindre » AU-DESSUS du champ. Contrairement au trombone de
    # st.chat_input (qui ne livre le fichier qu'à l'ENVOI), st.file_uploader se déclenche
    # DÈS LA SÉLECTION → l'absorption démarre tout de suite, et le champ de saisie est
    # DÉSACTIVÉ tant qu'elle tourne. C'est la vraie séparation « joindre » / « envoyer ».
    if _ingesting:
        st.button(f"Absorption de « {_INGEST.get('doc', 'document')} » en cours…",
                  icon=":material/hourglass_top:", disabled=True, use_container_width=False,
                  key="attach_busy")
    else:
        with st.popover("Joindre un document", icon=":material/attach_file:"):
            up = st.file_uploader("Document à absorber", type=_UPLOAD_TYPES,
                                  key="chat_uploader", label_visibility="collapsed")
            if up is not None:
                _sig = (up.name, up.size)
                if ss.get("_last_upload_sig") != _sig:   # n'absorbe qu'une fois par fichier
                    ss._last_upload_sig = _sig
                    _launch_ingest(up.name, up.getvalue())   # absorption IMMÉDIATE
                    st.rerun()

    _ph = ("Absorption en cours… envoi bloqué jusqu'à la fin" if _ingesting
           else "Posez une question sur le document sélectionné…")
    sub = st.chat_input(_ph, disabled=_ingesting)          # texte seul ; bloqué pdt l'absorption
    prompt = (sub.strip() or None) if isinstance(sub, str) and sub else None
    # Question éventuellement transférée de l'accueil (ou en attente).
    if prompt is None:
        prompt = ss.get("pending_prompt")

    # GATE GLOBAL — tant qu'un document est EN ATTENTE (options non confirmées) ou EN COURS
    # d'absorption, on NE RÉPOND PAS, quelle que soit la façon dont la question est arrivée
    # (même message, message séparé, suivi). Elle est mise en attente jusqu'à la fin de
    # l'ingestion (façon Claude/ChatGPT : le document doit être dans le contexte AVANT la
    # réponse). L'auto-refresh de _render_ingestion_activity relance la page → la question
    # part dès l'absorption finie.
    if prompt and (_ingesting or _staged or _ingest_failed):
        # En attente d'options, en cours d'absorption, OU absorption échouée (on ne répond
        # pas silencieusement sans le doc → l'utilisateur tranche via le panneau d'erreur).
        # On lit le SNAPSHOT (haut de view_chat), pas _INGEST en direct → pas de race.
        ss.pending_prompt = prompt
        prompt = None
    elif prompt:
        ss.pop("pending_prompt", None)      # question consommée → on répond ci-dessous

    if prompt:
        if not ss.active_sid:                       # session créée à la 1re interaction si besoin
            ss.active_sid = create_session(source_filter=source_filter)
        history = ([{"role": m["role"], "content": m["content"]} for m in messages]
                   if ss.use_memory else [])
        with st.chat_message("user", avatar=":material/person:"):
            st.markdown(prompt)
        # Routage : Auto → le routeur (sans LLM) décide ; RAG/Agent → forcé par l'utilisateur.
        from core.router import route_query, should_verify
        if ss.chat_mode == "agent":
            effective, route_reason = "agent", "mode Agent forcé"
        elif ss.chat_mode == "rag":
            effective, route_reason = "rag", "mode RAG forcé"
        else:
            d = route_query(prompt)
            effective, route_reason = d["mode"], d["reason"]

        with st.chat_message("assistant", avatar=":material/neurology:"):
            reasoning, citations, _chunks = None, [], []
            if ss.chat_mode == "auto":
                st.caption(f":material/alt_route: Routage : **{effective.upper()}** — {route_reason}")
            try:
                if effective == "agent":
                    answer, citations, reasoning, _chunks = _run_agent_ui(prompt, source_filter)
                else:
                    with st.spinner("Recherche dans les documents…"):
                        from core.ask import process_query_stream
                        gen, _chunks, citations = process_query_stream(
                            prompt, source_filter=source_filter,
                            conversation_history=history,
                            parent_child_on=ss.parent_child_on,
                            rewrite_enabled=ss.rewrite_enabled,
                            graph_rag_enabled=ss.graph_rag_enabled,
                            self_rag_enabled=ss.self_rag_enabled,
                            system_prompt=ss.system_prompt,
                        )
                    if gen is None:
                        answer = ("Je n'ai pas trouvé de passage pertinent. Reformulez la question "
                                  "ou changez le périmètre documentaire.")
                        st.markdown(answer)
                        citations = []
                    else:
                        answer = st.write_stream(gen)
                        if answer.startswith(OOS_PREFIX):
                            citations = []
                        else:
                            _render_sources(citations)
            except Exception as e:
                # Robustesse mono-poste : Ollama/Mongo coupé, timeout… → message lisible
                # au lieu d'un crash de la page.
                answer = ("Une erreur est survenue pendant la génération. Vérifie qu'Ollama et "
                          "MongoDB sont bien lancés (`python serve.py`, ou `python diagnostic.py`).")
                st.error(f"{answer}\n\n`{type(e).__name__}: {str(e)[:200]}`")
                citations, _chunks, reasoning = [], [], None

        # Vérification CIBLÉE (mode Auto + RAG + question à enjeu) : +1 appel SEULEMENT
        # quand ça compte → on ne paie pas la vérif sur le tout-venant. Pas de double
        # évaluation : si l'auto-correction (Self-RAG) est active, elle a DÉJÀ vérifié.
        ss.eval_result = None
        try:
            if (ss.chat_mode == "auto" and effective == "rag" and citations
                    and not ss.self_rag_enabled
                    and should_verify(prompt)["verify"]):
                with st.spinner("Vérification de la fidélité (question à enjeu)…"):
                    from core.evaluation import verify_answer
                    ss.eval_result = verify_answer(prompt, answer, _chunks or [])
        except Exception:
            ss.eval_result = None      # la vérif est un bonus : ne jamais bloquer la réponse
        add_message(ss.active_sid, "user", prompt)
        add_message(ss.active_sid, "assistant", answer, citations=citations or [],
                    reasoning=reasoning, chunks=[_trim_chunk(c) for c in (_chunks or [])])
        st.rerun()


# ── Vue : Documents (ingestion + exploration) ─────────────────────────────────

def _ingestion_panel():
    st.markdown("##### Ingérer un document")
    st.caption("PDF (converti via Docling) ou Markdown nettoyé. L'ingestion tourne en arrière-plan.")

    # État : en cours → progression ; terminé → résultat ; sinon → formulaire.
    if _INGEST["running"]:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=1500, key="ingest_poll")
        st.progress(min(100, _INGEST["pct"]) / 100.0, text=_INGEST["step"] or "En cours…")
        st.caption("Tu peux naviguer dans les autres vues pendant l'ingestion.")
        return

    if _INGEST["result"] is not None:
        res = _INGEST["result"]
        if res.get("status") == "success":
            st.success(res.get("message", "Ingestion terminée."))
            g = res.get("graph", {}) or {}
            enh = res.get("enhancement", {}) or {}
            rap = res.get("raptor", {}) or {}
            m = st.columns(4)
            m[0].metric("Chunks", res.get("num_chunks", "—"))
            m[1].metric("Entités", g.get("nodes", "—"))
            m[2].metric("Relations", g.get("edges", "—"))
            m[3].metric("Résumés RAPTOR", rap.get("summaries_generated", "—"))
            st.caption(
                f"Enrichis : {enh.get('chunks_enhanced', '—')} · "
                f"descriptions de tables : {enh.get('table_descriptions', '—')} · "
                f"chunks avec entités : {enh.get('chunks_with_entities', '—')}"
            )
        else:
            st.error(res.get("message", "Échec de l'ingestion."))
        if st.button("Nouvelle ingestion"):
            _INGEST["result"] = None
            st.rerun()
        return

    DOCS_OUT.mkdir(parents=True, exist_ok=True)
    DOCS_PDF.mkdir(parents=True, exist_ok=True)
    src_mode = st.segmented_control("Source", ["Uploader", "Fichier existant"],
                                    default="Uploader", label_visibility="collapsed")
    path = None
    if src_mode == "Fichier existant":
        existing = ([str(p) for ext in ("*.pdf", "*.docx", "*.pptx", "*.html")
                     for p in sorted(DOCS_PDF.glob(ext))]
                    + [str(p) for p in sorted(DOCS_OUT.glob("*.md"))])
        if existing:
            path = st.selectbox("Fichier", existing, format_func=lambda p: Path(p).name)
        else:
            st.info("Aucun fichier dans docs/PDF ou docs/out.")
    else:
        up = st.file_uploader("Fichier", type=["pdf", "docx", "pptx", "html", "md"],
                              label_visibility="collapsed")
        if up is not None:
            # Documents source → docs/PDF ; markdown déjà converti → docs/out.
            target = (DOCS_OUT / up.name) if up.name.lower().endswith(".md") else (DOCS_PDF / up.name)
            target.write_bytes(up.getvalue())
            path = str(target)

    if path and str(path).lower().endswith(".md") and Path(path).exists():
        with st.expander("Aperçu du Markdown"):
            st.markdown(Path(path).read_text(encoding="utf-8")[:4000])

    c1, c2 = st.columns(2)
    with c1:
        mode = st.segmented_control("Découpage", ["technical", "naive"], default="technical")
    with c2:
        raptor = st.toggle("Résumés RAPTOR", value=RAPTOR_SUMMARIES)
    c3, c4 = st.columns(2)
    with c3:
        nkw = st.number_input("Mots-clés / chunk", 0, 10, AUTO_KEYWORDS)
    with c4:
        nq = st.number_input("Questions / chunk", 0, 10, AUTO_QUESTIONS)
    graph_llm = st.toggle("Relations LLM du graphe (plus riche, +lent)",
                          value=GRAPH_USE_LLM_RELATIONS,
                          help="Extrait des relations typées (certifie, évalue…) par chunk. "
                               "Sinon co-occurrence seule.")
    enh_model = st.text_input(
        "Modèle d'enrichissement (vide = défaut)",
        value="",
        placeholder="ex: llama3.2:3b pour accélérer (l'enrichissement = ~85 % du temps)",
        help="L'enrichissement (mots-clés / questions / résumés) est le maillon le plus lent. "
             "Un modèle léger (llama3.2:3b) accélère nettement ; vide = REWRITER_MODEL. "
             "RAPTOR et le graphe ajoutent aussi des appels LLM : les désactiver accélère.")

    if st.button("Lancer l'ingestion", icon=":material/play_arrow:", type="primary",
                 disabled=path is None):
        _INGEST.update(running=True, pct=0, step="Démarrage…", result=None)
        threading.Thread(
            target=_run_ingest_bg,
            args=(path, int(nkw), int(nq), mode or "technical", bool(raptor), bool(graph_llm),
                  enh_model.strip()),
            daemon=True,
        ).start()
        st.rerun()


def _render_doc_explorer(src: str, key_prefix: str = "exp",
                         show_viewer: bool = True, viewer_height: int = 620):
    """Exploration d'UN document. Toujours : recherche + filtre type + NAVIGATION par chunks
    (liste + métadonnées + contenu du passage). `show_viewer=True` ajoute la VISUALISATION
    page blanche (document entier + surlignage) — activée dans l'onglet Documents, désactivée
    dans le chat (navigation seule). `key_prefix` évite les collisions de clés ; `viewer_height`
    règle la hauteur de la visualisation."""
    col = _chunks_col()
    f1, f2 = st.columns([3, 2])
    with f1:
        search = st.text_input("Recherche", key=f"{key_prefix}_search",
                               label_visibility="collapsed",
                               placeholder="Rechercher dans les chunks…")
    with f2:
        tf = st.segmented_control("Type", ["Tous", "Texte", "Tab/Fig", "Résumés"],
                                  default="Tous", key=f"{key_prefix}_tf",
                                  label_visibility="collapsed")

    q = {"source": src}
    if tf == "Texte":
        q["chunk_type"] = {"$nin": ["summary", "table", "figure", "mixed"]}
    elif tf == "Résumés":
        q["chunk_type"] = "summary"
    elif tf == "Tab/Fig":
        q["chunk_type"] = {"$in": ["table", "figure", "mixed"]}
    if search:
        q["content"] = {"$regex": re.escape(search), "$options": "i"}

    chunks = list(col.find(q).sort([("section_idx", 1), ("chunk_idx", 1)]))
    st.caption(f"{len(chunks)} chunk(s)")
    if not chunks:
        st.info("Aucun chunk avec ces filtres.")
        return

    labels = []
    for c in chunks:
        h = c.get("heading") or c.get("breadcrumb") or f"Section {c.get('section_idx', '?')}"
        tag = {"summary": "Résumé · ", "table": "Tableau · ",
               "figure": "Figure · ", "mixed": "Tab+Fig · "}.get(c.get("chunk_type"), "")
        page = f" · p.{c.get('page_number')}" if c.get("page_number") else ""
        labels.append(f"{tag}{h[:48]}{page}")

    def _chunk_meta(sel):
        st.caption(" · ".join(filter(None, [sel.get("source"),
                   f"p.{sel.get('page_number')}" if sel.get("page_number") else None,
                   sel.get("breadcrumb")])))
        for field, lbl in (("keywords_str", "Mots-clés"), ("questions_str", "Questions"),
                           ("table_description", "Description"), ("entities_str", "Entités")):
            if sel.get(field):
                st.markdown(f"**{lbl}** — {sel[field]}")

    if show_viewer:
        left, right = st.columns([1, 3], gap="medium")
        with left:
            idx = st.selectbox("Chunks", range(len(chunks)), key=f"{key_prefix}_chunk",
                               format_func=lambda i: labels[i], label_visibility="collapsed")
            sel = chunks[idx]
            st.divider()
            _chunk_meta(sel)
        with right:
            md_text = ""
            md_file = DOCS_OUT / src
            if md_file.exists():
                md_text = md_file.read_text(encoding="utf-8")
            if not md_text:
                st.info("Markdown source introuvable — affichage du contenu du chunk.")
                st.markdown(sel.get("content", ""))
            else:
                st.components.v1.html(_markdown_viewer(md_text, sel.get("content", "")),
                                      height=viewer_height, scrolling=True)
    else:
        # Chat : UNIQUEMENT la navigation par chunks (la visualisation page entière et le
        # résumé restent dans l'onglet Documents).
        idx = st.selectbox("Chunks", range(len(chunks)), key=f"{key_prefix}_chunk",
                           format_func=lambda i: labels[i], label_visibility="collapsed")
        sel = chunks[idx]
        _chunk_meta(sel)
        with st.container(border=True):
            st.markdown(sel.get("content", ""))


def _doc_summary_ui(src: str, key: str = "btn_summarize"):
    """Bouton + affichage du résumé global d'un document (réutilisable chat / Documents)."""
    if st.button("Résumer ce document", icon=":material/summarize:", key=key,
                 use_container_width=True):
        from core.summarize import summarize_document
        with st.spinner("Génération du résumé (réutilise les résumés de section)…"):
            st.session_state.doc_summary = {"src": src, **summarize_document(src)}
    _cur = st.session_state.get("doc_summary")
    if _cur and _cur.get("src") == src:
        if _cur.get("status") == "success":
            with st.container(border=True):
                st.caption(f":material/summarize: Résumé — basé sur {_cur['n']} {_cur['basis']}")
                st.markdown(_cur["summary"])
        elif _cur.get("status") == "empty":
            st.info("Matériau insuffisant pour résumer ce document.")
        else:
            st.error(f"Échec du résumé : {_cur.get('summary', '')[:200]}")


def _explore_panel():
    sources = list_sources()
    if not sources:
        st.info("Aucun chunk en base. Ingérez un document ci-dessus.")
        return
    col = _chunks_col()
    c1, c2 = st.columns([4, 2])
    with c1:
        _pre = st.session_state.pop("explore_src", None)   # présélection venue du chat (#8)
        _idx = sources.index(_pre) if _pre in sources else 0
        src = st.selectbox("Document", sources, index=_idx, key="doc_src",
                           label_visibility="collapsed")
    with c2:
        with st.popover("Vider ce document", icon=":material/delete:",
                        use_container_width=True):
            st.caption("Supprime les chunks de ce document du store Mongo. "
                       "Ré-ingère pour reconstruire l'index complet (Chroma/BM25/graphe).")
            if st.button("Confirmer la suppression", type="primary", key="confirm_del_src"):
                col.delete_many({"source": src})
                try:
                    from core.ask import clear_retrieval_caches
                    clear_retrieval_caches()
                except Exception:
                    pass
                st.rerun()

    _doc_summary_ui(src, key="btn_summarize")
    _render_doc_explorer(src, key_prefix="doc", show_viewer=True, viewer_height=900)


def _markdown_viewer(md_text: str, chunk_content: str) -> str:
    import markdown2
    clean = (chunk_content or "").strip()
    if clean.startswith("[") and "]" in clean:
        clean = clean[clean.index("]") + 1:].strip()
    hs = md_text.find(clean[:120]) if len(clean) > 20 else -1
    if hs >= 0:
        he = hs + len(clean[:120])
        marked = md_text[:hs] + '<mark id="hl">' + md_text[hs:he] + "</mark>" + md_text[he:]
    else:
        marked = md_text
    body = markdown2.markdown(marked, extras=["tables", "fenced-code-blocks", "break-on-newline"])
    return f"""<meta charset="utf-8"><style>
      /* Fond blanc EXPLICITE : sans lui, l'iframe hérite du thème sombre de Streamlit
         → texte sombre sur fond sombre = illisible (« PDF sombre »). Page propre. */
      html {{ background: #e9e9ee; }}
      body {{ font-family: Georgia, 'Times New Roman', serif; font-size: 14.5px; line-height: 1.75;
             color: #1f1f23; background: #ffffff; margin: 14px auto; max-width: 1100px;
             padding: 26px 38px; border-radius: 6px; box-shadow: 0 2px 16px rgba(0,0,0,0.18); }}
      h1,h2,h3,h4 {{ color: #111; margin-top: 1.1em; font-family: system-ui, sans-serif; }}
      h1 {{ font-size: 1.4em; border-bottom: 1px solid #eee; padding-bottom: 4px; }}
      table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
      th,td {{ border: 1px solid #ddd; padding: 5px 9px; }} th {{ background: #f5f5f5; }}
      code {{ background: #f2f2f4; padding: 1px 5px; border-radius: 4px; }}
      mark#hl {{ background: #fff3bf; padding: 6px 8px; display: block;
                border-left: 3px solid #f59f00; border-radius: 4px; }}
    </style><body>{body}
    <script>var e=document.getElementById('hl'); if(e) e.scrollIntoView({{block:'center'}});</script>
    </body>"""


def view_documents():
    st.markdown("### Documents")
    # Arrivée via « Explorer » depuis le chat → ouvrir directement sur l'Exploration.
    _default_panel = "Exploration" if st.session_state.get("explore_src") else "Ingestion"
    panel = st.segmented_control("section", ["Ingestion", "Exploration"],
                                 default=_default_panel, label_visibility="collapsed")
    st.write("")
    if panel == "Ingestion":
        _ingestion_panel()
    else:
        _explore_panel()


# ── Vue : Graphe ──────────────────────────────────────────────────────────────

def view_graph():
    st.markdown("### Graphe d'entités")
    from nlp.graph_builder import load_graph_from_mongo, graph_stats

    docs = MongoClient(MONGO_URI)[MONGO_DB]["entity_graph"].distinct("source_doc")
    if not docs:
        st.info("Aucun graphe d'entités. Ingérez un document (le graphe est construit automatiquement).")
        return
    doc = st.selectbox("Document", docs)

    @st.cache_resource(ttl=300)
    def _load(name):
        return load_graph_from_mongo(source_doc=name)

    G = _load(doc)
    if G is None or G.number_of_nodes() == 0:
        st.info("Graphe vide pour ce document.")
        return

    stt = graph_stats(G)
    rel_types = stt.get("relation_types", {})
    n_typed = sum(v for k, v in rel_types.items() if k != "co_occurrence")
    m = st.columns(4)
    m[0].metric("Entités", stt["nodes"])
    m[1].metric("Relations", stt["edges"])
    m[2].metric("Relations typées", n_typed)
    m[3].metric("Types d'entités", len(stt.get("node_types", {})))

    sub = st.segmented_control("g", ["Visualisation", "Explorer une entité", "Top entités"],
                               default="Visualisation", label_visibility="collapsed")
    if sub == "Top entités":
        rows = [{"Entité": e, "Type": G.nodes.get(e, {}).get("type", "?"), "Connexions": d}
                for e, d in sorted(G.degree(), key=lambda x: x[1], reverse=True)[:25]]
        st.dataframe(rows, use_container_width=True, hide_index=True)
        import pandas as pd
        nt = stt.get("node_types", {})
        if nt:
            st.markdown("**Répartition par type d'entité**")
            st.bar_chart(pd.DataFrame({"Nombre": nt}))
        if rel_types:
            st.markdown("**Types de relations**")
            st.bar_chart(pd.DataFrame({"Nombre": rel_types}))
        return

    if sub == "Explorer une entité":
        ent = st.selectbox("Entité", [""] + sorted(G.nodes()),
                           format_func=lambda x: "Choisir une entité…" if x == "" else x)
        if ent and ent in G.nodes():
            nd = G.nodes[ent]
            chunk_ids = nd.get("chunk_ids", set())
            cc = st.columns(3)
            cc[0].metric("Type", nd.get("type", "?"))
            cc[1].metric("Apparitions", nd.get("count", 0))
            cc[2].metric("Chunks", len(chunk_ids))
            rows = []
            for nb in sorted(set(list(G.successors(ent)) + list(G.predecessors(ent)))):
                for a, b, d in ((ent, nb, "→"), (nb, ent, "←")):
                    if G.has_edge(a, b):
                        ed = G[a][b]
                        rows.append({"Sens": d, "Entité liée": nb,
                                     "Type": G.nodes.get(nb, {}).get("type", "?"),
                                     "Relation": ed.get("relation", "co_occurrence"),
                                     "Poids": ed.get("weight", 1)})
            if rows:
                st.markdown("**Relations**")
                st.dataframe(rows, use_container_width=True, hide_index=True)
            else:
                st.info("Aucune relation pour cette entité.")
            if chunk_ids:
                with st.expander(f"Chunks contenant « {ent} » ({len(chunk_ids)})"):
                    col = _chunks_col()
                    for cid in sorted(chunk_ids):
                        cd = col.find_one({"_id": cid})
                        if cd:
                            h = cd.get("heading") or cd.get("breadcrumb") or cid
                            st.markdown(f"**{h}** — {cd.get('content', '')[:160]}…")
        return

    max_nodes = st.slider("Nœuds affichés", 20, 200, 80, step=10)
    top = sorted(G.degree(), key=lambda x: x[1], reverse=True)[:max_nodes]
    keep = {n for n, _ in top}
    colors = {"ORG": "#2563eb", "PER": "#16a34a", "LOC": "#ea580c",
              "NORM": "#9333ea", "ACRO": "#ca8a04", "MISC": "#64748b", "UNKNOWN": "#94a3b8"}
    nodes = [{"id": n, "label": n, "color": colors.get(G.nodes.get(n, {}).get("type", "UNKNOWN"), "#94a3b8"),
              "value": d, "title": f"{n} — {d} connexions"} for n, d in top]
    edges = [{"from": s, "to": t} for s, t, _ in G.edges(data=True) if s in keep and t in keep]
    html = f"""<meta charset="utf-8">
    <script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
    <div id="g" style="height:560px;border:1px solid #eee;border-radius:8px;"></div>
    <script>
      var net = new vis.Network(document.getElementById('g'),
        {{nodes: new vis.DataSet({json.dumps(nodes)}), edges: new vis.DataSet({json.dumps(edges)})}},
        {{physics:{{solver:'forceAtlas2Based',stabilization:{{iterations:120}}}},
          nodes:{{shape:'dot',scaling:{{min:6,max:36}},font:{{size:12,color:'#1f1f23'}}}},
          edges:{{color:{{color:'#cbd5e1'}},smooth:false}},
          interaction:{{hover:true}}}});
    </script>"""
    st.components.v1.html(html, height=590)


# ── Vue : Observabilité (traces) ──────────────────────────────────────────────

def _render_span(s: dict, total: float, depth: int = 0):
    dur = s.get("duration_ms") or 0
    pct = int(100 * dur / total) if total else 0
    md = {k: v for k, v in (s.get("metadata") or {}).items() if k not in ("query", "source", "mode")}
    bar = "▮" * max(0, min(20, pct // 5))
    indent = "&nbsp;" * (depth * 4)
    extra = f" · {md}" if md else ""
    st.markdown(f"{indent}`{s.get('name')}` — **{dur:.0f} ms** {bar}{extra}", unsafe_allow_html=True)
    for c in s.get("children", []):
        _render_span(c, total, depth + 1)


def view_traces():
    st.markdown("### Observabilité")
    st.caption("Traces des requêtes : chaque span chronométré (rewrite, retrieval, rerank, génération).")
    try:
        traces = list(MongoClient(MONGO_URI)[MONGO_DB]["traces"].find().sort("_id", -1).limit(20))
    except Exception as e:
        st.error(f"Traces indisponibles : {e}")
        return
    if not traces:
        st.info("Aucune trace pour l'instant. Pose une question dans le Chat.")
        return

    durs = sorted(t.get("duration_ms") or 0 for t in traces)
    m = st.columns(3)
    m[0].metric("Requêtes tracées", len(traces))
    m[1].metric("Latence médiane", f"{durs[len(durs) // 2]:.0f} ms")
    m[2].metric("Latence max", f"{durs[-1]:.0f} ms")
    st.divider()

    for t in traces:
        q = ((t.get("metadata") or {}).get("query") or "")[:60]
        oos = " · hors-scope" if (t.get("metadata") or {}).get("hors_scope") else ""
        head = f"{t.get('timestamp', '')} · {t.get('duration_ms', 0):.0f} ms · {q}{oos}"
        with st.expander(head):
            _render_span(t, total=t.get("duration_ms") or 1)


# ── Vue : Paramètres ──────────────────────────────────────────────────────────

def _reset_corpus() -> str:
    """Vide l'index documentaire local (Chroma/Qdrant + chunks/BM25/graphe Mongo) SANS
    toucher aux sessions de conversation ni aux traces. Retourne un court compte-rendu."""
    from pymongo import MongoClient
    report = []
    db = MongoClient(MONGO_URI)[MONGO_DB]
    for cn in ("chunks", "bm25_indexes", "entity_graph"):
        try:
            report.append(f"{cn}: -{db[cn].delete_many({}).deleted_count}")
        except Exception as e:
            report.append(f"{cn}: erreur ({e})")
    try:
        from retrieval.vector_store import get_vector_store
        get_vector_store().reset()
        report.append("vecteurs réinitialisés")
    except Exception as e:
        report.append(f"vecteurs: erreur ({e})")
    try:
        (_ROOT / "data" / "bm25_index.pkl").unlink(missing_ok=True)
    except Exception:
        pass
    try:
        from core.ask import clear_retrieval_caches
        clear_retrieval_caches()
    except Exception:
        pass
    return " · ".join(report)


def view_settings():
    ss = st.session_state
    st.markdown("### Paramètres")

    st.markdown("##### Comportement de recherche")
    ss.use_memory = st.toggle("Mémoire de conversation", value=ss.use_memory)
    ss.rewrite_enabled = st.toggle("Réécriture de requête (LLM)", value=ss.rewrite_enabled,
                                   help="Reformule la question avant la recherche (+latence).")
    ss.graph_rag_enabled = st.toggle("GraphRAG", value=ss.graph_rag_enabled,
                                     help="Exploite le graphe d'entités du document.")
    ss.parent_child_on = st.toggle("Contexte parent (parent-child)", value=ss.parent_child_on)

    st.divider()
    st.markdown("##### Retrieval")
    ss.num_chunks = st.slider("Nombre de chunks récupérés", 1, 50, ss.num_chunks)
    ws = st.slider("Poids recherche sémantique", 0.0, 1.0, WEIGHT_SEMANTIC, 0.1)
    wb = st.slider("Poids recherche BM25 (mots-clés)", 0.0, 1.0, WEIGHT_BM25, 0.1)
    ce = st.slider("Seuil hors-scope (cross-encoder)", 0.50, 0.60,
                   float(CE_RELEVANCE_THRESHOLD), 0.005, format="%.3f")

    st.divider()
    st.markdown("##### Modèles")
    models = _ollama_models()
    embed_opts = models or [ss.embed_model]
    gen_opts = models or [ss.gen_model]
    ss.embed_model = st.selectbox("Modèle d'embedding", embed_opts,
                                  index=embed_opts.index(ss.embed_model) if ss.embed_model in embed_opts else 0)
    ss.gen_model = st.selectbox("Modèle de génération", gen_opts,
                                index=gen_opts.index(ss.gen_model) if ss.gen_model in gen_opts else 0)
    v1, v2 = st.columns(2)
    if v1.button("Décharger le LLM (VRAM)", icon=":material/eject:", use_container_width=True):
        ok, msg = _ollama_set_keep_alive(ss.gen_model, 0)
        (st.success if ok else st.error)("Modèle déchargé." if ok else msg)
    if v2.button("Charger le LLM (VRAM)", icon=":material/bolt:", use_container_width=True):
        ok, msg = _ollama_set_keep_alive(ss.gen_model, -1)
        (st.success if ok else st.error)("Modèle chargé." if ok else msg)

    st.divider()
    st.markdown("##### Routage & magasin vectoriel")
    st.caption("Lecture seule : modèle par rôle et backend du magasin vectoriel.")
    try:
        from core.model_router import routing_table
        rt = routing_table()
        st.table({"Rôle": list(rt.keys()), "Modèle": list(rt.values())})
    except Exception as e:
        st.caption(f"Routage indisponible : {e}")
    try:
        from retrieval.vector_store import get_vector_store
        from env_config import VECTOR_STORE_BACKEND
        n = get_vector_store().count()
        st.caption(f"Magasin vectoriel : **{VECTOR_STORE_BACKEND}** · {n:,} vecteurs indexés")
    except Exception as e:
        st.caption(f"Magasin vectoriel indisponible : {e}")

    st.divider()
    st.markdown("##### Enrichissement (prochaines ingestions)")
    ec1, ec2 = st.columns(2)
    akw = ec1.number_input("Mots-clés / chunk", 0, 10, AUTO_KEYWORDS)
    aqq = ec2.number_input("Questions / chunk", 0, 10, AUTO_QUESTIONS)
    cmode = st.segmented_control("Découpage par défaut", ["technical", "naive"],
                                 default=CHUNKING_MODE if CHUNKING_MODE in ("technical", "naive") else "technical")
    rap = st.toggle("Résumés RAPTOR par défaut", value=RAPTOR_SUMMARIES)

    st.divider()
    st.markdown("##### Self-RAG")
    st.caption("Évalue la réponse et retente si le score est bas (qualité +, latence +).")
    sr_on = st.toggle("Activer le Self-RAG", value=SELF_RAG_ENABLED)
    sr_thr = st.slider("Seuil de score (sous lequel on retente)", 0.1, 0.9,
                       float(SELF_RAG_THRESHOLD), 0.05)
    sr_ret = st.number_input("Retries maximum", 1, 3, int(SELF_RAG_MAX_RETRIES))

    st.divider()
    st.markdown("##### System prompt")
    ss.system_prompt = st.text_area("System prompt", value=ss.system_prompt, height=200,
                                    label_visibility="collapsed")
    cols = st.columns(2)
    if cols[0].button("Réinitialiser le prompt", use_container_width=True):
        ss.system_prompt = DEFAULT_SYSTEM_PROMPT
        st.rerun()

    if cols[1].button("Enregistrer dans .env", icon=":material/save:", type="primary",
                      use_container_width=True):
        try:
            _save_env({
                "NUM_CHUNKS": str(ss.num_chunks),
                "EMBED_MODEL": ss.embed_model,
                "GEN_MODEL": ss.gen_model,
                "WEIGHT_SEMANTIC": str(ws),
                "WEIGHT_BM25": str(wb),
                "CE_RELEVANCE_THRESHOLD": str(ce),
                "AUTO_KEYWORDS": str(int(akw)),
                "AUTO_QUESTIONS": str(int(aqq)),
                "CHUNKING_MODE": cmode or "technical",
                "RAPTOR_SUMMARIES": str(bool(rap)).lower(),
                "SELF_RAG_ENABLED": str(bool(sr_on)).lower(),
                "SELF_RAG_THRESHOLD": str(sr_thr),
                "SELF_RAG_MAX_RETRIES": str(int(sr_ret)),
            })
            st.success("`.env` mis à jour. Redémarrez l'application pour tout appliquer.")
        except Exception as e:
            st.error(f"Erreur : {e}")

    st.divider()
    st.markdown("##### Application")
    rc = st.columns(2)
    if rc[0].button("Redémarrer l'app", icon=":material/restart_alt:", use_container_width=True):
        import os as _os
        _os.execv(sys.executable, [sys.executable, "-m", "streamlit", "run",
                                   str(_ROOT / "app" / "app.py")])
    if rc[1].button("Version d'Ollama", icon=":material/info:", use_container_width=True):
        import subprocess
        try:
            out = subprocess.run(["ollama", "--version"], capture_output=True,
                                 text=True, timeout=10).stdout
            st.code(out or "(version inconnue)")
        except Exception as e:
            st.error(str(e))

    st.divider()
    st.markdown("##### :material/warning: Zone dangereuse — corpus")
    st.caption("Réinitialise l'index documentaire (Chroma/Qdrant + chunks/BM25/graphe Mongo). "
               "N'efface PAS les conversations ni les traces. Irréversible — il faudra "
               "ré-ingérer les documents ensuite.")
    with st.popover("Réinitialiser le corpus", icon=":material/delete_forever:"):
        st.warning("Tous les documents indexés seront supprimés. Confirmer ?")
        if st.button("Oui, tout effacer", type="primary", key="reset_corpus_go"):
            rep = _reset_corpus()
            st.toast(f"Corpus réinitialisé — {rep}", icon=":material/delete_forever:")
            st.rerun()


@st.cache_data(ttl=20)
def _ollama_models() -> list[str]:
    import subprocess
    try:
        out = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=5).stdout
        names = []
        for line in out.splitlines():
            line = line.strip()
            if not line or line.lower().startswith("name"):
                continue
            names.append(line.split()[0])
        return list(dict.fromkeys(names))
    except Exception:
        return []


def _save_env(updates: dict):
    env_path = _ROOT / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    remaining = dict(updates)
    out = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            key = s.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)
    for k, v in remaining.items():
        out.append(f"{k}={v}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")


# ── Routeur ───────────────────────────────────────────────────────────────────

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
                 "avec `python serve.py` — l'ingestion et les réponses ne fonctionneront pas "
                 "tant qu'ils ne sont pas démarrés.")


_LYNX_DARK_CSS = """
<style>
/* « AI for Requirements » (LynX) ALIGNÉ sur le thème SOMBRE de l'hôte (et du graphe agraph,
   déjà sombre). On corrige seulement ce que LynX code en clair/quasi-noir, sans toucher à son
   code, et on fait RESSORTIR les zones de saisie. */
/* Réponses (LynX force un fond clair #FAFAFB) → surface sombre lisible. */
[data-testid="stChatMessage"] { background: #15171f !important; }
/* Zones de saisie : fond distinct + texte clair + bordure, pour bien ressortir. */
[data-testid="stAppViewContainer"] input, [data-testid="stAppViewContainer"] textarea,
[data-baseweb="input"] input, [data-baseweb="textarea"] textarea,
[data-baseweb="base-input"] input, [data-baseweb="select"] > div {
  background: #1b1e27 !important; color: #e7e8ec !important;
  border: 1px solid #2c2f3a !important; }
/* Textes LynX codés en quasi-noir (#111827 / #374151) → clairs sur fond sombre. */
[style*="#111827"] { color: #e7e8ec !important; }
[style*="#374151"] { color: #cbd0da !important; }
/* Couleurs de verdict / score : versions plus claires pour ressortir sur sombre. */
[style*="#15803D"] { color: #22c55e !important; }
[style*="#B45309"] { color: #f59e0b !important; }
[style*="#B91C1C"] { color: #f87171 !important; }
</style>
"""


def _load_lynx():
    """Charge l'app « AI for Requirements » (LynX, dossier ./lynx) SANS la modifier.
    Importée une fois via importlib sous un nom unique (`lynx_main`) pour éviter toute
    collision avec le package `app` de l'hôte ; son dossier est mis sur sys.path pour que
    ses `from src import …` se résolvent dans lynx/src."""
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
    On neutralise son `st.set_page_config` (déjà appelé par l'hôte) le temps du rendu, sans
    toucher au code de LynX. LynX gère sa propre navigation interne (ss.page)."""
    # LynX aligné sur le thème SOMBRE de l'hôte : on corrige ses fonds clairs / textes
    # quasi-noir et on fait ressortir les zones de saisie (réinjecté à chaque rerun).
    # Le retour vers l'accueil « AI for SSH » est intégré DANS l'UI de LynX (bouton aligné
    # à côté de son bouton « Accueil » dans la barre latérale, + sur sa page d'accueil) —
    # cf. lynx/app.py — plutôt qu'un bouton flottant en haut.
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
    elif view == "graph":
        view_graph()
    elif view == "traces":
        view_traces()
    elif view == "settings":
        view_settings()
    else:
        view_chat()


main()
