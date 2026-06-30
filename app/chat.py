"""Vue Chat : conversation, streaming, sources, sélection de documents, pièce jointe."""
import streamlit as st

from app.common import (
    DOCS_OUT, DOCS_PDF, list_sources, _chunks_col, _ollama_models, _ollama_set_keep_alive,
)
from app.ingestion import (
    _UPLOAD_TYPES, _enqueue_jobs, _default_ingest_params, _ingest_active,
)
from core.chat_sessions import (
    create_session, get_session, get_messages, add_message,
    update_session_source, replace_last_assistant_message,
)


def _options_popover():
    """Menu d'options accolé à la zone de saisie : uniquement les leviers de recherche
    (appliqués à la question courante). Les options d'ingestion se choisissent dans
    l'onglet Documents au lancement du lot. Valeurs en session_state."""
    ss = st.session_state
    with st.popover("Options", icon=":material/tune:"):
        st.markdown("**Recherche** - appliqué à vos questions")
        ss.parent_child_on = st.toggle("Contexte parent (parent-child)", value=ss.parent_child_on,
                                       help="Renvoie la section parente entière du passage trouvé.")
        ss.self_rag_enabled = st.toggle("Auto-correction (vérifie et retente)", value=ss.self_rag_enabled,
                                        help="Étend le vérificateur : si la fidélité aux sources est jugée "
                                             "faible, reformule et régénère automatiquement avant d'afficher "
                                             "(qualité +, latence +). En mode Auto, remplace la vérification a posteriori.")
        ss.use_memory = st.toggle("Mémoire de conversation", value=ss.use_memory,
                                  help="Tient compte des échanges précédents.")
        st.caption("Les options d'**ingestion** se choisissent dans l'onglet **Documents** "
                   "au lancement du lot, pas ici.")


def _render_sources(citations: list[dict]):
    if not citations:
        return
    with st.expander(f"Sources ({len(citations)})"):
        for c in citations:
            src = c.get("source", "document")
            loc = c.get("heading") or c.get("breadcrumb") or f"section {c.get('section', '?')}"
            page = f" - p. {c['page']}" if c.get("page") else ""
            st.markdown(f"`[{c.get('idx', '?')}]` {src} - {loc}{page}")


def _chat_eval_ui(messages: list):
    """Vérificateur inline de la dernière réponse (à la demande) : 1 appel LLM-as-judge
    fusionné -> 3 axes + extraits problématiques cités. Sur demande = 0 latence ajoutée
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
            with st.spinner("Vérification (fidélité aux sources)..."):
                from core.evaluation import verify_answer
                ss.eval_result = verify_answer(q, answer, chunks)
            st.rerun()

    er = ss.get("eval_result")
    if er:
        st.caption("Vérification automatique (LLM-as-judge, 0 -> 1)")
        cc = st.columns(3)
        cc[0].metric("Fidélité", f"{(er.get('faithfulness') or 0):.2f}")
        cc[1].metric("Pertinence réponse", f"{(er.get('answer_relevance') or 0):.2f}")
        cc[2].metric("Pertinence contexte", f"{(er.get('context_relevance') or 0):.2f}")
        issues = er.get("issues") or []
        if issues:
            with st.expander(f"Points à vérifier ({len(issues)})", expanded=False):
                for it in issues:
                    st.caption(f"- {it}")
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
    """Libellé d'un passage récupéré : [n] source - section - page - score."""
    meta = c.get("meta", {})
    src = meta.get("source", "document")
    loc = (meta.get("heading") or meta.get("breadcrumb")
           or (f"section {meta.get('section_idx')}" if meta.get("section_idx") is not None else ""))
    page = f" - p.{meta.get('page_number')}" if meta.get("page_number") else ""
    ce = c.get("ce_score")
    score = f" - score {ce:.2f}" if isinstance(ce, (int, float)) else ""
    return f"[{i + 1}] {src}" + (f" - {loc}" if loc else "") + page + score


_CHUNK_TYPE_LABELS = {"table": "Tableau", "figure": "Figure", "mixed": "Tableau + figure"}


def _render_chunk_detail(c: dict):
    """Affiche le contenu INTÉGRAL d'un passage + ses métadonnées d'enrichissement
    (type, mots-clés, questions auto-générées, entités) - ce que l'indexation a réellement
    associé au chunk. Les métadonnées absentes sont simplement omises."""
    meta = c.get("meta", {})
    st.markdown(c.get("doc", "") or "_(contenu vide)_")

    bits = []
    ctype = meta.get("chunk_type")
    if ctype == "summary":
        n = meta.get("summary_num_chunks")
        bits.append(f"**Type** - Résumé RAPTOR{f' ({n} chunks)' if n else ''}")
    elif ctype in _CHUNK_TYPE_LABELS:
        bits.append(f"**Type** - {_CHUNK_TYPE_LABELS[ctype]}")
    if meta.get("keywords_str"):
        bits.append(f"**Mots-clés** - {meta['keywords_str']}")
    if meta.get("questions_str"):
        bits.append(f"**Questions** - {meta['questions_str']}")
    if meta.get("entities_str"):
        bits.append(f"**Entités** - {meta['entities_str']}")
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
            with st.spinner("Régénération..."):
                from core.ask import process_query
                rep, _ch, cits = process_query(question, selected_chunks=selected,
                                               system_prompt=ss.system_prompt)
            replace_last_assistant_message(ss.active_sid, rep or "", cits or [], chunks=selected)
            ss.eval_result = None
            st.rerun()


def _run_agent_ui(prompt: str, source_filter: "str | list[str] | None"):
    """Mode Agent (ReAct) streamé. Le raisonnement (Pensée/Action/Observation) s'affiche
    dans un bloc repliable, replié à la fin ; la réponse finale se streame en dessous,
    séparée. Retourne (answer, citations, reasoning, chunks) ; les chunks sont les passages
    complets récupérés par l'agent, pour le panneau Passages."""
    from core.agent import ReActAgent
    from tools.rag_tool import run_tool as _rt

    def _scoped_runner(name, arguments):
        # L'agent respecte le périmètre documentaire choisi dans le chat.
        if source_filter and isinstance(arguments, dict) and not arguments.get("document"):
            arguments = {**arguments, "document": source_filter}
        return _rt(name, arguments)

    status = st.status("Réflexion en cours...", expanded=True)
    answer_ph = st.empty()  # la réponse finale se streame ici, SOUS le raisonnement
    parts: list[str] = []
    trace: list[str] = []
    result: dict = {}

    for ev in ReActAgent(tool_runner=_scoped_runner).run_stream(prompt):
        kind = ev.get("type")
        if kind == "thought":
            status.markdown(f"**Pensée** - {ev['text']}")
            trace.append(f"**Pensée** - {ev['text']}")
        elif kind == "action":
            q = ev["input"].get("query", "")
            status.markdown(f"&nbsp;&nbsp;-> **Recherche** `{q}`")
            trace.append(f"-> **Recherche** `{q}`")
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

    status.update(label=f"Raisonnement - {result.get('tool_calls', 0)} recherche(s) - "
                        f"{result.get('latency_s')}s",
                  state="complete", expanded=False)
    answer = result.get("answer", "")
    answer_ph.markdown(answer)  # la réponse finale, clairement distincte du raisonnement
    citations = result.get("sources", [])
    _render_sources(citations)
    return answer, citations, "\n\n".join(trace), result.get("chunks", [])


def _chat_model_control():
    """Sélecteur compact du MODÈLE DE GÉNÉRATION + bouton « Charger le modèle », dans le chat
    (mode simple comme expert). Changement À CHAUD : on pose un override runtime dans le
    model_router (effet immédiat sur les réponses suivantes) et on charge le modèle en VRAM."""
    ss = st.session_state
    from core.model_router import set_generate_model, get_generate_model
    current = ss.get("gen_model") or get_generate_model()
    with st.popover(f":material/smart_toy: Modèle : {current}"):
        models = _ollama_models() or [current]
        idx = models.index(current) if current in models else 0
        choice = st.selectbox("Modèle de génération", models, index=idx, key="chat_gen_select")
        if st.button("Charger le modèle", icon=":material/bolt:", type="primary",
                     key="chat_gen_load", use_container_width=True):
            set_generate_model(choice)
            ss.gen_model = choice
            with st.spinner(f"Chargement de « {choice} » en VRAM..."):
                ok, msg = _ollama_set_keep_alive(choice, -1)
            st.toast(f"Modèle « {choice} » chargé." if ok
                     else f"Modèle sélectionné, mais chargement VRAM : {msg}")
            st.rerun()
        st.caption("Sert aux prochaines réponses. Le chargement le garde prêt en mémoire.")


def _chat_session_header():
    """En-tête de conversation : titre renommable. L'exploration/visualisation des documents
    se fait dans l'onglet Documents (pas dans le chat, pour rester épuré)."""
    ss = st.session_state
    sid = ss.get("active_sid")
    if not sid:
        return
    from core.chat_sessions import rename_session, get_session_documents
    sess = get_session(sid) or {}
    title = sess.get("title") or "Nouvelle conversation"

    h1, h2 = st.columns([3, 2])
    with h1:
        with st.popover(f":material/edit: {title[:46]}", use_container_width=True):
            new = st.text_input("Renommer la conversation", value=title, key=f"rn_{sid}")
            if st.button("Renommer", key=f"rnb_{sid}", icon=":material/check:"):
                rename_session(sid, new)
                st.rerun()
    with h2:
        if ss.expert_mode:        # compteur technique -> expert seulement
            n_docs = len(get_session_documents(sid))
            st.caption(f":material/folder: {n_docs} document(s) en mémoire"
                       if n_docs else ":material/folder_off: Aucun document en mémoire")


def view_chat():
    ss = st.session_state
    _chat_session_header()
    # Le chat est DÉCOUPLÉ de l'ingestion : l'absorption se fait dans l'onglet Documents
    # (file séquentielle en arrière-plan). On répond toujours sur l'index existant, sans
    # blocage. Le périmètre documentaire est une sélection MANUELLE (multi-document).
    sources = list_sources()
    ss.setdefault("scope_multiselect", [])
    ss.scope_multiselect = [d for d in ss.scope_multiselect if d in sources]  # purge disparus

    # Ligne du haut : sélecteur de DOCUMENTS (multi-document) à gauche, contrôle du
    # MODÈLE de génération à droite (les deux modes).
    top = st.columns([4, 1])
    with top[0]:
        selected = st.multiselect(
            "Chercher dans", sources, key="scope_multiselect",
            label_visibility="collapsed",
            placeholder="Choisissez un ou plusieurs documents - vide = tous")
    with top[1]:
        _chat_model_control()

    # Périmètre : 1+ document(s) = recherche ciblée ; 0 = tout l'index + avertissement.
    if selected:
        source_filter = list(selected)
        if len(selected) == 1:
            st.caption(f":material/check_circle: Réponses basées sur **{selected[0]}**")
        else:
            st.caption(f":material/check_circle: Réponses basées sur **{len(selected)} documents** : "
                       + ", ".join(f"**{s}**" for s in selected))
    else:
        source_filter = None
        st.caption(
            ":material/info: Aucun document choisi -> je cherche dans **tous vos documents**. "
            "Pour cibler, cochez un ou plusieurs documents ci-dessus.")

    # Voyant FLOTTANT du document actif : utile sur de longues conversations, mais c'est un
    # overlay -> réservé au mode EXPERT pour garder le chat simple épuré.
    if ss.expert_mode and source_filter:
        _label = (source_filter[0] if len(source_filter) == 1
                  else f"{len(source_filter)} documents")
        _badge = (f"<div style='position:fixed;bottom:98px;right:26px;z-index:1000;"
                  f"background:#15171f;border:1px solid #2c2f3a;border-left:3px solid #22c55e;"
                  f"border-radius:10px;padding:7px 13px;font-size:0.85rem;color:#e7e8ec;"
                  f"box-shadow:0 4px 16px rgba(0,0,0,.45);max-width:330px;overflow:hidden;"
                  f"text-overflow:ellipsis;white-space:nowrap;'>"
                  f"&nbsp; Recherche dans&nbsp;: <b>{_label}</b></div>")
        st.markdown(_badge, unsafe_allow_html=True)

    if ss.expert_mode:
        try:
            col = _chunks_col()
            if source_filter:
                n = col.count_documents({"source": {"$in": source_filter}})
                st.caption(f"{n} chunks indexés")
            else:
                st.caption(f"{col.count_documents({})} chunks indexés")
        except Exception:
            pass

    sess = get_session(ss.active_sid) or {}
    if sess.get("source_filter") != source_filter:
        update_session_source(ss.active_sid, source_filter)

    messages = get_messages(ss.active_sid)
    if not messages:
        st.markdown("##### Posez une question sur vos documents")
        st.caption("Réponses sourcées, citant les passages de vos documents.")
        # Exemples sous forme de MENU DÉROULANT : un guide discret pour démarrer, sans
        # encombrer la page (placeholder = invite ; choisir une entrée pose la question).
        _examples = [
            "Quelles sont les exigences de chiffrement ?",
            "Quelles sont les menaces identifiées ?",
            "Quelles hypothèses sont faites sur l'environnement ?",
            "Résume les principales fonctions de sécurité.",
        ]
        _ex = st.selectbox(
            "Exemples de questions", _examples, index=None, key="ex_select",
            label_visibility="collapsed",
            placeholder=" Besoin d'inspiration ? Choisissez une question d'exemple...")
        if _ex and ss.get("_ex_handled") != _ex:
            ss._ex_handled = _ex          # garde-fou anti-redéclenchement
            ss.pending_prompt = _ex
            st.rerun()

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
                # Exploration des passages récupérés (contenu brut + régénération) :
                # détail technique -> mode EXPERT seulement.
                if ss.expert_mode and m.get("chunks"):
                    q = (messages[idx - 1]["content"]
                         if idx > 0 and messages[idx - 1]["role"] == "user" else "")
                    _chat_chunks_for_message(m, idx, idx == len(messages) - 1, q)

    # Vérification LLM-as-judge de la réponse : outil d'évaluation -> mode EXPERT seulement.
    if ss.expert_mode:
        _chat_eval_ui(messages)

    # Mode de traitement (Auto/RAG/Agent) + options de recherche : EXPERT uniquement.
    # En mode simple, on reste sur « Auto » (le routeur choisit) sans rien exposer.
    if ss.expert_mode:
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
    else:
        ss.chat_mode = "auto"

    # Pièce jointe rapide : on ajoute le document avec les options par défaut, sans poser
    # de question. L'indexation tourne en arrière-plan et le chat reste disponible.
    # Pour un lot ou des options fines, voir l'onglet Documents.
    with st.popover("Joindre un document", icon=":material/attach_file:"):
        up = st.file_uploader("Document", type=_UPLOAD_TYPES, key="chat_uploader",
                              label_visibility="collapsed")
        st.caption("Ajouté en arrière-plan, avec les réglages par défaut. Pour plusieurs "
                   "documents ou des options, ouvrez l'onglet Documents.")
        if up is not None:
            _sig = (up.name, up.size)
            if ss.get("_last_upload_sig") != _sig:   # n'ajoute qu'une fois par fichier
                ss._last_upload_sig = _sig
                DOCS_OUT.mkdir(parents=True, exist_ok=True)
                DOCS_PDF.mkdir(parents=True, exist_ok=True)
                _t = (DOCS_OUT / up.name) if up.name.lower().endswith(".md") else (DOCS_PDF / up.name)
                _t.write_bytes(up.getvalue())
                _enqueue_jobs([{"name": up.name, "path": str(_t)}], _default_ingest_params())
                st.toast(f"« {up.name} » ajouté - indexation en arrière-plan.")
                st.rerun()
        if st.button("Gérer dans l'onglet Documents", key="chat_to_docs",
                     icon=":material/description:", use_container_width=True):
            ss.view = "documents"
            ss._open_ingestion = True
            st.rerun()
    if _ingest_active():
        st.caption(":material/sync: Ajout d'un document en cours en arrière-plan "
                   "(le chat reste disponible - suivi dans l'onglet Documents).")

    sub = st.chat_input("Posez une question sur vos documents...")
    prompt = (sub.strip() or None) if isinstance(sub, str) and sub else None
    # Question éventuellement transférée de l'accueil.
    if prompt is None:
        prompt = ss.get("pending_prompt")
    if prompt:
        ss.pop("pending_prompt", None)      # question consommée -> on répond ci-dessous

    if prompt:
        if not ss.active_sid:                       # session créée à la 1re interaction si besoin
            ss.active_sid = create_session(source_filter=source_filter)
        history = ([{"role": m["role"], "content": m["content"]} for m in messages]
                   if ss.use_memory else [])
        with st.chat_message("user", avatar=":material/person:"):
            st.markdown(prompt)
        # Routage : Auto -> le routeur (sans LLM) décide ; RAG/Agent -> forcé par l'utilisateur.
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
                st.caption(f":material/alt_route: Routage : **{effective.upper()}** - {route_reason}")
            try:
                if effective == "agent":
                    answer, citations, reasoning, _chunks = _run_agent_ui(prompt, source_filter)
                else:
                    with st.spinner("Recherche dans les documents..."):
                        from core.ask import process_query_stream
                        gen, _chunks, citations = process_query_stream(
                            prompt, source_filter=source_filter,
                            conversation_history=history,
                            parent_child_on=ss.parent_child_on,
                            self_rag_enabled=ss.self_rag_enabled,
                            system_prompt=ss.system_prompt,
                        )
                    if gen is None:
                        answer = ("Je n'ai pas trouvé d'information sur ce sujet dans vos documents. "
                                  "Essayez de reformuler, ou choisissez d'autres documents à interroger.")
                        st.markdown(answer)
                        citations = []
                    else:
                        answer = st.write_stream(gen)
                        # Pas de sources hors-scope : la réponse renvoie alors citations=[].
                        if citations:
                            _render_sources(citations)
            except Exception as e:
                # Robustesse mono-poste : Ollama/Mongo coupé, timeout... -> message lisible
                # au lieu d'un crash de la page.
                answer = ("Une erreur est survenue pendant la génération. Vérifie qu'Ollama et "
                          "MongoDB sont bien lancés (`python serve.py`, ou `python diagnostic.py`).")
                st.error(f"{answer}\n\n`{type(e).__name__}: {str(e)[:200]}`")
                citations, _chunks, reasoning = [], [], None

        # Vérification CIBLÉE (mode Auto + RAG + question à enjeu) : +1 appel SEULEMENT
        # quand ça compte -> on ne paie pas la vérif sur le tout-venant. Pas de double
        # évaluation : si l'auto-correction (Self-RAG) est active, elle a DÉJÀ vérifié.
        ss.eval_result = None
        try:
            if (ss.chat_mode == "auto" and effective == "rag" and citations
                    and not ss.self_rag_enabled
                    and should_verify(prompt)["verify"]):
                with st.spinner("Vérification de la fidélité (question à enjeu)..."):
                    from core.evaluation import verify_answer
                    ss.eval_result = verify_answer(prompt, answer, _chunks or [])
        except Exception:
            ss.eval_result = None      # la vérif est un bonus : ne jamais bloquer la réponse
        add_message(ss.active_sid, "user", prompt)
        add_message(ss.active_sid, "assistant", answer, citations=citations or [],
                    reasoning=reasoning, chunks=[_trim_chunk(c) for c in (_chunks or [])])
        st.rerun()
