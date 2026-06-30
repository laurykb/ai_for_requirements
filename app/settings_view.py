"""Vues Observabilité (traces) et Paramètres (modèles, retrieval, .env, reset corpus)."""
import sys

import streamlit as st
from pymongo import MongoClient

from app.common import _ROOT, _ollama_models, _ollama_set_keep_alive
from core.llm_answer import DEFAULT_SYSTEM_PROMPT
from env_config import (
    MONGO_URI, MONGO_DB, WEIGHT_SEMANTIC, WEIGHT_BM25, CE_RELEVANCE_THRESHOLD,
    AUTO_KEYWORDS, AUTO_QUESTIONS, CHUNKING_MODE, RAPTOR_SUMMARIES,
    SELF_RAG_ENABLED, SELF_RAG_THRESHOLD, SELF_RAG_MAX_RETRIES,
)


# -- Observabilité (traces) ----------------------------------------------------

def _render_span(s: dict, total: float, depth: int = 0):
    dur = s.get("duration_ms") or 0
    pct = int(100 * dur / total) if total else 0
    md = {k: v for k, v in (s.get("metadata") or {}).items() if k not in ("query", "source", "mode")}
    bar = "|" * max(0, min(20, pct // 5))
    indent = "&nbsp;" * (depth * 4)
    extra = f" - {md}" if md else ""
    st.markdown(f"{indent}`{s.get('name')}` - **{dur:.0f} ms** {bar}{extra}", unsafe_allow_html=True)
    for c in s.get("children", []):
        _render_span(c, total, depth + 1)


def _perf_panel():
    """Performance d'inférence (mono-poste interactif) : tokens/s, time-to-first-token,
    latence - stats EXACTES renvoyées par Ollama, + VRAM live. Mesure côté serving."""
    from utils import perf
    rows = perf.snapshot()
    st.markdown("##### Performance d'inférence")
    vram = perf.gpu_memory()
    if vram:
        st.caption(" · ".join(f"GPU{i} VRAM {int(u)}/{int(t)} Mo" for i, (u, t) in enumerate(vram)))
    if not rows:
        st.caption("Aucune génération mesurée. Pose une question dans le Chat.")
        return
    agg = perf.aggregate(rows)
    m = st.columns(4)
    m[0].metric("Tokens/s (méd.)", agg["tok_per_s"] or "-")
    m[1].metric("1er token (méd.)", f"{agg['ttft_s']:.2f}s" if agg["ttft_s"] else "-")
    m[2].metric("Latence (méd.)", f"{agg['total_s']:.1f}s" if agg["total_s"] else "-")
    m[3].metric("Générations", agg["count"])
    with st.expander("Détail des dernières générations", expanded=False):
        st.dataframe(
            [{"modèle": r["model"], "tokens": r["gen_tokens"],
              "tok/s": round(r["tok_per_s"], 1) if r["tok_per_s"] else None,
              "1er token (s)": round(r["ttft_s"], 2) if r["ttft_s"] else None,
              "latence (s)": round(r["total_s"], 1)} for r in rows[:30]],
            use_container_width=True, hide_index=True)
    st.divider()


def view_traces():
    st.markdown("### Observabilité")
    _perf_panel()
    st.markdown("##### Traces des requêtes")
    st.caption("Chaque span chronométré (retrieval, rerank, génération).")
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
        oos = " - hors-scope" if (t.get("metadata") or {}).get("hors_scope") else ""
        head = f"{t.get('timestamp', '')} - {t.get('duration_ms', 0):.0f} ms - {q}{oos}"
        with st.expander(head):
            _render_span(t, total=t.get("duration_ms") or 1)


# -- Paramètres ----------------------------------------------------------------

def _reset_corpus() -> str:
    """Vide l'index documentaire local (Chroma + chunks/BM25 Mongo) SANS toucher aux
    sessions de conversation ni aux traces. Retourne un court compte-rendu."""
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
    return " - ".join(report)


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


def view_settings():
    ss = st.session_state
    st.markdown("### Paramètres")

    st.markdown("##### Comportement de recherche")
    ss.use_memory = st.toggle("Mémoire de conversation", value=ss.use_memory)
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
        from core.model_router import set_generate_model
        set_generate_model(ss.gen_model)      # active aussi le modèle à chaud (génération)
        ok, msg = _ollama_set_keep_alive(ss.gen_model, -1)
        (st.success if ok else st.error)("Modèle chargé et activé." if ok else msg)

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
        n = get_vector_store().count()
        st.caption(f"Magasin vectoriel : **ChromaDB** - {n:,} vecteurs indexés")
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
                                   str(_ROOT / "app" / "main.py")])
    if rc[1].button("Version d'Ollama", icon=":material/info:", use_container_width=True):
        import subprocess
        try:
            out = subprocess.run(["ollama", "--version"], capture_output=True,
                                 text=True, timeout=10).stdout
            st.code(out or "(version inconnue)")
        except Exception as e:
            st.error(str(e))

    st.divider()
    st.markdown("##### :material/warning: Zone dangereuse - corpus")
    st.caption("Réinitialise l'index documentaire (Chroma + chunks/BM25 Mongo). "
               "N'efface PAS les conversations ni les traces. Irréversible - il faudra "
               "ré-ingérer les documents ensuite.")
    with st.popover("Réinitialiser le corpus", icon=":material/delete_forever:"):
        st.warning("Tous les documents indexés seront supprimés. Confirmer ?")
        if st.button("Oui, tout effacer", type="primary", key="reset_corpus_go"):
            rep = _reset_corpus()
            st.toast(f"Corpus réinitialisé - {rep}", icon=":material/delete_forever:")
            st.rerun()
