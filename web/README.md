# web/ — AI for SSH (nouvelle interface)

Front **Next.js (App Router) + Tailwind, TypeScript**, en cours de migration depuis
l'interface Streamlit (`app/main.py`). Direction artistique héritée
d'AI_for_geopolitics : dark OLED, motion sobre, kit UI maison (`src/components/ui.tsx`)
— avec l'accent périwinkle propre à AI for SSH.

## État de la migration

| Étape | Contenu | État |
|---|---|---|
| 1 | Fondations (tokens, kit, layout) + page d'accueil | fait |
| 2 | Backbone FastAPI (`/health`, `/api/sources`) + orchestration `serve.py` | fait |
| 3 | Chat RAG (SSE, sources, boîte de verre) | à venir |
| 4 | Documents & ingestion | à venir |
| 5 | LynX / AI for Requirements (verdicts, boîte de verre, DAG React Flow) | à venir |
| 6 | Settings/observabilité, archivage du Streamlit dans `legacy/` | à venir |

Pendant la migration, **l'application fonctionnelle reste le Streamlit** :
`python serve.py` depuis la racine du repo.

## Lancer en local

Prérequis : Node ≥ 20 (installé via nvm : `nvm use default`).

```bash
python serve.py --web   # depuis la racine : Mongo + Ollama + API :8000 + front :3000
```

ou, front seul (sans l'API) :

```bash
bash dev.sh             # depuis web/ — équivaut à npm run dev
```

Ouvre <http://localhost:3000>. L'API est attendue sur `http://127.0.0.1:8000`
(surchargable via `NEXT_PUBLIC_API_BASE` dans `.env.local`). Première fois :
`npm install` (réseau requis une fois ; les fonts Google sont self-hostées au
build par `next/font`, rien ne sort de la machine au runtime).

## Vérifications

```bash
npm run lint
npm run build
```
