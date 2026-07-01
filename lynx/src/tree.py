"""Graphe d'exigences en mémoire (in-process, sans base de graphe externe).

La traçabilité est un **DAG** : `parent_id` est le lien de décomposition
principal, et les liens typés (DERIVE / REFINES / SATISFIES) ajoutent des arêtes
amont supplémentaires. Les primitives de navigation (parents, enfants, ancêtres,
frères, descendants) raisonnent sur l'ensemble de ces arêtes ; tous les
analyseurs en héritent.
"""

from __future__ import annotations

import copy
from typing import Dict, List, Optional

from .models import LinkType, Requirement

# Liens qui expriment une décomposition (la cible est « amont » de la source).
_DECOMP = {LinkType.DERIVE, LinkType.REFINES, LinkType.SATISFIES}


class RequirementTree:
    """Vue graphe (DAG) d'un corpus d'exigences.

    Reconstruit à partir d'une liste plate ; chaque opération de CRUD renvoie un
    *nouveau* graphe (immutabilité) pour analyser un scénario candidat sans muter
    l'état courant de l'UI.
    """

    def __init__(self, requirements: List[dict]):
        self._by_id: Dict[str, Requirement] = {}
        self._parents: Dict[str, List[str]] = {}
        self._children: Dict[str, List[str]] = {}
        for raw in requirements:
            req = raw if isinstance(raw, Requirement) else Requirement(**raw)
            if req.id in self._by_id:
                raise ValueError(f"Identifiant d'exigence dupliqué : {req.id}")
            self._by_id[req.id] = req
            self._parents.setdefault(req.id, [])
            self._children.setdefault(req.id, [])
        # Arêtes : parent_id (DERIVE principal) + liens typés de décomposition.
        for req in self._by_id.values():
            if req.parent_id and req.parent_id in self._by_id:
                self._add_edge(req.parent_id, req.id)
            for lk in req.links:
                if lk.type in _DECOMP and lk.target in self._by_id and lk.target != req.id:
                    self._add_edge(lk.target, req.id)

    def _add_edge(self, parent: str, child: str) -> None:
        if parent not in self._children[child]:  # évite les doublons
            self._parents[child].append(parent)
            self._children[parent].append(child)

    # --- lecture ----------------------------------------------------------
    def __contains__(self, req_id: str) -> bool:
        return req_id in self._by_id

    def __len__(self) -> int:
        return len(self._by_id)

    def get(self, req_id: str) -> Optional[Requirement]:
        return self._by_id.get(req_id)

    def all(self) -> List[Requirement]:
        return list(self._by_id.values())

    def to_corpus(self) -> List[dict]:
        return [r.model_dump() for r in self._by_id.values()]

    def roots(self) -> List[Requirement]:
        return [r for r in self._by_id.values() if not self._parents.get(r.id)]

    def parents(self, req_id: str) -> List[Requirement]:
        return [self._by_id[p] for p in self._parents.get(req_id, []) if p in self._by_id]

    def children(self, req_id: str) -> List[Requirement]:
        return [self._by_id[c] for c in self._children.get(req_id, []) if c in self._by_id]

    def siblings(self, req_id: str) -> List[Requirement]:
        """Frères = nœuds partageant au moins un parent (DAG), hors la cible."""
        if req_id not in self._by_id:
            return []
        parents = self._parents.get(req_id, [])
        if parents:
            seen: set[str] = set()
            out: List[Requirement] = []
            for p in parents:
                for c in self._children.get(p, []):
                    if c != req_id and c not in seen:
                        seen.add(c)
                        out.append(self._by_id[c])
            return out
        return [r for r in self.roots() if r.id != req_id]

    def ancestors(self, req_id: str) -> List[Requirement]:
        """Clôture amont (DAG), du plus proche au plus lointain, sans cycle."""
        out: List[Requirement] = []
        seen = {req_id}
        queue = list(self._parents.get(req_id, []))
        while queue:
            pid = queue.pop(0)
            if pid in seen or pid not in self._by_id:
                continue
            seen.add(pid)
            out.append(self._by_id[pid])
            queue.extend(self._parents.get(pid, []))
        return out

    def descendants(self, req_id: str) -> List[Requirement]:
        """Clôture aval (DAG), en largeur, sans cycle."""
        out: List[Requirement] = []
        seen: set[str] = set()
        queue = list(self._children.get(req_id, []))
        while queue:
            cid = queue.pop(0)
            if cid in seen or cid not in self._by_id:
                continue
            seen.add(cid)
            out.append(self._by_id[cid])
            queue.extend(self._children.get(cid, []))
        return out

    # --- écriture (renvoie un nouvel arbre) -------------------------------
    def _clone_raw(self) -> List[dict]:
        return copy.deepcopy(self.to_corpus())

    def with_updated(self, req_id: str, changes: dict) -> "RequirementTree":
        """Applique ``changes`` à l'exigence ``req_id``.

        Toutes les clés présentes dans ``changes`` sont appliquées telles quelles
        (y compris une chaîne vide pour vider un champ) ; les clés absentes ne
        sont pas touchées. C'est à l'appelant de ne mettre que ce qui change.
        """
        corpus = self._clone_raw()
        for item in corpus:
            if item["id"] == req_id:
                item.update(changes)
        return RequirementTree(corpus)

    def with_deleted(self, req_id: str) -> "RequirementTree":
        """Supprime la cible. Les enfants directs deviennent orphelins
        (parent_id conservé mais pointant vers un nœud absent)."""
        corpus = [item for item in self._clone_raw() if item["id"] != req_id]
        return RequirementTree(corpus)

    def with_added(self, requirement: dict) -> "RequirementTree":
        new_id = requirement.get("id")
        if new_id in self._by_id:
            raise ValueError(f"Identifiant déjà existant : {new_id}")
        corpus = self._clone_raw()
        corpus.append(dict(requirement))
        return RequirementTree(corpus)

    def with_link(self, child_id: str, parent_id: str, link_type) -> "RequirementTree":
        """Ajoute un lien typé (``child_id`` --link_type--> ``parent_id``).

        Le lien est stocké sur la *fille* (``child_id``), sa cible étant la *mère*
        (``parent_id``) — cohérent avec la sémantique de ``Requirement.links``.
        """
        ltype = getattr(link_type, "value", link_type)
        corpus = self._clone_raw()
        for item in corpus:
            if item["id"] == child_id:
                links = list(item.get("links") or [])
                if not any(lk.get("target") == parent_id and lk.get("type") == ltype for lk in links):
                    links.append({"type": ltype, "target": parent_id})
                item["links"] = links
        return RequirementTree(corpus)

    def with_unlink(self, child_id: str, parent_id: str, link_type=None) -> "RequirementTree":
        """Retire le(s) lien(s) typé(s) de ``child_id`` vers ``parent_id``.

        Si ``link_type`` est fourni, seul ce type est retiré ; sinon tous les liens
        vers ``parent_id`` le sont.
        """
        ltype = getattr(link_type, "value", link_type)
        corpus = self._clone_raw()
        for item in corpus:
            if item["id"] == child_id:
                item["links"] = [
                    lk for lk in (item.get("links") or [])
                    if not (lk.get("target") == parent_id
                            and (ltype is None or lk.get("type") == ltype))]
        return RequirementTree(corpus)
