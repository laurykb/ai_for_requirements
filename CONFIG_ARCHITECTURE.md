## Architecture de Configuration

Configuration portable actuelle:

```text
.env.example (versionné, template)
        ↓
.env (local, optionnel, overrides)
        ↓
env_config.py (chargement + fallbacks + auto-détection)
```

## Règles

- `.env` reste local (non versionné).
- `.env.example` contient des valeurs sûres et portables.
- `env_config.py` charge:
  1. `.env` si présent
  2. sinon `.env.example` si présent
  3. sinon des valeurs par défaut internes

## Pourquoi ce choix

- Clonable sur GitHub sans fichier secret.
- Reproductible pour les nouveaux utilisateurs.
- Aucun chemin machine ou venv hardcodé requis.

