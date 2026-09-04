# AI for SSH hors ligne — guide unique

Le livrable contient quatre images Docker (frontend, API, MongoDB et Ollama),
tous les modèles Ollama et les données présents sur la machine source, la
configuration Compose, les répertoires persistants et leurs sommes SHA-256.

## Prérequis

Matériel cible validé : AMD Ryzen Threadripper PRO 7945WX, 503 Gio de RAM et deux NVIDIA RTX 6000 Ada de 48 Gio. Le runtime Docker `nvidia` est obligatoire et contrôlé automatiquement. Ollama répartit les modèles sur les deux GPU.

- source : Linux x86-64, Docker/Compose, Ollama avec les modèles, environnement
  Python du projet et MongoDB accessible sur localhost:27017 ;
- cible hors ligne : Ubuntu 24.04 x86-64 avec Docker/Compose, Ollama, Python 3 et VS Code ;
- support de transfert d'au moins 150 Gio ;
- disque local cible avec au moins 200 Gio libres.

Node.js, npm, MongoDB et les dépendances Python sont inclus dans les images.

## 1. Préparer sur la machine connectée

```bash
bash deploy/offline.sh prepare /chemin/vers/ai-for-ssh
```

La destination doit être absente. Le script valide l'application, construit les
images, sauvegarde `ragdb_export`, copie les données et le magasin complet des
modèles retournés par `ollama list`, produit `images.tar`, audite le contenu
et crée `SHA256SUMS`. Pour un emplacement atypique, définir
`OLLAMA_MODELS_DIR=/chemin/vers/models`.

## 2. Installer et lancer sur la machine hors ligne

Copier d'abord le paquet du support de transfert vers le disque local. Cette
étape évite les limitations de Docker installé via Snap sur `/media` :

```bash
cp -a /media/$USER/AI_FOR_SSH/ai-for-ssh "$HOME/ai-for-ssh"
cd "$HOME/ai-for-ssh"
bash deploy/offline.sh verify
python3 serve.py
bash deploy/offline.sh smoke
```

Au premier lancement, `serve.py` charge les images, restaure MongoDB et monte
directement la copie fidèle du magasin Ollama. Aux lancements
suivants, la même commande redémarre l'application. Sur la cible, ouvrir
`http://localhost:3000`, puis exécuter `smoke` pour vérifier toutes les routes
et tous les services.

Depuis un autre poste du réseau, ouvrir :

```text
http://ADRESSE_IP_DE_LA_CIBLE:3000
```

L'adresse de la cible s'obtient avec `hostname -I`. Le pare-feu local doit
autoriser le port TCP choisi. Par défaut, l'interface écoute sur toutes les
interfaces (`WEB_BIND_ADDRESS=0.0.0.0`).

L'installation vérifie les sommes, charge les quatre images, initialise les
volumes Ollama et MongoDB, puis démarre les images déjà chargées. Aucune commande
`pull`, `pip`, `npm` ou `ollama pull` n'est exécutée sur la cible.

Pour faire cohabiter deux éditions, modifier `.env` avant `install` :

```dotenv
COMPOSE_PROJECT_NAME=ai-for-ssh-export
WEB_PORT=3001
```

## Garanties du déploiement hors ligne

- Exécuter le paquet depuis un disque local (le répertoire personnel est recommandé), jamais directement depuis `/media` ou `/mnt` avec Docker Snap.
- Aucun accès Internet et aucune commande `apt`, `pip`, `npm`, `docker pull` ou `ollama pull` ne sont nécessaires sur la cible.
- Les modèles ne sont pas téléchargés par `serve.py` : leur magasin complet est embarqué, monté en lecture seule puis vérifié par `ollama list`. Les 12 modèles peuvent être sélectionnés et chargés depuis l'interface.
- Le cross-encoder `bge-reranker-v2-m3` est embarqué localement et la recette `smoke` vérifie son chargement réel sur CPU.
- La première installation recrée uniquement le volume MongoDB du projet, puis restaure `data/mongo.archive`. Cela évite toute réutilisation accidentelle d’un volume MongoDB 8.0.
- `start` vérifie réellement `/`, `/backend/health`, `/backend/api/models` et `/backend/api/settings` depuis la machine hôte avant d’afficher l’URL.
- Le fichier `.env` du bundle est monté dans l’API : les réglages et choix de modèles enregistrés depuis l’interface persistent après recréation des conteneurs.
- `INTERNAL_API_BASE=http://api:8000` est volontaire : Next.js l’utilise côté serveur pour le proxy même origine `/backend`. Il ne faut pas le remplacer par `127.0.0.1`.

Si une tentative antérieure a laissé un volume incompatible ou une installation incomplète :

```bash
bash deploy/offline.sh reinstall
```

`reinstall` supprime le volume MongoDB du projet et restaure le dump embarqué. Sauvegarder les nouvelles données créées sur la cible avant de l’utiliser.

## Exploitation

```bash
bash deploy/offline.sh status
bash deploy/offline.sh logs
bash deploy/offline.sh diagnose
bash deploy/offline.sh smoke
bash deploy/offline.sh stop
bash deploy/offline.sh start
```

Les données MongoDB sont dans un volume Docker. Les modèles Ollama sont dans le magasin embarqué `models/ollama-store`, monté en lecture seule. Les documents,
index et corpus LynX sont dans `data/`, `docs/` et `lynx/corpus/`. Sauvegarder
ces éléments avant de remplacer ou supprimer une installation.

## Dépannage

```bash
bash deploy/offline.sh diagnose
docker compose logs --tail=100
```

- port occupé : changer `WEB_PORT` dans `.env`, puis relancer `start` ;
- espace insuffisant : vérifier avec `df -h` et `docker system df` ;
- somme invalide : recopier le paquet, ne pas poursuivre l'installation ;
- modèle absent : refaire l'export depuis une source où `ollama list` le montre ;
- index incohérent : `docker compose exec api python scripts/repair_vector_index.py`.
