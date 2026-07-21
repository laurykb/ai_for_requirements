# Orchestration multi-agent : flux de données, états, mémoire, formats

Le dépôt contient **deux orchestrations multi-agent de paradigmes opposés**. Les documenter
ensemble clarifie les choix de conception (et ce qui est, ou non, un « vrai » multi-agent).

| | **RAG — agent ReAct** (`core/agent.py`) | **LynX — analyseurs parallèles** (`lynx/src/orchestrator.py`) |
|---|---|---|
| Contrôle du flux | **le LLM décide** l'étape suivante | **le code décide** (orchestration fixe) |
| Topologie | **séquentielle** (boucle Pensée→Action→Observation) | **fan-out parallèle** puis agrégation |
| « Agents » | 1 agent + 1 outil (`rag_search`) | 8 analyseurs spécialisés (2 déterministes, 6 LLM) |
| Sortie | réponse rédigée + citations | verdict unique (VALIDE/ATTENTION/BLOQUANT) |

> Honnêteté : le RAG est un **agent + outil**, pas un essaim. LynX est un **pipeline
> d'analyseurs**. Le mot « multi-agent » recouvre ici deux patterns distincts ; ce qui compte
> est l'orchestration et le flux de données, traités ci-dessous.

Principe commun aux deux : **« le modèle propose, le code dispose ».** Un LLM ne fait jamais
qu'émettre une intention ou un constat ; c'est toujours du **code déterministe** qui valide,
exécute et agrège.

---

## 1. RAG — l'agent ReAct (séquentiel, piloté par le LLM)

### Création
Un agent mono-outil (`ReActAgent`) au-dessus d'un **contrat d'outil** standard
(`tools/rag_tool.py` : nom + description en langage naturel + schéma JSON des paramètres).
Le même contrat sert l'agent ET le serveur MCP. On l'instancie avec un `tool_runner`
(l'exécuteur réel) et un `system_prompt` qui décrit le protocole ReAct.

### Orchestration de la logique
Boucle bornée par `max_iterations` (`core/agent.py:run`). À chaque tour :
1. `prompt = system_prompt + question + scratchpad` → appel LLM.
2. On **parse** la complétion : `Pensée:` / `Action:` + `Action Input:` / `Réponse finale:`.
3. Si `Réponse finale` → on sort. Sinon, l'`Action Input` (JSON) est **validé et exécuté par
   le code** (`run_tool` vérifie le nom et les arguments — le modèle n'exécute rien).
4. Le résultat de l'outil est reformaté en **Observation** compacte, ré-injectée dans le
   scratchpad. On reboucle.

### Gestion des états
L'état d'un run vit dans des variables locales explicites (pas de framework) :
- `scratchpad` (str) : **mémoire de travail** — l'historique Pensée/Action/Observation, ré-
  envoyé au LLM à chaque tour. C'est *là* que se construit le raisonnement.
- `steps` (list) : journal structuré (pour l'UI / la trace).
- `sources` : **registre GLOBAL de citations** (index `[1] [2]…` croissant sur tous les appels).
- `seen_calls` : déduplication des appels identiques — les petits modèles relancent 3-4× la
  même recherche ; on coupe court et on synthétise au lieu de gâcher des itérations.

### Mémoire
- **Intra-run** : le scratchpad (ci-dessus).
- **Inter-tours (conversation)** : l'historique de chat (`core/chat_sessions`) est passé à la
  génération, séparément de l'agent.

### Formatage des données qui circulent
C'est le point le plus soigné :
- **Agent → outil** : `Action Input` = **JSON** parsé puis validé contre le schéma (`run_tool`).
- **Outil → agent** : en mode `passages`, l'outil renvoie un dict structuré avec deux niveaux
  distincts (`tools/rag_tool.py`) :
  - `passages` : texte **tronqué à 1000 caractères** → c'est ce que voit le LLM (on ne noie pas
    son contexte) ;
  - `chunks` : passages **intégraux** → gardés à part pour l'UI et les citations, **jamais
    réinjectés au LLM**.
- **Observation** : texte compact numéroté dans le registre global → l'agent peut citer `[n]`.
- **Synthèse finale** : en retrieval agentique, une **unique génération ancrée** sur tous les
  passages cumulés (fiable + citée), plutôt que le texte libre du raisonnement.

---

## 2. LynX — les analyseurs parallèles (déterministe, piloté par le code)

### Création
Huit **analyseurs spécialisés** (`lynx/src/analyzers.py`), chacun avec un rôle :
structure & allocation (déterministes), pertinence amont / couverture du parent / redondance /
pertinence aval / impact latent / co-références (LLM), propagation aval (déterministe). Les
analyseurs trans-matrice (impact latent, co-références) sont pré-filtrés par embeddings/référents
pour ne solliciter le LLM que sur la zone utile. Les prompts des analyseurs LLM sont des **fichiers
markdown** (`lynx/skills/*.md`) — la prompt-engineering est séparée du code.

### Orchestration de la logique
`run_impact_analysis` (`lynx/src/orchestrator.py`) :
1. Construit l'**arbre candidat** (applique l'action à une *copie* de l'arbre).
2. Lance les analyseurs **déterministes** en séquence (instantanés, sans LLM).
3. Lance les analyseurs **LLM en parallèle** (`ThreadPoolExecutor`) et **remonte chaque
   résultat dès qu'il arrive** (`as_completed`), avec un callback `on_event` pour la trace live.
4. Agrège tous les `Finding` dans un `ImpactReport`, calcule le **statut global** (le plus
   sévère l'emporte).
5. **Synthèse** : un appel LLM (`synthese_impact`) résume l'ensemble en un verdict + message,
   streamé token par token.

### Gestion des états
- `RequirementTree` **immutable** : `with_updated` / `with_deleted` / `with_added` renvoient un
  **nouvel** arbre → l'arbre candidat ne mute jamais l'original (analyse sans effet de bord).
- `Ctx` : l'état partagé passé à chaque analyseur = `(arbre courant, arbre candidat, action)`.
- `ImpactReport` : `findings` + `global_status` (recalculé), + `narrative`.

### Mémoire
- **Cache de reproductibilité** (`lynx/src/llm.py`) : clé `(modèle, prompt, entrée)` → même
  verdict pour la même action (les verdicts LLM redeviennent déterministes).
- **Self-consistency** optionnelle (`LLM_VOTE`) : plusieurs votes, on garde le majoritaire.

### Formatage des données qui circulent
- **Modèles typés pydantic** (`lynx/src/models.py`) : `Action`, `Finding`
  (analyzer / scope / severity / message / impacted_ids / details), `ImpactReport`. Le **type**
  est le contrat entre analyseurs — pas du texte libre.
- **Analyseur → orchestrateur** : chaque analyseur renvoie `List[Finding]` ; isolation des
  erreurs (`_safe` : un analyseur en échec produit un Finding INFO, ne bloque pas les autres).
- **Orchestrateur → synthèse** : un **payload structuré** (`_synthesis_payload`) — action +
  statut + liste de constats `{axe, gravité, message}` — passé au skill markdown puis au LLM.
- **Client LLM** (`lynx/src/llm.py`) : `call_skill` renvoie un **dict JSON** parsé (ou
  `{"error": …}`), `stream_agent` un flux de tokens. Compatible OpenAI (`/v1`), dégradation
  propre si le LLM est indisponible (les analyseurs déterministes continuent).

---

## 3. Principes de conception communs (à retenir)

1. **Le code dispose, le modèle propose** : toute exécution (outil, application d'action,
   agrégation) est déterministe et testable ; le LLM ne fait qu'émettre une intention/un constat.
2. **Contrat de données explicite** : JSON validé (RAG) ou modèles pydantic (LynX) entre les
   maillons — jamais « le LLM parse le texte de l'autre LLM ».
3. **Séparer ce que voit le LLM de ce que garde le système** : passages tronqués vs chunks
   intégraux (RAG) ; payload de synthèse vs arbre complet (LynX).
4. **Dégradation propre** : LLM indisponible → repli déterministe (LynX), garde-fou de réponse
   honnête (RAG) ; un agent en échec n'effondre pas l'orchestration.
5. **Bornes** : `max_iterations` (RAG), déduplication des appels, parallélisme borné au nombre
   d'analyseurs (LynX).
6. **État explicite, pas de framework** : variables locales nommées (RAG) ou arbre immutable +
   `Ctx` (LynX). Relisable, sans magie d'orchestration.
