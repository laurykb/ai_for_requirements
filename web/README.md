# web/ — AI for SSH (interface cible)

Front **Next.js (App Router) + Tailwind, TypeScript**, servi par défaut avec
`python serve.py`. Il regroupe les interfaces RAG et LynX autour du kit partagé
`src/components/ui.tsx`. L’ancienne interface Streamlit a été retirée ; le front
Next.js est l’unique interface maintenue.

## Lancer en local

Prérequis : Node ≥ 20 (installé via nvm : `nvm use default`).

```bash
python serve.py   # depuis la racine : Mongo + Ollama + API :8000 + front :3000
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
