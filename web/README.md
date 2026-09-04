# Interface AI for Requirements

Front Next.js 16 de l’atelier d’ingénierie des exigences. Il expose l’import et
l’activation d’une baseline, l’exploration de l’arbre, l’analyse d’impact,
l’audit/correction LynX et le chat sourcé sur les exigences.

```bash
# depuis la racine
python serve.py

# ou uniquement le front
cd web
npm install
npm run dev
```

Interface : <http://localhost:3000>. API attendue :
<http://127.0.0.1:8000>, configurable avec `NEXT_PUBLIC_API_BASE`.

```bash
npm run lint
npm run build
```
