"""Boîte de verre : rend lisibles les échanges entre agents.

LynX n'est pas une chaîne où les agents se parlent : c'est un **fan-out**.
L'orchestrateur envoie à chaque agent (analyseur) un extrait de la matrice,
chacun rend un avis *indépendant*, puis un agent de **synthèse** agrège tous les
avis en un verdict unique. Ce module transforme les échanges bruts — le payload
JSON envoyé à chaque agent LLM et la réponse reçue, capturés par ``llm`` — en
messages en langage naturel, pour les afficher dans l'UI.
"""

from __future__ import annotations

from typing import Any, List

# Nom lisible + rôle + mission de chaque agent, par nom de skill.
SKILL_META = {
    "coherence_pertinence": ("Agent Pertinence amont (T1)", "IA",
                             "Vérifie que l'exigence reste cohérente avec ses ancêtres"),
    "couverture_amont": ("Agent Couverture (T2)", "IA",
                         "Vérifie que le parent reste couvert par ses filles"),
    "redondance_surspec": ("Agent Redondance (T3)", "IA",
                           "Compare l'exigence à ses sœurs (doublon / sur-spécification)"),
    "coherence_pertinence_aval": ("Agent Pertinence aval (T4)", "IA",
                                  "Vérifie que l'exigence reste cohérente avec ses filles (déclinaison)"),
    "impact_latent": ("Agent Impact latent", "IA",
                      "Repère les exigences non reliées mais sémantiquement impactées par la modif"),
    "coherence_coreference": ("Agent Co-références", "IA",
                              "Vérifie la cohérence entre exigences partageant un référent concret"),
    "routeur_embeddings": ("Routeur Redondance (embeddings)", "embeddings",
                           "Pré-filtre vectoriel : ne dérange le LLM que dans la zone ambiguë"),
    "routeur_impact_latent": ("Routeur Impact latent (embeddings)", "embeddings",
                              "Pré-filtre vectoriel : sélectionne les exigences proches non reliées"),
    "routeur_coreference": ("Routeur Co-références (référents)", "déterministe",
                            "Repère les exigences partageant un référent concret (acronyme, code, unité)"),
    "synthese_message": ("Agent Synthèse", "synthèse",
                         "Agrège tous les avis en un verdict unique"),
    "synthese_impact": ("Agent Synthèse", "synthèse",
                        "Agrège tous les avis en un verdict unique"),
}

# Agents déterministes (pas d'appel LLM) : reconstruits depuis leurs constats.
DET_META = {
    "allocation": ("Agent Allocation", "déterministe",
                   "Somme les budgets des filles et la compare au plafond du parent"),
    "downstream": ("Agent Propagation aval", "déterministe",
                   "Repère les descendants impactés (orphelins, re-test)"),
    "structure": ("Agent Structure", "déterministe",
                  "Valide l'action elle-même (collision d'ID, niveau, cible)"),
}


def _oui_non(v: Any) -> str:
    if v is True:
        return "oui"
    if v is False:
        return "non"
    return "n.c."


def _short_node(node: Any) -> str:
    if not isinstance(node, dict):
        return f"`{node}`"
    txt = (node.get("texte") or "").strip()
    if len(txt) > 100:
        txt = txt[:100] + "…"
    ident = node.get("id", "?")
    return f"`{ident}` « {txt} »" if txt else f"`{ident}`"


def _id_list(items: Any) -> str:
    out: List[str] = []
    for it in items or []:
        if isinstance(it, dict):
            out.append(str(it.get("id", "?")))
        else:
            out.append(str(it))
    return ", ".join(f"`{i}`" for i in out) if out else "—"


def _fmt_input(skill: str, payload: Any) -> str:
    """Décrit, en français, ce que l'orchestrateur a envoyé à l'agent."""
    if not isinstance(payload, dict):
        return str(payload)
    if skill == "coherence_pertinence":
        cible = _short_node(payload.get("exigence_cible"))
        chaine = _id_list(payload.get("chaine_amont"))
        return (f"« Voici l'exigence {cible}. Sa chaîne d'ancêtres est {chaine}. "
                f"Reste-t-elle cohérente et pertinente vis-à-vis d'eux ? »")
    if skill == "couverture_amont":
        parent = _short_node(payload.get("exigence_parent"))
        filles = _id_list(payload.get("exigences_filles"))
        return (f"« Voici le parent {parent} et ses filles {filles}. "
                f"Le parent reste-t-il entièrement couvert par elles ? »")
    if skill == "redondance_surspec":
        cible = _short_node(payload.get("exigence_cible"))
        soeurs = _id_list(payload.get("exigences_soeurs"))
        return (f"« Voici l'exigence {cible} et ses sœurs {soeurs}. "
                f"Est-elle redondante ou sur-spécifiée par rapport à elles ? »")
    if skill == "coherence_pertinence_aval":
        cible = _short_node(payload.get("exigence_cible"))
        filles = _id_list(payload.get("exigences_filles"))
        return (f"« Voici l'exigence {cible} et ses filles {filles}. "
                f"Restent-elles cohérentes et pertinentes vis-à-vis d'elle (déclinaison) ? »")
    if skill == "impact_latent":
        cible = _short_node(payload.get("exigence_modifiee"))
        proches = _id_list(payload.get("exigences_proches"))
        return (f"« Voici l'exigence modifiée {cible} et des exigences NON reliées mais proches "
                f"{proches}. Lesquelles sont réellement impactées et à relire ? »")
    if skill == "coherence_coreference":
        cible = _short_node(payload.get("exigence_cible"))
        refs = _id_list(payload.get("co_references"))
        return (f"« Voici l'exigence {cible} et des exigences partageant un référent concret "
                f"{refs}. Restent-elles mutuellement cohérentes ? »")
    if skill == "routeur_impact_latent":
        return (f"« Sélectionne, parmi les exigences non reliées à `{payload.get('cible', '?')}`, "
                f"les plus proches sémantiquement. »")
    if skill == "routeur_coreference":
        refs = ", ".join(payload.get("referents") or []) or "—"
        return (f"« Cherche les exigences partageant un référent concret avec "
                f"`{payload.get('cible', '?')}` (référents : {refs}). »")
    if skill == "routeur_embeddings":
        return (f"« Compare le vecteur de `{payload.get('cible', '?')}` à ses "
                f"{payload.get('n_soeurs', 0)} sœur(s). »")
    if skill in ("synthese_message", "synthese_impact"):
        action = payload.get("action", {}) or {}
        constats = payload.get("constats", []) or []
        lignes = [f"« Action : {action.get('type', '?')} sur "
                  f"`{action.get('exigence', '?')}`. Statut global : "
                  f"{payload.get('statut_global', '?')}. Avis reçus des agents :"]
        for c in constats:
            lignes.append(f"   — [{c.get('axe', '?')}/{c.get('gravite', '?')}] {c.get('message', '')}")
        lignes.append("Rédige une synthèse unique pour l'ingénieur. »")
        return "\n".join(lignes)
    return str(payload)


def _fmt_output(skill: str, out: Any) -> str:
    """Décrit, en français, la réponse rendue par l'agent."""
    if not isinstance(out, dict):
        return str(out)
    if out.get("error"):
        return f"Indisponible ({out.get('error')})."
    preuve = (out.get("preuve") or "").strip()
    preuve_txt = f" Preuve citée : « {preuve} »." if preuve else ""
    if skill == "coherence_pertinence":
        base = (f"Cohérent : **{_oui_non(out.get('est_coherent'))}**. "
                f"{out.get('synthese', '')}")
        rupt = out.get("rupture_avec")
        if rupt:
            base += f" Rupture avec {_id_list(rupt)}."
        return base + preuve_txt
    if skill == "couverture_amont":
        base = (f"Parent couvert : **{_oui_non(out.get('est_complet'))}**. "
                f"{out.get('synthese', '')}")
        gaps = out.get("concepts_non_couverts")
        if gaps:
            base += f" Concepts non couverts : {', '.join(map(str, gaps))}."
        return base + preuve_txt
    if skill == "redondance_surspec":
        base = (f"Redondante : **{_oui_non(out.get('est_redondante'))}** · "
                f"sur-spécifiée : **{_oui_non(out.get('est_sur_specifiee'))}**. "
                f"{out.get('synthese', '')}")
        conf = out.get("soeurs_en_conflit")
        if conf:
            base += f" Sœurs en conflit : {_id_list(conf)}."
        return base + preuve_txt
    if skill == "coherence_pertinence_aval":
        base = (f"Cohérent avec ses filles : **{_oui_non(out.get('est_coherent'))}**. "
                f"{out.get('synthese', '')}")
        rupt = out.get("rupture_avec")
        if rupt:
            base += f" Rupture avec {_id_list(rupt)}."
        return base + preuve_txt
    if skill == "impact_latent":
        impactees = out.get("impactees") or []
        base = out.get("synthese", "") or (f"{len(impactees)} exigence(s) latente(s) impactée(s)."
                                           if impactees else "Aucun impact latent.")
        if impactees:
            base += f" À relire : {_id_list(impactees)}."
        return base + preuve_txt
    if skill == "coherence_coreference":
        base = (f"Cohérent : **{_oui_non(out.get('coherent'))}**. {out.get('synthese', '')}")
        conf = out.get("conflits")
        if conf:
            base += f" En conflit : {_id_list(conf)}."
        return base + preuve_txt
    if skill == "routeur_impact_latent":
        if out.get("note"):
            return f"Aucune exigence retenue — {out['note']}."
        return (f"Exigences proches retenues : {_id_list(out.get('retenus'))} "
                f"(similarités {out.get('similarites', '')}).")
    if skill == "routeur_coreference":
        if out.get("note"):
            return f"Aucune co-référence — {out['note']}."
        return f"Exigences partageant un référent : {_id_list(out.get('co_references'))}."
    if skill == "routeur_embeddings":
        return (f"Sœur la plus proche : `{out.get('plus_proche', '?')}` "
                f"(similarité {out.get('similarite', '?')}). "
                f"Décision : {out.get('decision', '')}")
    if skill in ("synthese_message", "synthese_impact"):
        return out.get("message", "")
    return str(out)


def humanize(records: List[dict]) -> List[dict]:
    """Convertit les échanges LLM bruts en messages lisibles (un par appel)."""
    msgs: List[dict] = []
    for r in records or []:
        raw_label = r.get("label") or ""
        skill = raw_label.split("#")[0]
        is_vote = "#vote" in raw_label
        name, role, mission = SKILL_META.get(skill, (skill or "Agent", "IA", ""))
        if is_vote:
            name += " · re-vérification (vote)"
        msgs.append({
            "agent": name,
            "role": role,
            "mission": mission,
            "input": _fmt_input(skill, r.get("input")),
            "output": _fmt_output(skill, r.get("output")),
            "latency_ms": r.get("latency_ms"),
            "cached": bool(r.get("cached")),
            "ok": bool(r.get("ok", True)),
        })
    return msgs


def audit_is_flagged(out: Any) -> bool:
    """Vrai si l'audit d'une exigence a relevé au moins un problème."""
    if not isinstance(out, dict) or out.get("error"):
        return False
    red = out.get("redaction") or {}
    per = out.get("pertinence") or {}
    cov = out.get("couverture") or {}
    rdd = out.get("redondance") or {}
    pav = out.get("pertinence_aval") or {}
    return (red.get("conforme") is False or per.get("coherent") is False
            or cov.get("complet") is False or rdd.get("redondant") is True
            or pav.get("coherent") is False)


def _fmt_audit_input(payload: Any) -> str:
    if not isinstance(payload, dict):
        return str(payload)
    ex = payload.get("exigence") or {}
    ctx = []
    parent = payload.get("parent")
    if parent:
        ctx.append(f"parent `{parent.get('id', '?')}`")
    ctx.append(f"{len(payload.get('soeurs') or [])} sœur(s)")
    ctx.append(f"{len(payload.get('filles') or [])} fille(s)")
    return f"« Audite {_short_node(ex)} ({', '.join(ctx)}). »"


def _fmt_audit_output(out: Any) -> str:
    if not isinstance(out, dict):
        return str(out)
    if out.get("error"):
        return f"Non audité ({out.get('error')})."
    parts: List[str] = []
    red = out.get("redaction") or {}
    if red.get("conforme") is False:
        parts.append(f"Rédaction : {red.get('probleme') or 'non conforme'}")
    per = out.get("pertinence") or {}
    if per.get("coherent") is False:
        parts.append(f"Pertinence : {per.get('probleme') or 'incohérence avec le parent'}")
    cov = out.get("couverture") or {}
    if cov.get("complet") is False:
        manques = ", ".join(map(str, cov.get("manques") or [])) or "concepts manquants"
        parts.append(f"Couverture : {manques}")
    rdd = out.get("redondance") or {}
    if rdd.get("redondant") is True:
        avec = ", ".join(map(str, rdd.get("avec") or [])) or "une sœur"
        parts.append(f"Redondance : doublon avec {avec}")
    pav = out.get("pertinence_aval") or {}
    if pav.get("coherent") is False:
        avec = ", ".join(map(str, pav.get("avec") or [])) or "une fille"
        parts.append(f"Pertinence aval : incohérence avec {avec}")
    if not parts:
        return "Conforme sur tous les axes (rédaction, pertinence, couverture, redondance, pertinence aval)."
    return " · ".join(parts)


def humanize_audit(records: List[dict]) -> List[dict]:
    """Rend lisibles les audits par exigence (appels ``audit_exigence``)."""
    out: List[dict] = []
    for r in records or []:
        if (r.get("label") or "").split("#")[0] != "audit_exigence":
            continue
        payload = r.get("input") or {}
        res = r.get("output") or {}
        out.append({
            "req_id": (payload.get("exigence") or {}).get("id", "?"),
            "input": _fmt_audit_input(payload),
            "output": _fmt_audit_output(res),
            "flagged": audit_is_flagged(res),
            "ok": not (isinstance(res, dict) and res.get("error")),
            "latency_ms": r.get("latency_ms"),
            "cached": bool(r.get("cached")),
        })
    return out


def build_timeline(records: List[dict], findings: List[dict]) -> List[dict]:
    """Timeline complète : agents déterministes + agents IA + synthèse.

    ``findings`` : constats au format UI (``{analyzer, scope, sev, msg, ...}``),
    utilisés pour représenter les agents déterministes qui ne passent pas par le
    LLM (donc absents de ``records``).
    """
    timeline: List[dict] = []
    # 1. Agents déterministes, reconstruits depuis leurs constats.
    for key, (name, role, mission) in DET_META.items():
        finds = [f for f in (findings or []) if f.get("analyzer") == key]
        if not finds:
            continue
        timeline.append({
            "agent": name, "role": role, "mission": mission,
            "input": "« Voici la matrice candidate (après action). Applique ta règle. »",
            "output": " ".join(f.get("msg", "") for f in finds),
            "latency_ms": None, "cached": False, "ok": True,
        })
    # 2. Agents IA + 3. synthèse (ordre : IA puis synthèse en dernier).
    llm_msgs = humanize(records)
    ia = [m for m in llm_msgs if m["role"] != "synthèse"]
    synth = [m for m in llm_msgs if m["role"] == "synthèse"]
    timeline.extend(ia)
    timeline.extend(synth)
    return timeline
