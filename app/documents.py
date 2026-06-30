"""Vue Documents : ingestion (file multi-documents) + exploration des passages."""
import re
from pathlib import Path

import streamlit as st

from app.common import DOCS_OUT, DOCS_PDF, list_sources, _chunks_col
from app.ingestion import (
    _render_ingest_queue, _UPLOAD_TYPES, _default_ingest_params, _enqueue_jobs, _ingest_active,
)


def _ingestion_panel():
    st.markdown("##### Ajouter des documents")
    st.caption("PDF, Word, PowerPoint, HTML ou Markdown. **Plusieurs fichiers** acceptés : ils "
               "sont indexés **l'un après l'autre** en arrière-plan. Suivez chaque document via "
               "sa barre de progression.")

    # File d'ingestion : une barre par document, dans l'ordre de lancement. Toujours
    # visible (pas de masquage du formulaire) -> on peut empiler de nouveaux documents.
    _render_ingest_queue(compact=False, key="docs_ingest")

    DOCS_OUT.mkdir(parents=True, exist_ok=True)
    DOCS_PDF.mkdir(parents=True, exist_ok=True)
    st.divider()
    src_mode = st.segmented_control("Source", ["Uploader", "Fichiers existants"],
                                    default="Uploader", label_visibility="collapsed")
    items: list[dict] = []   # {name, path} prêts à mettre en file
    if src_mode == "Fichiers existants":
        existing = ([str(p) for ext in ("*.pdf", "*.docx", "*.pptx", "*.html")
                     for p in sorted(DOCS_PDF.glob(ext))]
                    + [str(p) for p in sorted(DOCS_OUT.glob("*.md"))])
        if existing:
            chosen = st.multiselect("Fichiers", existing,
                                    format_func=lambda p: Path(p).name)
            items = [{"name": Path(p).name, "path": p} for p in chosen]
        else:
            st.info("Aucun fichier dans docs/PDF ou docs/out.")
    else:
        ups = st.file_uploader("Fichiers", type=_UPLOAD_TYPES, label_visibility="collapsed",
                               accept_multiple_files=True)
        for up in (ups or []):
            # Documents source -> docs/PDF ; markdown déjà converti -> docs/out.
            target = (DOCS_OUT / up.name) if up.name.lower().endswith(".md") else (DOCS_PDF / up.name)
            target.write_bytes(up.getvalue())
            items.append({"name": up.name, "path": str(target)})

    # Options d'ingestion PARTAGÉES par tout le lot - REPLIÉES : par défaut, on ajoute le
    # document sans rien régler. L'utilisateur avancé déplie pour ajuster.
    _d = _default_ingest_params()
    with st.expander(" Options avancées (facultatif)", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            mode = st.segmented_control("Découpage du texte", ["technical", "naive"],
                                        default=_d["mode"],
                                        help="technical : suit la hiérarchie normative "
                                             "(ANSSI/CC). naive : découpe par titres.")
        with c2:
            raptor = st.toggle("Résumés par section (RAPTOR)", value=_d["raptor"])
        c3, c4 = st.columns(2)
        with c3:
            nkw = st.number_input("Mots-clés / passage", 0, 10, _d["nkw"])
        with c4:
            nq = st.number_input("Questions / passage", 0, 10, _d["nq"])
        enh_model = st.text_input(
            "Modèle d'enrichissement (vide = défaut)",
            value="",
            placeholder="ex: llama3.2:3b pour accélérer (l'enrichissement = ~85 % du temps)",
            help="L'enrichissement (mots-clés / questions / résumés) est le maillon le plus lent. "
                 "Un modèle léger (llama3.2:3b) accélère nettement ; vide = REWRITER_MODEL. "
                 "RAPTOR ajoute aussi des appels LLM : le désactiver accélère.")

    _n = len(items)
    _label = (f"Ajouter ({_n} document{'s' if _n > 1 else ''})" if _n
              else "Ajouter les documents")
    if st.button(_label, icon=":material/play_arrow:", type="primary", disabled=_n == 0):
        params = {"nkw": int(nkw), "nq": int(nq), "mode": mode or _d["mode"],
                  "raptor": bool(raptor), "enh_model": enh_model.strip()}
        n = _enqueue_jobs(items, params)
        st.toast(f"{n} document(s) en file - indexation séquentielle en arrière-plan.")
        st.rerun()


def _render_doc_explorer(src: str, key_prefix: str = "exp",
                         show_viewer: bool = True, viewer_height: int = 620):
    """Exploration d'UN document : recherche + filtre type + navigation par passages
    (liste + métadonnées + contenu). `show_viewer=True` ajoute la visualisation page
    blanche (document entier + surlignage)."""
    col = _chunks_col()
    f1, f2 = st.columns([3, 2])
    with f1:
        search = st.text_input("Recherche", key=f"{key_prefix}_search",
                               label_visibility="collapsed",
                               placeholder="Rechercher dans le texte...")
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
        tag = {"summary": "Résumé - ", "table": "Tableau - ",
               "figure": "Figure - ", "mixed": "Tab+Fig - "}.get(c.get("chunk_type"), "")
        page = f" - p.{c.get('page_number')}" if c.get("page_number") else ""
        labels.append(f"{tag}{h[:48]}{page}")

    def _chunk_meta(sel):
        st.caption(" - ".join(filter(None, [sel.get("source"),
                   f"p.{sel.get('page_number')}" if sel.get("page_number") else None,
                   sel.get("breadcrumb")])))
        for field, lbl in (("keywords_str", "Mots-clés"), ("questions_str", "Questions"),
                           ("table_description", "Description"), ("entities_str", "Entités")):
            if sel.get(field):
                st.markdown(f"**{lbl}** - {sel[field]}")

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
                st.info("Markdown source introuvable - affichage du contenu du chunk.")
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
        with st.spinner("Génération du résumé (réutilise les résumés de section)..."):
            st.session_state.doc_summary = {"src": src, **summarize_document(src)}
    _cur = st.session_state.get("doc_summary")
    if _cur and _cur.get("src") == src:
        if _cur.get("status") == "success":
            with st.container(border=True):
                st.caption(f":material/summarize: Résumé - basé sur {_cur['n']} {_cur['basis']}")
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
        _pre = st.session_state.pop("explore_src", None)   # présélection venue du chat
        _idx = sources.index(_pre) if _pre in sources else 0
        src = st.selectbox("Document", sources, index=_idx, key="doc_src",
                           label_visibility="collapsed")
    with c2:
        with st.popover("Vider ce document", icon=":material/delete:",
                        use_container_width=True):
            st.caption("Supprime les chunks de ce document du store Mongo. "
                       "Ré-ingère pour reconstruire l'index complet (Chroma/BM25).")
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
         -> texte sombre sur fond sombre = illisible. Page propre. */
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
    # Panneau par défaut : Ingestion si on arrive via « Joindre un document » (chat/sidebar)
    # ou si une ingestion est active ; Exploration si on arrive via « Explorer » un chunk.
    if st.session_state.pop("_open_ingestion", False) or _ingest_active():
        _default_panel = "Ingestion"
    elif st.session_state.get("explore_src"):
        _default_panel = "Exploration"
    else:
        _default_panel = "Ingestion"
    panel = st.segmented_control("section", ["Ingestion", "Exploration"],
                                 default=_default_panel, label_visibility="collapsed")
    st.write("")
    if panel == "Ingestion":
        _ingestion_panel()
    else:
        _explore_panel()
