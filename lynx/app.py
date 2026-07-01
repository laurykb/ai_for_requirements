"""LynX — assistant de vérification de la déclinaison d'exigences.

Écran d'accueil épuré, puis un écran graphe : on sélectionne une exigence, on
l'édite, et un système multi-agents (dont on voit la trace en direct) synthétise
en une réponse unique, streamée, si la modification est bonne ou ce qu'elle
impacte.

Lancement :  streamlit run app.py
"""

from uuid import uuid4

import streamlit as st
from streamlit_agraph import Config, Edge, Node, agraph

import threading

import json
from pathlib import Path

from src import correction, embeddings, feedback, llm, roi, store, telemetry, trace
from src.audit import audit_matrix
from src.corpus_io import load_many
from src.models import Action, ActionType, LinkType
from src.orchestrator import run_impact_analysis, stream_synthesis, verdict_label

SCORE_COLOR = lambda s: "#15803D" if s >= 80 else "#B45309" if s >= 50 else "#B91C1C"

MODELS = ["mistral-small3.2:latest", "qwen3.5:latest"]
# Couleur par niveau de déclinaison (L0 -> L5), palette distincte et sobre.
NIVEAU_COLORS = {0: "#4338CA", 1: "#2563EB", 2: "#0891B2", 3: "#059669", 4: "#D97706", 5: "#E11D48"}
NIVEAU_LABELS = {0: "L0 Besoin", 1: "L1 Fonctionnel", 2: "L2 Préliminaire",
                 3: "L3 Détaillée", 4: "L4 Réalisation", 5: "L5 Test"}
VERDICT_COLOR = {"VALIDE": "#15803D", "ATTENTION": "#B45309", "BLOQUANT": "#B91C1C"}

CSS = """
<style>
#MainMenu, header, footer {visibility: hidden;}
.block-container {max-width: 1600px; padding-top: 1.5rem; padding-bottom: 2rem;}
.stButton > button {border-radius: 8px; font-weight: 500;}
h1, h2, h3 {font-weight: 600; letter-spacing: -0.02em;}
section[data-testid="stSidebar"] {width: 300px;}
/* Réponse de LynX : grande et lisible, façon chatbot. */
.stChatMessage p, .stChatMessage li {font-size: 1.08rem; line-height: 1.7;}
.stChatMessage {background: #FAFAFB; border-radius: 14px;}
</style>
"""

HOME_CSS = """
<style>
#MainMenu, header, footer {visibility: hidden;}
section[data-testid="stSidebar"] {display: none;}
.block-container {max-width: 720px; padding-top: 12vh;}
</style>
"""


def _find(corpus, rid):
    return next((r for r in corpus if r.get("id") == rid), None)


def _lvl(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _select_node(req_id):
    st.session_state.selected = req_id
    st.session_state.verdict = None
    st.session_state.suggestion = None


def _load_eval():
    p = Path(__file__).parent / "eval" / "last_eval.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def render_value_panel():
    """Preuve de valeur — compacte."""
    with st.expander("Valeur"):
        rs = roi.stats()
        if rs.get("total"):
            st.markdown(f"**{rs['total']} défauts captés** · ~{rs['hours_saved']} h économisées")
        fb = feedback.stats()
        if fb.get("total"):
            st.caption(f"Accord : {int(fb['taux_justesse'] * 100)} % ({fb['total']})")
        ev = _load_eval()
        if ev:
            st.caption(f"Précision moteur : {int(ev['precision'] * 100)} % ({ev['cases']} cas)")
        _ts = telemetry.stats()
        if _ts.get("calls"):
            st.caption(f"{_ts['calls']} appels · {_ts['avg_latency_ms']} ms · {int(_ts['success_rate'] * 100)} %")
        if not (rs.get("total") or fb.get("total") or ev):
            st.caption("Pas encore de données.")


def _give_feedback(correct: bool):
    v = st.session_state.verdict
    if not v:
        return
    act = v.get("action")
    feedback.record(act.action_type.value if act else "?", act.target_id if act else "?",
                    v.get("verdict", ""), v.get("message", ""), correct)
    st.session_state.feedback_done = True


def _sig(action: Action) -> tuple:
    corpus_sig = tuple(sorted((r.get("id"), r.get("texte", "")) for r in st.session_state.corpus))
    link_type = str(action.link_type) if action.link_type else None
    return (action.action_type.value, action.target_id, action.new_text,
            action.link_target, link_type,
            st.session_state.deep, st.session_state.model, hash(corpus_sig))


# --------------------------------------------------------------------------
def draw_graph(corpus, impacted=None, selected=None, flagged=None):
    impacted = set(impacted or [])
    flagged = set(flagged or [])
    ids = {str(r.get("id")) for r in corpus}
    nodes, edges = [], []
    for item in corpus:
        rid = str(item.get("id") or "").strip()
        if not rid:
            continue  # pas de nœud fantôme « None »/vide
        color = NIVEAU_COLORS.get(_lvl(item.get("niveau", 0)), "#9CA3AF")
        hot = rid in impacted
        weak = rid in flagged
        is_sel = rid == selected
        # L'anneau signale l'état : sélection (noir) / impacté (or) / à revoir (rouge).
        border = "#111827" if is_sel else ("#F59E0B" if hot else ("#DC2626" if weak else color))
        emph = is_sel or hot or weak
        nodes.append(Node(id=rid, label=rid, title=item.get("texte", ""), color=color,
                          size=26 if emph else 14, shape="dot",
                          borderWidth=5 if emph else 2, borderColor=border,
                          font={"size": 13, "face": "Inter, sans-serif", "color": "#E7E8EC"}))
        pid = item.get("parent_id")
        if pid and str(pid) in ids:
            edges.append(Edge(source=str(pid), target=rid, color="#CBD5E1"))
        for lk in (item.get("links") or []):
            tgt = lk.get("target") if isinstance(lk, dict) else getattr(lk, "target", None)
            if tgt and str(tgt) in ids:  # liens typés (DAG) en pointillé clair
                edges.append(Edge(source=str(tgt), target=rid, color="#E5E7EB", dashes=True))
    config = Config(
        height=820, width=1560, directed=True,
        physics=False,                 # plus de re-simulation : le graphe ne part plus dans tous les sens
        hierarchical=True,             # disposition en arbre
        direction="UD",                # racine (L0) en haut, déclinaison vers le bas
        sortMethod="directed",         # respecte le sens des liens (vrai arbre de décision)
        shakeTowards="roots",
        levelSeparation=160,           # plus d'espace vertical entre niveaux
        nodeSpacing=210,               # plus d'espace horizontal -> les labels ne se chevauchent plus
        treeSpacing=260,
        nodeHighlightBehavior=True, highlightColor="#93C5FD",
        stabilization=True, fit=True,
    )
    return agraph(nodes=nodes, edges=edges, config=config)


def render_legend():
    chips = "".join(
        f"<span style='display:inline-flex;align-items:center;gap:4px;margin-right:10px;font-size:0.72rem;color:#9CA3AF'>"
        f"<span style='width:9px;height:9px;border-radius:50%;background:{NIVEAU_COLORS[l]};display:inline-block'></span>"
        f"L{l}</span>"
        for l in range(6))
    st.markdown(f"<div style='margin-bottom:.1rem'>{chips}</div>", unsafe_allow_html=True)


# Bandeau « pipeline » : chaque agent avec la gravité de son constat, pour saisir
# l'architecture (fan-out) et où ça coince d'un coup d'œil.
_SEV_COLOR = {"INFO": "#15803D", "WARNING": "#B45309", "BLOCKING": "#B91C1C"}
_PIPE_DET = [("Structure", "structure"), ("Alloc.", "allocation"), ("Aval", "downstream")]
_PIPE_IA = [("Pertinence", "pertinence"), ("Couvert.", "couverture"), ("Redond.", "redondance")]


def _worst_by_agent(findings):
    """Gravité la pire par analyseur (INFO < WARNING < BLOCKING)."""
    rank = {"INFO": 0, "WARNING": 1, "BLOCKING": 2}
    worst = {}
    for f in findings or []:
        a, s = f.get("analyzer"), f.get("sev")
        if s in rank and (a not in worst or rank[s] > rank[worst[a]]):
            worst[a] = s
    return worst


def _pipe_chip(label, analyzer_key, worst):
    sev = worst.get(analyzer_key)
    color = _SEV_COLOR.get(sev, "#4B5563")  # gris = non exécuté / rien à signaler
    return (f"<span style='white-space:nowrap;margin-right:11px'>"
            f"<span style='display:inline-block;width:9px;height:9px;border-radius:50%;"
            f"background:{color};margin-right:4px'></span>"
            f"<span style='font-size:.8rem;color:#C7CBD4'>{label}</span></span>")


def _render_pipeline_strip(findings, verdict):
    worst = _worst_by_agent(findings)
    det = "".join(_pipe_chip(l, k, worst) for l, k in _PIPE_DET)
    ia = "".join(_pipe_chip(l, k, worst) for l, k in _PIPE_IA)
    vcolor = VERDICT_COLOR.get(verdict, "#374151")
    st.markdown(
        f"<div style='font-size:.8rem;color:#9CA3AF;line-height:1.9;margin:.1rem 0 .4rem'>"
        f"<b style='color:#C7CBD4'>Action</b> ▸ {det}"
        f"<span style='color:#4B5563'>│</span> {ia}"
        f"▸ <b style='color:#C7CBD4'>Synthèse</b> ▸ "
        f"<span style='color:{vcolor};font-weight:700'>{verdict}</span></div>",
        unsafe_allow_html=True)


# Aide sur les types de liens : (libellé, description, est_décomposition).
# Décomposition = pèse sur l'analyse (couverture, pertinence, budget, orphelins) ;
# référence = affichée en pointillé mais hors déclinaison (cf. tree._DECOMP).
LINK_TYPE_INFO = {
    "DERIVE": ("se décline de", "Découpe une exigence mère en filles plus concrètes "
               "(ossature du cycle en V).", True),
    "REFINES": ("raffine / précise", "Ajoute de la précision à une exigence existante, "
                "sans forcément descendre d'un niveau système.", True),
    "SATISFIES": ("satisfait", "Une exigence ou une solution répond à un besoin amont.", True),
    "VERIFIES": ("vérifie", "Un moyen de preuve (essai, analyse, inspection) démontre "
                 "que l'exigence est tenue — branche montante du V.", False),
    "ALLOCATES_TO": ("alloue à", "Assigne l'exigence (souvent un budget) à un composant "
                     "ou sous-système responsable.", False),
}

_LINK_TYPE_HELP = (
    "**Liens de décomposition** — comptent dans l'analyse (couverture, pertinence, budget) :\n\n"
    "- **DERIVE** — se décline de : découpe une mère en filles concrètes\n"
    "- **REFINES** — raffine : précise une exigence existante\n"
    "- **SATISFIES** — satisfait : répond à un besoin amont\n\n"
    "**Références transverses** — affichées mais hors déclinaison :\n\n"
    "- **VERIFIES** — vérifie : un test/essai prouve l'exigence\n"
    "- **ALLOCATES_TO** — alloue à : assigne à un composant responsable"
)


def _link_type_label(t):
    """Libellé lisible dans le menu déroulant : 'DERIVE — se décline de (décomposition)'."""
    label, _desc, decomp = LINK_TYPE_INFO.get(t, (t, "", False))
    return f"{t} — {label} ({'décomposition' if decomp else 'référence'})"


def _render_suggestion(sel):
    """Aperçu d'une proposition de correction : texte réécrit + justification."""
    sug = st.session_state.get("suggestion")
    if not sug or sug.get("req_id") != sel["id"]:
        return
    if sug.get("error"):
        st.caption(f":orange[Proposition indisponible — {sug['error']}]")
        return
    st.markdown("**Proposition de correction**")
    st.markdown(f"> {sug['texte']}")
    if not sug.get("corrige_tout", True):
        st.caption(":orange[Correction partielle — certains points (couverture, redondance) "
                   "demandent une action structurelle, pas une simple réécriture.]")
    if sug.get("justification"):
        st.caption(sug["justification"])
    if sug.get("changements"):
        for c in sug["changements"]:
            st.caption(f"· {c}")
    s1, s2 = st.columns([2, 1])
    s1.button("Appliquer cette correction", type="primary", use_container_width=True,
              on_click=_cb_apply_suggestion, args=(sel["id"],),
              help="Applique le texte réécrit comme une modification — il repasse par "
                   "l'analyse d'impact pour vérifier qu'il ne casse rien en aval.")
    s2.button("Ignorer", use_container_width=True, on_click=_cb_dismiss_suggestion)


def _render_remap_panel(sel, corpus):
    """Remap : liens typés vers/depuis d'autres exigences existantes (DAG)."""
    sid = sel["id"]
    out_links = sel.get("links") or []
    incoming = [(r["id"], lk.get("type")) for r in corpus
                for lk in (r.get("links") or []) if lk.get("target") == sid]
    n = len(out_links) + len(incoming)
    with st.expander(f"Remapper — liens vers d'autres exigences ({n})"):
        if out_links or incoming:
            st.caption("Liens existants")
            for i, lk in enumerate(out_links):
                t, tg = lk.get("type"), lk.get("target")
                c1, c2 = st.columns([5, 1])
                c1.markdown(f"**{t}** → `{tg}`  ·  mère")
                c2.button("Retirer", key=f"unlink_{sid}_{i}", on_click=_cb_unlink, args=(sid, tg, t))
            for j, (cid, t) in enumerate(incoming):
                c1, c2 = st.columns([5, 1])
                c1.markdown(f"**{t}** ← `{cid}`  ·  fille")
                c2.button("Retirer", key=f"unlinkin_{sid}_{j}", on_click=_cb_unlink, args=(cid, sid, t))
            st.divider()
        others = [r["id"] for r in corpus if r["id"] != sid]
        if not others:
            st.caption("Aucune autre exigence à relier.")
            return
        st.caption("Créer un lien")
        d1, d2, d3 = st.columns(3)
        d1.selectbox("Sens", ["Rattacher à une mère (amont)", "Rattacher une fille (aval)"],
                     key=f"linkdir_{sid}",
                     help="**Amont** : l'exigence sélectionnée devient *fille* d'une autre "
                          "(on lui ajoute une mère).\n\n**Aval** : une autre exigence devient "
                          "*fille* de celle sélectionnée (on lui ajoute une fille).")
        d2.selectbox("Exigence à relier", others, key=f"linkother_{sid}",
                     help="L'autre exigence existante à rattacher.")
        d3.selectbox("Type de lien", [t.value for t in LinkType], key=f"linktype_{sid}",
                     format_func=_link_type_label, help=_LINK_TYPE_HELP)
        # Rappel dynamique du type choisi (une ligne, sans surcharger).
        sel_type = st.session_state.get(f"linktype_{sid}") or LinkType.DERIVE.value
        lbl, desc, decomp = LINK_TYPE_INFO.get(sel_type, (sel_type, "", False))
        tag = ("décomposition — pèse sur l'analyse (couverture, pertinence, budget)"
               if decomp else "référence transverse — affichée mais hors déclinaison")
        st.caption(f"**{sel_type}** · _{lbl}_ — {tag}.\n\n{desc}")
        st.button("Créer le lien", use_container_width=True, on_click=_cb_link, args=(sid,))


_ROLE_STYLE = {
    "déterministe": ("#0891B2", "Règle"),
    "embeddings": ("#0D9488", "Vectoriel"),
    "IA": ("#7C3AED", "Agent IA"),
    "synthèse": ("#B45309", "Synthèse"),
}


def _render_glassbox(v):
    """Boîte de verre unique : bandeau pipeline + échanges reçus/répondus par agent."""
    exchanges = v.get("exchanges") or []
    findings = v.get("findings") or []
    if not exchanges and not findings:
        return
    with st.expander(f"Comment LynX a raisonné — boîte de verre ({len(exchanges)} agents)"):
        _render_pipeline_strip(findings, v.get("verdict", ""))
        st.caption("Chaque agent reçoit un extrait de la matrice et rend un avis indépendant, "
                   "en parallèle ; l'agent de synthèse agrège le tout en un verdict unique.")
        for m in exchanges:
            meta = []
            if m.get("cached"):
                meta.append("cache")
            if m.get("latency_ms") is not None:
                meta.append(f"{m['latency_ms']} ms")
            if not m.get("ok", True):
                meta.append("indisponible")
            meta_txt = ("  ·  " + " · ".join(meta)) if meta else ""
            _agent_header(m.get("role"), m.get("agent", ""),
                          f" · {m.get('mission', '')}{meta_txt}")
            st.caption(f"Reçu — {m.get('input', '')}")
            # Pour la synthèse, le verdict complet est déjà affiché au-dessus : on ne le répète pas.
            if m.get("role") == "synthèse":
                st.caption("Rédige le verdict affiché ci-dessus à partir de ces avis.")
            else:
                st.markdown(m.get("output", ""))


def render_verdict():
    v = st.session_state.verdict
    if not v:
        return
    color = VERDICT_COLOR.get(v["verdict"], "#374151")
    with st.chat_message("assistant"):
        st.markdown(f"<span style='font-size:1.1rem;color:{color};font-weight:700'>{v['verdict']}</span>",
                    unsafe_allow_html=True)
        flags = []
        if not v.get("deep", True):
            flags.append("mode rapide")
        if v.get("cached"):
            flags.append("cache")
        if flags:
            st.caption(":gray[" + " · ".join(flags) + "]")
        st.markdown(v["message"])
        _render_glassbox(v)
        if st.session_state.get("feedback_done"):
            st.caption(":green[Merci, retour enregistré.]")
        else:
            fb = st.columns([1, 1, 4])
            fb[0].button("Verdict juste", key="fb_ok", on_click=_give_feedback, args=(True,))
            fb[1].button("Verdict faux", key="fb_ko", on_click=_give_feedback, args=(False,))
        if v["verdict"] == "BLOQUANT" and not v.get("forced"):
            with st.form("override"):
                why = st.text_area("Justification", height=70, label_visibility="collapsed",
                                   placeholder="Justifier pour appliquer malgré tout…")
                if st.form_submit_button("Appliquer malgré tout") and why.strip():
                    act = v["action"]
                    old = _find(st.session_state.corpus, act.target_id)
                    new_text = "" if act.action_type == ActionType.DELETE else (act.new_text or "")
                    st.session_state.corpus = v["candidate"]
                    st.session_state.audit = None
                    store.save_working(v["candidate"])
                    store.append_history(act.action_type.value, act.target_id,
                                         old.get("texte", "") if old else "", new_text,
                                         f"DÉROGATION : {why.strip()}")
                    if act.action_type == ActionType.DELETE:
                        st.session_state.selected = None
                    st.session_state.verdict = {**v, "forced": True}
                    st.rerun()


def _apply(action, candidate, verdict):
    if verdict == "BLOQUANT":
        return
    old = _find(st.session_state.corpus, action.target_id)
    old_text = old.get("texte", "") if old else ""
    new_text = "" if action.action_type == ActionType.DELETE else (action.new_text or "")
    if action.action_type in (ActionType.LINK, ActionType.UNLINK):
        lt = action.link_type.value if hasattr(action.link_type, "value") else (action.link_type or "")
        verb = "lien" if action.action_type == ActionType.LINK else "retrait lien"
        old_text = ""
        new_text = f"{verb} {lt} {action.target_id} → {action.link_target}"
    st.session_state.corpus = candidate
    st.session_state.audit = None  # l'audit ne reflète plus le corpus modifié
    store.save_working(candidate)
    store.append_history(action.action_type.value, action.target_id, old_text, new_text,
                         getattr(action, "override_rationale", "") or "")
    if action.action_type == ActionType.DELETE:
        st.session_state.selected = None
    elif action.action_type == ActionType.CREATE:
        st.session_state.selected = action.target_id


AXIS_LABELS = {
    "LIEN": "Lien manquant", "DOUBLON": "Doublon d'ID", "CYCLE": "Cycle",
    "ALLOCATION": "Allocation", "REDACTION": "Rédaction", "PERTINENCE": "Pertinence",
    "COUVERTURE": "Couverture", "REDONDANCE": "Redondance",
}


_SEV_PREFIX = {"BLOQUANT": "BLOQUANT", "WARNING": "À revoir", "INFO": "Info"}


def _agent_header(role, title, meta_txt=""):
    """Ligne d'en-tête d'un agent dans une boîte de verre : badge de rôle + titre."""
    color, badge = _ROLE_STYLE.get(role, ("#6B7280", role))
    st.markdown(
        f"<div style='margin:.6rem 0 .1rem'>"
        f"<span style='background:{color};color:#fff;font-size:.66rem;font-weight:700;"
        f"padding:1px 7px;border-radius:6px'>{badge}</span> "
        f"<b>{title}</b>"
        f"<span style='color:#9CA3AF;font-size:.75rem'>{meta_txt}</span></div>",
        unsafe_allow_html=True)


def _audit_det_agents(rep):
    """Les trois contrôles déterministes de l'audit, reconstruits depuis les constats."""
    finds = rep.findings
    structural = [f for f in finds if f.axis in ("DOUBLON", "LIEN", "CYCLE")]
    alloc = [f for f in finds if f.axis == "ALLOCATION"]
    vect = [f for f in finds if f.axis == "REDONDANCE" and f.message.startswith("Doublon probable")]
    return [
        ("déterministe", "Structure", "Doublons d'ID, liens manquants, cycles", structural),
        ("déterministe", "Allocation", "Budgets des filles vs plafond du parent", alloc),
        ("embeddings", "Doublons quasi-identiques", "Comparaison de tous les couples (vectoriel)", vect),
    ]


def _render_audit_glassbox(records, rep):
    """Boîte de verre de l'audit : pipeline + contrôles déterministes + audits IA signalés."""
    if not rep:
        return
    audits = trace.humanize_audit(records)
    flagged = [a for a in audits if a["flagged"] or not a["ok"]]
    clean = len(audits) - len(flagged)
    with st.expander(f"Comment LynX a audité — boîte de verre ({rep.n} exigences)"):
        vcolor = SCORE_COLOR(rep.score)
        st.markdown(
            f"<div style='font-size:.8rem;color:#9CA3AF;line-height:1.9;margin:.1rem 0 .4rem'>"
            f"<b style='color:#C7CBD4'>Matrice</b> ▸ Règles structurelles · Doublons vectoriels · "
            f"{len(audits)} audits IA (parallèle) ▸ <b style='color:#C7CBD4'>Score</b> ▸ "
            f"<span style='color:{vcolor};font-weight:700'>{rep.score}/100</span></div>",
            unsafe_allow_html=True)
        st.caption("L'audit combine des règles déterministes, un test vectoriel de doublons, "
                   "puis un agent IA par exigence — en parallèle.")

        # 1. Contrôles déterministes (bornés : trois agents, avec ou sans constat).
        for role, title, mission, dfinds in _audit_det_agents(rep):
            _agent_header(role, title)
            st.caption(mission)
            if dfinds:
                for f in dfinds:
                    st.markdown(f"· {f.message}")
            else:
                st.caption("Aucun problème détecté.")

        # 2. Audits IA par exigence : seulement les signalés en détail + décompte.
        if audits:
            st.markdown("**Audits IA par exigence** — détail des exigences signalées :")
            for a in flagged:
                meta = []
                if a.get("cached"):
                    meta.append("cache")
                if a.get("latency_ms") is not None:
                    meta.append(f"{a['latency_ms']} ms")
                meta_txt = ("  ·  " + " · ".join(meta)) if meta else ""
                _agent_header("IA", f"Audit de {a['req_id']}", meta_txt)
                st.caption(f"Reçu — {a['input']}")
                st.markdown(a["output"])
            if clean:
                st.caption(f"{clean} exigence(s) auditée(s) sans remarque.")


def render_audit_summary():
    rep = st.session_state.audit
    if not rep:
        return
    notable = [f for f in rep.findings if f.severity in ("BLOQUANT", "WARNING")]
    n_bloq = sum(1 for f in notable if f.severity == "BLOQUANT")
    ev = _load_eval()
    color = "#B91C1C" if n_bloq else ("#B45309" if notable else "#15803D")
    head = f"{len(notable)} à fiabiliser · {n_bloq} bloquant(s)" if notable else "Aucun point critique"
    rel = f" · précision {int(ev['precision'] * 100)} %" if ev else ""
    st.markdown(f"<span style='font-size:1.1rem;font-weight:600;color:{color}'>{head}</span>"
                f"<span style='color:#9CA3AF'>  ·  complétude {rep.score}/100{rel}</span>",
                unsafe_allow_html=True)
    if rep.findings:
        order = {"BLOQUANT": 0, "WARNING": 1, "INFO": 2}
        with st.expander(f"Détail ({len(notable)})", expanded=bool(n_bloq)):
            for f in sorted(rep.findings, key=lambda x: (order.get(x.severity, 3), x.axis)):
                st.button(f"[{_SEV_PREFIX.get(f.severity, f.severity)}] {f.req_id} · "
                          f"{AXIS_LABELS.get(f.axis, f.axis)} — {f.message}",
                          key=f"af_{f.req_id}_{f.axis}_{f.message[:12]}", use_container_width=True,
                          on_click=_select_node, args=(f.req_id,))
    _render_audit_glassbox(st.session_state.get("audit_exchanges") or [], rep)


def process_action(action: Action, candidate):
    """Exécute l'action UNE fois (appelée via action_request) et rend la réponse.

    Pas de st.rerun() ici : la trace des agents et la réponse streamée restent
    affichées, et il n'y a aucun risque de boucle.
    """
    ss = st.session_state
    ss.feedback_done = False  # nouveau verdict -> on redemande un retour
    sig = _sig(action)
    if sig in ss.cache:  # réponse déjà connue (reproductibilité) -> applique et rafraîchit
        v = ss.cache[sig]
        ss.verdict = {**v, "action": action, "candidate": candidate, "cached": True}
        _apply(action, candidate, v["verdict"])
        st.rerun()
        return

    status = st.status("Analyse multi-agents en cours…", expanded=True)

    def on_event(kind, label):
        if kind == "start":
            status.write(f"{label}…")
        elif kind == "done":
            status.write(f"{label} — terminé")

    llm.start_trace()  # boîte de verre : on capture les échanges agents<->LLM
    report = run_impact_analysis(ss.corpus, action, semantic=ss.deep, on_event=on_event)
    status.update(label="Analyse terminée", state="complete", expanded=False)

    verdict = verdict_label(report)
    color = VERDICT_COLOR.get(verdict, "#374151")
    full = ""
    with st.chat_message("assistant"):
        st.markdown(f"<span style='font-size:1.1rem;color:{color};font-weight:700'>{verdict}</span>",
                    unsafe_allow_html=True)
        holder = st.empty()
        for piece in stream_synthesis(report, action, use_llm=ss.deep):
            full += piece
            holder.markdown(full)
    records = llm.stop_trace()

    findings = [{"scope": f.scope.value, "sev": f.severity.value, "analyzer": f.analyzer,
                 "method": (f.details or {}).get("method", ""),
                 "sim": (f.details or {}).get("similarity"), "msg": f.message}
                for f in report.findings]
    v = {"verdict": verdict, "message": full, "impacted": report.impacted_ids, "deep": ss.deep,
         "findings": findings, "exchanges": trace.build_timeline(records, findings)}
    ss.cache[sig] = v
    ss.verdict = {**v, "action": action, "candidate": candidate}
    # ROI : on journalise les défauts captés tôt (shift-left vs remontée du V).
    roi.record_catches("edition", action.action_type.value, action.target_id, v["findings"])
    _apply(action, candidate, verdict)
    # Rafraîchit pour que le graphe reflète immédiatement l'ajout/suppression.
    # Sûr : action_request a déjà été remis à None, donc aucune reprise -> pas de boucle.
    st.rerun()


# --- Callbacks (déclenchés exactement une fois par clic) -------------------
def _cb_update(req_id):
    text = st.session_state.get(f"edit_{req_id}", "")
    cand = [dict(r) for r in st.session_state.corpus]
    for r in cand:
        if r["id"] == req_id:
            r["texte"] = text
    st.session_state.action_request = (Action(action_type=ActionType.UPDATE, target_id=req_id,
                                              new_text=text), cand)


def _cb_delete(req_id):
    cand = [dict(r) for r in st.session_state.corpus if r["id"] != req_id]
    st.session_state.action_request = (Action(action_type=ActionType.DELETE, target_id=req_id), cand)


def _cb_create(req_id):
    text = (st.session_state.get(f"child_{req_id}", "") or "").strip()
    if not text:
        return
    parent = _find(st.session_state.corpus, req_id)
    custom_id = (st.session_state.get(f"newid_{req_id}", "") or "").strip()
    nid = custom_id or f"REQ-NEW-{uuid4().hex[:6].upper()}"
    niveau = int(st.session_state.get(f"newlvl_{req_id}", min(_lvl(parent.get("niveau", 0)) + 1, 5)))
    domaine = parent.get("domaine") or "Général"
    cand = [dict(r) for r in st.session_state.corpus] + [{
        "id": nid, "niveau": niveau, "type": parent.get("type", "Exigence"),
        "domaine": domaine, "texte": text, "parent_id": req_id, "test_status": "PENDING"}]
    st.session_state.action_request = (Action(action_type=ActionType.CREATE, target_id=nid,
                                              new_text=text, parent_id=req_id,
                                              niveau=niveau, domaine=domaine), cand)


def _cb_link(sel_id):
    """Crée un lien typé entre deux exigences existantes (remap)."""
    ss = st.session_state
    direction = ss.get(f"linkdir_{sel_id}", "")
    other = ss.get(f"linkother_{sel_id}")
    ltype = ss.get(f"linktype_{sel_id}") or "DERIVE"
    if not other:
        return
    # amont : sel devient fille de `other` ; aval : `other` devient fille de sel.
    if direction.startswith("Rattacher à une mère"):
        child, parent = sel_id, other
    else:
        child, parent = other, sel_id
    cand = [dict(r) for r in ss.corpus]
    for r in cand:
        if r["id"] == child:
            r["links"] = list(r.get("links") or []) + [{"type": ltype, "target": parent}]
    ss.action_request = (Action(action_type=ActionType.LINK, target_id=child,
                                link_target=parent, link_type=ltype), cand)


def _cb_unlink(child_id, parent_id, ltype=None):
    """Retire un lien typé existant entre deux exigences."""
    ss = st.session_state
    cand = [dict(r) for r in ss.corpus]
    for r in cand:
        if r["id"] == child_id:
            r["links"] = [lk for lk in (r.get("links") or [])
                          if not (lk.get("target") == parent_id
                                  and (ltype is None or lk.get("type") == ltype))]
    ss.action_request = (Action(action_type=ActionType.UNLINK, target_id=child_id,
                                link_target=parent_id, link_type=ltype), cand)


def _problems_for(req_id):
    """Rassemble les problèmes connus d'une exigence (audit + dernier verdict)."""
    probs, seen = [], set()

    def _add(p):
        if p and p not in seen:
            seen.add(p)
            probs.append(p)

    rep = st.session_state.get("audit")
    if rep:
        for f in rep.findings:
            if f.req_id == req_id and f.severity != "INFO":
                _add(f"[{f.axis}] {f.message}")
    v = st.session_state.get("verdict")
    act = v.get("action") if v else None
    if act is not None and getattr(act, "target_id", None) == req_id:
        for f in v.get("findings", []):
            if f.get("sev") != "INFO":
                _add(f"[{f.get('scope')}] {f.get('msg')}")
    return probs


def _cb_suggest(req_id):
    st.session_state.suggest_request = req_id


def _cb_dismiss_suggestion():
    st.session_state.suggestion = None


def _cb_apply_suggestion(req_id):
    """Applique la correction proposée comme une action UPDATE (→ analyse d'impact)."""
    sug = st.session_state.get("suggestion")
    if not sug or sug.get("req_id") != req_id or sug.get("error"):
        return
    text = sug["texte"]
    cand = [dict(r) for r in st.session_state.corpus]
    for r in cand:
        if r["id"] == req_id:
            r["texte"] = text
    st.session_state.action_request = (Action(action_type=ActionType.UPDATE, target_id=req_id,
                                              new_text=text), cand)
    st.session_state.suggestion = None  # consommée


def _cb_audit():
    st.session_state.audit_request = True


def _cb_import():
    ups = st.session_state.get("uploader_corpus")
    if not ups:
        return
    new, _errs = load_many(ups)
    if new:
        st.session_state.corpus = new
        st.session_state.selected = None
        st.session_state.verdict = None
        st.session_state.cache = {}
        st.session_state.audit = None


def _cb_reset():
    st.session_state.corpus = store.reset_working()
    st.session_state.selected = None
    st.session_state.verdict = None
    st.session_state.cache = {}
    st.session_state.audit = None


# --------------------------------------------------------------------------
def page_home():
    st.markdown(HOME_CSS, unsafe_allow_html=True)
    st.markdown(
        "<div style='text-align:center'>"
        "<div style='font-size:4rem;font-weight:700;letter-spacing:-0.04em;color:#FFFFFF'>LynX</div>"
        "<div style='color:#aab0bd;font-size:1.15rem;margin-top:0.25rem'>"
        "Assistant de vérification de la déclinaison d'exigences</div>"
        "<div style='height:2.2rem'></div>"
        "<div style='font-size:1.45rem;color:#FFFFFF;font-weight:500'>"
        "Quelle exigence souhaitez-vous modifier&nbsp;?</div>"
        "</div>",
        unsafe_allow_html=True)
    st.write("")
    _, c, _ = st.columns([1, 2, 1])
    with c:
        if st.button("Ouvrir le graphe", type="primary", use_container_width=True):
            st.session_state.page = "graph"
            st.rerun()
        if st.button("← AI for SSH", use_container_width=True):
            st.session_state.view = "home"
            st.rerun()


def page_graph():
    st.markdown(CSS, unsafe_allow_html=True)
    ss = st.session_state
    llm.set_model(ss.model)
    corpus = ss.corpus

    with st.sidebar:
        st.markdown("### LynX")
        _nav1, _nav2 = st.columns(2)
        if _nav1.button("Accueil", use_container_width=True):
            ss.page = "home"
            st.rerun()
        if _nav2.button("AI for SSH", use_container_width=True):
            ss.view = "home"          # retour à l'accueil de l'app hôte (AI for SSH)
            st.rerun()
        ss.model = st.selectbox("Modèle", MODELS, index=MODELS.index(ss.model) if ss.model in MODELS else 0)
        ss.deep = st.checkbox("Analyse IA", value=ss.deep)
        if not ss.deep:
            st.caption(":orange[Mode rapide, sans IA]")
        elif not llm.llm_available():
            st.caption(":red[LLM injoignable]")
        st.button("Auditer la matrice", type="primary", use_container_width=True, on_click=_cb_audit,
                  help="Passe toute la matrice au crible et rend un **score de fiabilité /100** "
                       "avec la liste des points à corriger.\n\n"
                       "1. Règles déterministes : liens manquants, doublons d'ID, cycles, "
                       "dépassements de budget.\n"
                       "2. Test vectoriel : doublons quasi-identiques (embeddings).\n"
                       "3. Un agent IA par exigence : rédaction, pertinence, couverture, redondance.")
        with st.expander(f"Corpus · {len(corpus)}"):
            st.file_uploader("Importer (JSON)", type=["json"], accept_multiple_files=True,
                             key="uploader_corpus", label_visibility="collapsed")
            st.button("Importer", use_container_width=True, on_click=_cb_import)
            st.button("Réinitialiser", use_container_width=True, on_click=_cb_reset)
        render_value_panel()

    # Audit demandé (callback) : exécuté UNE fois ici, avec avancement en direct.
    if ss.audit_request:
        ss.audit_request = False
        status = st.status("Audit de la matrice en cours…", expanded=True)
        status.write("Vérifications structurelles, allocation et doublons (embeddings)…")

        def _audit_ev(done, total):
            status.update(label=f"Audit de la matrice — analyse sémantique {done}/{total}")

        llm.start_trace()  # boîte de verre : capture des audits IA par exigence
        ss.audit = audit_matrix(ss.corpus, deep=ss.deep, on_event=_audit_ev)
        ss.audit_exchanges = llm.stop_trace()
        status.update(label=f"Audit terminé — score {ss.audit.score}/100", state="complete", expanded=False)

    # --- Graphe (pleine largeur, en haut) ---
    render_audit_summary()
    render_legend()
    flagged = ss.audit.flagged_ids if ss.audit else None
    clicked = draw_graph(corpus, impacted=(ss.verdict or {}).get("impacted"),
                         selected=ss.selected, flagged=flagged)
    # Le clic sur un nœud déclenche déjà un rerun ; on met juste à jour l'état.
    if clicked and isinstance(clicked, str) and clicked != ss.selected:
        ss.selected, ss.verdict, ss.suggestion = clicked, None, None

    st.divider()

    # --- Éditeur (pleine largeur, sous le graphe) ---
    sel = _find(corpus, ss.selected) if ss.selected else None
    if not sel:
        st.info("Cliquez une exigence dans le graphe pour l'éditer, la supprimer ou y ajouter une fille.")
    else:
        # Proposition de correction (déclenchée par bouton, générée UNE fois ici).
        if ss.get("suggest_request") == sel["id"]:
            ss.suggest_request = None
            with st.status("Génération d'une proposition de correction…", expanded=False):
                ss.suggestion = {**correction.suggest_correction(
                    corpus, sel["id"], _problems_for(sel["id"])), "req_id": sel["id"]}
        ed, meta = st.columns([3, 1], gap="large")
        with meta:
            st.markdown(f"**{sel['id']}**")
            st.caption(f"Niveau {sel.get('niveau', 0)} · {sel.get('domaine') or '—'}")
            hist = store.get_history(sel["id"])
            if hist:
                with st.expander(f"Historique ({len(hist)})"):
                    for h in hist:
                        st.caption(f"{h['timestamp']} · {h['action']}")
                        if h.get("rationale"):
                            st.caption(h["rationale"])
        with ed:
            st.text_area("Exigence", value=sel.get("texte", ""), height=120,
                         key=f"edit_{sel['id']}", label_visibility="collapsed")
            b1, b2 = st.columns([2, 1])
            b1.button("Appliquer la modification", type="primary", use_container_width=True,
                      on_click=_cb_update, args=(sel["id"],))
            b2.button("Supprimer", use_container_width=True,
                      on_click=_cb_delete, args=(sel["id"],))
            # La réécriture n'est proposée que si l'exigence est signalée (WARNING/BLOQUANT) :
            # inutile — et risqué — de réécrire une exigence déjà saine. On ne la lance jamais
            # automatiquement ni en masse (l'audit ne fait que détecter), pour maîtriser la latence.
            problems = _problems_for(sel["id"])
            if problems:
                st.button("Proposer une correction (IA)", use_container_width=True,
                          on_click=_cb_suggest, args=(sel["id"],),
                          help="Génère une réécriture corrigeant les problèmes détectés "
                               "(rédaction, cohérence amont), à sens technique constant. "
                               "Vous la relisez avant de l'appliquer ; elle repasse alors "
                               "par l'analyse d'impact.")
                _render_suggestion(sel)
            with st.expander("Ajouter une exigence enfant"):
                a1, a2 = st.columns([2, 1])
                a1.text_input("Identifiant", key=f"newid_{sel['id']}",
                              placeholder="laisser vide = auto")
                a2.number_input("Niveau", min_value=0, max_value=5,
                                value=min(_lvl(sel.get("niveau", 0)) + 1, 5),
                                key=f"newlvl_{sel['id']}")
                st.text_area("Nouvelle exigence", height=90, key=f"child_{sel['id']}",
                             label_visibility="collapsed", placeholder="Texte de la nouvelle exigence…")
                st.button("Appliquer l'ajout", use_container_width=True,
                          on_click=_cb_create, args=(sel["id"],))
            _render_remap_panel(sel, corpus)

    # Traitement de l'action demandée : EXACTEMENT une fois, puis on efface.
    req = ss.action_request
    if req:
        ss.action_request = None
        process_action(*req)
    else:
        render_verdict()


def main():
    st.set_page_config(page_title="LynX", layout="wide")
    ss = st.session_state
    ss.setdefault("page", "home")
    ss.setdefault("selected", None)
    ss.setdefault("verdict", None)
    ss.setdefault("deep", True)
    ss.setdefault("model", MODELS[0])
    ss.setdefault("cache", {})
    ss.setdefault("audit", None)
    ss.setdefault("action_request", None)
    ss.setdefault("audit_request", False)
    ss.setdefault("feedback_done", False)
    ss.setdefault("suggestion", None)
    ss.setdefault("suggest_request", None)
    # Préchauffage des modèles (embeddings + LLM) en arrière-plan : supprime le
    # coût « à froid » de la première analyse, sans bloquer le rendu.
    if not ss.get("warmed"):
        ss.warmed = True
        def _warm():
            try:
                embeddings.get_embedding("préchauffage")
            except Exception:
                pass
            try:
                llm.call_agent('Réponds en JSON {"ok": true}.', "ping")
            except Exception:
                pass
        threading.Thread(target=_warm, daemon=True).start()
    if "corpus" not in ss:
        ss.corpus = store.load_initial()

    if not ss.corpus:
        st.markdown(CSS, unsafe_allow_html=True)
        st.warning("Aucun corpus. Placez un fichier dans corpus/corpus.json.")
        return

    if ss.page == "home":
        page_home()
    else:
        page_graph()


if __name__ == "__main__":
    main()
