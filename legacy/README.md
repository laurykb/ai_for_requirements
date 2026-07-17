# legacy/

Code conservé mais **plus au premier plan**. Rien ici n'est mort : c'est
l'ancienne interface, gardée le temps que le front cible atteigne la parité.

## `legacy/app/` — interface Streamlit historique du RAG

L'UI Streamlit unifiée (« AI for SSH ») qui embarque aussi LynX via
`_load_lynx()`. Elle reste **fonctionnelle** et sert de filet tant que le front
Next.js (`web/`) + API FastAPI (`api/`) n'a pas atteint la parité complète.

Lancement :

```bash
python serve.py --streamlit     # depuis la racine du dépôt
```

`legacy/app/main.py` remet lui-même la racine du dépôt **et** `legacy/` sur
`sys.path`, donc aucun réglage d'environnement n'est nécessaire.

## Pourquoi « legacy » et pas supprimé ?

La migration vers Next.js n'est pas terminée : quelques fonctions du RAG et de
LynX n'ont pas encore leur équivalent dans le front `web/` (sessions de
conversation persistées, mode Agent ReAct + routage, régénération avec
sélection de passages, création d'exigence fille côté UI, feedback/ROI,
sélecteur de modèle). Tant que la parité n'est pas atteinte, cette UI reste la
référence fonctionnelle. Elle sera retirée quand `web/` la couvrira entièrement.
