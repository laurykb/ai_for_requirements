# Exploiter le hardware — 100 % local, 100 % open-source

Aucune clé API, aucun cloud. Le code parle le **protocole OpenAI** (`/v1`) ; on le
pointe vers un serveur **local open-source**. Réglages par `LLM_BASE_URL`,
`LLM_MODEL`, `LLM_MAX_CONCURRENCY` (voir `src/config.py`).

Matériel cible : **2× RTX 6000 Ada (96 Go VRAM)**, Threadripper 24 threads, 503 Go RAM.

## Niveau 0 — Ollama réglé (gain immédiat, zéro changement de code)

Ollama par défaut ≈ 4 requêtes en parallèle sur **un seul** GPU. On élargit :

```bash
# à mettre dans l'environnement du service Ollama, puis redémarrer Ollama
export OLLAMA_SCHED_SPREAD=1        # répartit sur les 2 GPU
export OLLAMA_NUM_PARALLEL=16       # requêtes concurrentes par modèle
export OLLAMA_MAX_LOADED_MODELS=2
export OLLAMA_KEEP_ALIVE=30m
# puis: systemctl restart ollama   (ou relancer `ollama serve`)
```

Côté app, monter la concurrence en conséquence :

```bash
export LLM_MAX_CONCURRENCY=16
```

Bon pour le confort, mais Ollama (llama.cpp) reste orienté **un flux à la fois** :
le *continuous batching* lui manque.

## Niveau 1 — vLLM (le vrai passage à l'échelle, open-source)

vLLM fait du **continuous batching** : on envoie les 34 requêtes de l'audit d'un
coup, il les empaquette sur le GPU → utilisation quasi pleine, **5–20× le débit**
d'Ollama, et on peut servir un modèle bien plus fort sur 96 Go.

```bash
# un seul GPU, modèle 14B quantifié (rapide, déjà excellent)
docker run --gpus all -p 8000:8000 vllm/vllm-openai:latest \
  --model Qwen/Qwen2.5-14B-Instruct-AWQ --max-model-len 8192

# OU les deux GPU en tensor-parallel pour un 32B/72B (plus fort)
docker run --gpus all -p 8000:8000 vllm/vllm-openai:latest \
  --model Qwen/Qwen2.5-32B-Instruct-AWQ --tensor-parallel-size 2 --max-model-len 8192
```

Puis on bascule l'app dessus **sans toucher au code** :

```bash
export LLM_BASE_URL=http://localhost:8000/v1
export LLM_MODEL=Qwen/Qwen2.5-32B-Instruct-AWQ
export LLM_MAX_CONCURRENCY=32
streamlit run app.py
```

Alternatives équivalentes (open-source, même protocole) : **SGLang**, **TGI**.

## Pourquoi ça change tout pour LynX

L'audit global lance une requête par exigence (des dizaines à des milliers). Avec
Ollama on sérialise ; avec vLLM on sature les 2 GPU en parallèle. C'est la
différence entre « audit en plusieurs minutes » et « audit en quelques secondes »
sur un référentiel réel — sans rien envoyer hors de la machine.
