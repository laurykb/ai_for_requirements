"""Résolution HyPE à la requête : un hit sur un vecteur-question est remonté vers
son chunk parent, avec déduplication (meilleur rang/score conservé). Pur, offline."""


def resolve_hype_hits(ranked_ids, lookup):
    new_ranked = []
    new_lookup = {}
    for cid in ranked_ids:
        entry = lookup.get(cid) or {}
        meta = dict(entry.get("meta") or {})
        if meta.get("chunk_type") == "hype_question":
            pid = meta.get("parent_id") or meta.get("id") or cid
            meta["chunk_type"] = "chunk"          # normalise pour l'aval (parent-child, etc.)
            meta.pop("parent_id", None)
        else:
            pid = cid
        cand = {"doc": entry.get("doc", ""), "meta": meta, "distance": entry.get("distance")}
        if pid not in new_lookup:
            new_ranked.append(pid)
            new_lookup[pid] = cand
        else:
            # dedup : garde la meilleure distance (plus petite) ; l'ordre reste au 1er hit.
            prev = new_lookup[pid].get("distance")
            cur = cand.get("distance")
            if cur is not None and (prev is None or cur < prev):
                new_lookup[pid]["distance"] = cur
    return new_ranked, new_lookup
