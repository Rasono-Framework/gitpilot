<p align="center">
  <img src="docs/assets/gitpilot-banner.png" alt="gitpilot banner" width="960">
</p>

<h1 align="center">gitpilot</h1>

<p align="center">
  Backend FastAPI async pour piloter GitHub via une GitHub App, avec support SQL optionnel et mode stateless.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
  <a href="CONTRIBUTING.md"><img src="https://img.shields.io/badge/contributions-welcome-brightgreen.svg" alt="Contributions welcome"></a>
  <img src="https://img.shields.io/badge/runtime-FastAPI-009688.svg" alt="FastAPI">
  <img src="https://img.shields.io/badge/storage-SQLite%20%7C%20PostgreSQL%20%7C%20None-5865F2.svg" alt="Storage backends">
</p>

gitpilot est un backend FastAPI async pour piloter GitHub via une GitHub App.

Le service expose une API HTTP pour :

- creer un repo
- creer une branche
- pousser un fichier
- pousser plusieurs fichiers dans un commit atomique
- lister les repos
- suivre les operations executees

Le projet garde aussi le CLI historique, mais le point d'entree principal est maintenant l'API FastAPI.

## Objectifs

- API simple a integrer dans des outils internes
- execution async avec reutilisation des connexions HTTP
- persistance optionnelle des operations
- support `SQLite`, `PostgreSQL` ou `sans etat` via configuration
- demarrage local simple
- conteneur Docker propre pour dev et deploiement

## Stack

- Python 3.10+
- FastAPI
- Uvicorn
- HTTPX async
- SQLAlchemy async
- SQLite via `aiosqlite`
- PostgreSQL via `asyncpg`
- GitHub App auth via JWT + installation token

## Architecture

- [api_app.py](file:///Users/user/code/bac%20a%20sable/test%20github/src/api_app.py)
  Factory FastAPI, lifecycle, middleware `x-request-id`, routes HTTP.
- [api_service.py](file:///Users/user/code/bac%20a%20sable/test%20github/src/api_service.py)
  Queue memoire `asyncio.Queue`, workers async, orchestration des jobs GitHub.
- [github_async.py](file:///Users/user/code/bac%20a%20sable/test%20github/src/github_async.py)
  Client GitHub async mutualise, retries, cache du token d'installation.
- [api_db.py](file:///Users/user/code/bac%20a%20sable/test%20github/src/api_db.py)
  Bootstrap SQLAlchemy async, validation du schema, support SQLite/PostgreSQL.
- [api_models.py](file:///Users/user/code/bac%20a%20sable/test%20github/src/api_models.py)
  Table `operations` pour la tracabilite.
- [migrations.py](file:///Users/user/code/bac%20a%20sable/test%20github/src/migrations.py)
  Adaptateur Alembic utilise par `init-db` et `db-current`.
- [server.py](file:///Users/user/code/bac%20a%20sable/test%20github/src/server.py)
  Entree `serve`, `init-db` et `db-current`.

## Flux d'execution

1. Une requete HTTP arrive avec un Bearer token interne.
2. L'endpoint valide le payload.
3. En mode `sql`, les migrations Alembic posent le schema puis une operation est ecrite en base et executee en `wait=true` ou en queue.
4. En mode `none`, aucune operation n'est persistee et `wait=true` devient obligatoire.
5. Un worker async appelle GitHub via le client mutualise.
6. En mode `sql`, le resultat est consultable via `/v1/operations/{id}`.

## Limites de performance

Le service est optimise pour accepter un debit eleve de requetes entrantes avec FastAPI async, pooling HTTP et queue memoire.

Important :

- `1000 req/s` peut etre realiste pour l'acceptation de jobs HTTP sur une machine correcte
- `1000` ecritures GitHub completes par seconde ne depend pas uniquement de FastAPI
- la vraie limite vient surtout de la latence reseau, du rate limiting GitHub, et du nombre de workers/process

En pratique :

- `SQLite` : bien pour dev, tests, single-node, faible concurrence d'ecriture
- `PostgreSQL` : choix recommande des qu'on passe en charge reelle, multi-workers, ou besoins de retention d'operations
- `STATE_BACKEND=none` : utile pour un service simple sans base, avec execution synchrone uniquement

## Securite

- toutes les routes metier exigent `Authorization: Bearer <API_AUTH_TOKEN>`
- le service refuse de demarrer sans `API_AUTH_TOKEN`
- les secrets GitHub ne sont pas committe dans le repo
- en mode `sql`, chaque operation est tracee avec statut, duree, erreur, `request_id` et resultat

## Prerequis

- une GitHub App valide
- l'App installee sur l'organisation cible
- les permissions GitHub adequates
  `Contents: Read & Write`
  `Administration: Read & Write`
  `Metadata: Read`
- Python et un virtualenv

## Configurer la GitHub App

### Liens officiels

- creation et enregistrement : [Registering a GitHub App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app)
- creation detaillee : [Creating a GitHub App](https://docs.github.com/en/apps/creating-github-apps/setting-up-a-github-app/creating-a-github-app)
- choix des permissions : [Choosing permissions for a GitHub App](https://docs.github.com/en/apps/creating-github-apps/setting-up-a-github-app/choosing-permissions-for-a-github-app)
- installation sur un compte ou une organisation : [Installing your own GitHub App](https://docs.github.com/en/apps/using-github-apps/installing-your-own-github-app)

### Liens directs GitHub

- app personnelle : [github.com/settings/apps/new](https://github.com/settings/apps/new)
- app d'organisation : `https://github.com/organizations/<your-org>/settings/apps/new`

### Parametres recommandes

- **GitHub App name** : un nom globalement unique, court et explicite
- **Homepage URL** : l'URL du projet ou de la doc interne
- **Webhook** : desactive si tu utilises seulement l'API sortante ; active-le uniquement si tu implementes une reception serveur et un secret de webhook
- **Where can this GitHub App be installed?** : prefere `Only on this account` pour reduire la surface d'exposition

### Permissions minimales pour `gitpilot`

- **Repository permissions**
- `Contents: Read and write`
- `Administration: Read and write`
- `Metadata: Read-only`
- `Workflows: Read and write` seulement si tu dois modifier des fichiers dans `.github/workflows/`

Pourquoi :

- `Contents` est requis pour lire et pousser des fichiers
- `Administration` est requis pour les operations de creation et suppression de repositories
- `Metadata` est necessaire pour la lecture des metadonnees de repo et est couramment requise comme permission de base
- `Workflows` est optionnel et ne doit pas etre demande si tu ne modifies jamais les workflows GitHub Actions

### Installation recommandee

1. Cree la GitHub App via l'interface GitHub ou les liens ci-dessus.
2. Genere la cle privee PEM depuis la page de l'app.
3. Installe l'app sur le compte cible.
4. Choisis `Only select repositories` si tu veux limiter la surface d'acces.
5. Si `gitpilot` cree de nouveaux repositories, GitHub leur accordera automatiquement l'acces a l'app une fois crees.
6. Recupere `App ID` et `Installation ID`, puis renseigne les variables dans `.env`.

### Valeurs a recuperer pour `.env`

- `GITHUB_APP_ID` : visible dans la page de settings de la GitHub App
- `GITHUB_PRIVATE_KEY` : contenu PEM de la cle privee telechargee
- `GITHUB_INSTALLATION_ID` : visible apres installation ou via la commande `github-api whoami`
- `GITHUB_ORGANIZATION` : optionnel ; utile si tu veux un owner par defaut cote API

## Configuration

Copier le modele :

```bash
cp .env.example .env
```

Variables principales :

```env
GITHUB_APP_ID=4248933
GITHUB_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"
GITHUB_INSTALLATION_ID=146737430
GITHUB_ORGANIZATION=your-org

API_AUTH_TOKEN=replace-with-a-long-random-secret
STATE_BACKEND=sql
API_HOST=0.0.0.0
API_PORT=8000
API_WORKERS=1

DATABASE_URL=sqlite:///./github_api.db
DB_ECHO=false
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=40
DB_POOL_TIMEOUT=30
DB_POOL_RECYCLE=1800

GITHUB_TIMEOUT_SECONDS=15
QUEUE_MAXSIZE=10000
QUEUE_WORKERS=64
```

## Choix de stockage

### SQLite

Mode local simple :

```env
DATABASE_URL=sqlite:///./github_api.db
```

Le code convertit automatiquement cette valeur en URL async SQLAlchemy compatible `aiosqlite`.

### PostgreSQL

Mode charge/retenue :

```env
DATABASE_URL=postgresql://user:password@127.0.0.1:5432/github_api
```

Le code convertit automatiquement cette valeur en `postgresql+asyncpg://...`.

### Sans etat

Mode simple, sans base, sans polling d'operations :

```env
STATE_BACKEND=none
```

Dans ce mode :

- `wait=true` est obligatoire sur les routes d'ecriture
- `/v1/operations` et `/v1/operations/{id}` sont indisponibles
- `init-db` devient un no-op

## Migrations Alembic

Le schema SQL n'est plus cree implicitement au boot de l'API.

Choix de stabilite :

- source de verite unique du schema via Alembic
- fail-fast au demarrage si le schema n'est pas applique
- meme flux pour dev, tests, Docker et prod

Commandes utiles :

```bash
# Appliquer toutes les migrations
.venv/bin/python -m src.server init-db

# Voir la revision courante
.venv/bin/python -m src.server db-current
```

En mode `STATE_BACKEND=sql`, lance toujours `init-db` avant `serve`.

## Installation locale

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Ensuite remplis `.env` avec tes vraies valeurs GitHub et ton `API_AUTH_TOKEN`.

## Initialiser la base

Uniquement utile si `STATE_BACKEND=sql`.

```bash
.venv/bin/python -m src.server init-db
```

Verifier la revision :

```bash
.venv/bin/python -m src.server db-current
```

## Lancer le serveur

```bash
API_AUTH_TOKEN=change-me \
STATE_BACKEND=sql \
DATABASE_URL=sqlite:///./github_api.db \
.venv/bin/python -m src.server serve
```

Le serveur ecoute ensuite sur `http://127.0.0.1:8000`.

## Lancer avec PostgreSQL

```bash
API_AUTH_TOKEN=change-me \
STATE_BACKEND=sql \
DATABASE_URL=postgresql://user:password@127.0.0.1:5432/github_api \
API_WORKERS=4 \
QUEUE_WORKERS=64 \
.venv/bin/python -m src.server serve
```

## Lancer sans base

```bash
API_AUTH_TOKEN=change-me \
STATE_BACKEND=none \
.venv/bin/python -m src.server serve
```

## Docker

Build :

```bash
docker build -t gh-api-service:latest .
```

Run :

```bash
docker run --rm -p 8000:8000 \
  -e GITHUB_APP_ID=4248933 \
  -e GITHUB_INSTALLATION_ID=146737430 \
  -e GITHUB_ORGANIZATION=your-org \
  -e GITHUB_PRIVATE_KEY="$(python3 - <<'PY'
from pathlib import Path
from src.config import _parse_env
print(_parse_env(Path('.env'))['GITHUB_PRIVATE_KEY'])
PY
)" \
  -e API_AUTH_TOKEN=change-me \
  -e STATE_BACKEND=sql \
  -e DATABASE_URL=sqlite:///./github_api.db \
  gh-api-service:latest
```

Alternative si ton `.env` est propre et monte dans le conteneur :

```bash
docker run --rm -p 8000:8000 \
  -v "$PWD/.env:/app/.env:ro" \
  -e API_AUTH_TOKEN=change-me \
  -e STATE_BACKEND=sql \
  -e DATABASE_URL=sqlite:///./github_api.db \
  gh-api-service:latest
```

## Health checks

```bash
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/readyz
```

Exemple de reponse :

```json
{
  "status": "ok",
  "database": "ok",
  "state_backend": "sql",
  "queue_size": 0,
  "queue_maxsize": 10000,
  "queue_workers": 64
}
```

## Auth API

Toutes les routes `/v1/*` exigent :

```http
Authorization: Bearer <API_AUTH_TOKEN>
```

Exemple shell :

```bash
export API_URL="http://127.0.0.1:8000"
export API_TOKEN="change-me"
```

## Endpoints

### Lecture

- `GET /healthz`
- `GET /readyz`
- `GET /v1/owners/{owner}/repos?owner_type=auto`
- `GET /v1/orgs/{org}/repos?limit=30`
- `GET /v1/users/{user}/repos?limit=30`
- `GET /v1/operations?limit=100`
- `GET /v1/operations/{operation_id}`

### Ecriture

- `POST /v1/repos`
- `DELETE /v1/repos/{owner}/{repo}`
- `POST /v1/repos/{owner}/{repo}/branches`
- `POST /v1/repos/{owner}/{repo}/files`
- `POST /v1/repos/{owner}/{repo}/files/batch`

## Exemples API

### Lister les repos

```bash
curl -s \
  -H "Authorization: Bearer $API_TOKEN" \
  "$API_URL/v1/owners/your-org/repos?owner_type=org&limit=10"
```

Alias user explicite :

```bash
curl -s \
  -H "Authorization: Bearer $API_TOKEN" \
  "$API_URL/v1/users/hackville254/repos?limit=10"
```

### Creer un repo

Mode async par defaut :

```bash
curl -s -X POST \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  "$API_URL/v1/repos" \
  -d '{
    "owner": "your-org",
    "owner_type": "org",
    "name": "demo-fastapi",
    "description": "repo cree par API",
    "private": true
  }'
```

Mode sync :

```bash
curl -s -X POST \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  "$API_URL/v1/repos" \
  -d '{
    "owner_type": "user",
    "name": "demo-fastapi-sync",
    "description": "repo cree pour le user authentifie",
    "private": true,
    "wait": true
  }'
```

### Supprimer un repo

```bash
curl -s -X DELETE \
  -H "Authorization: Bearer $API_TOKEN" \
  "$API_URL/v1/repos/your-org/demo-fastapi?wait=true"
```

### Creer une branche

```bash
curl -s -X POST \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  "$API_URL/v1/repos/your-org/demo-live-action/branches" \
  -d '{
    "branch": "feat/api",
    "from_branch": "main",
    "wait": true
  }'
```

### Pousser un fichier

```bash
curl -s -X POST \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  "$API_URL/v1/repos/your-org/demo-live-action/files" \
  -d '{
    "branch": "feat/api",
    "path": "README.md",
    "content": "# Hello from FastAPI\n",
    "message": "docs: add README via FastAPI",
    "wait": true
  }'
```

### Pousser plusieurs fichiers en un commit

```bash
curl -s -X POST \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  "$API_URL/v1/repos/your-org/demo-live-action/files/batch" \
  -d '{
    "branch": "feat/api",
    "message": "feat: batch push",
    "wait": true,
    "files": [
      {
        "path": "docs/index.md",
        "content": "# Documentation\n"
      },
      {
        "path": "src/main.py",
        "content": "print(\"hello\")\n"
      }
    ]
  }'
```

### Poller une operation async

```bash
curl -s \
  -H "Authorization: Bearer $API_TOKEN" \
  "$API_URL/v1/operations/<operation_id>"
```

## Modele d'operation

Les operations passent par 4 etats :

- `queued`
- `running`
- `succeeded`
- `failed`

Chaque operation persiste :

- type d'operation
- owner, repo, branch, path
- payload
- resultat GitHub
- erreur eventuelle
- `request_id`
- `github_request_id`
- duree en millisecondes

En mode `STATE_BACKEND=none`, ce modele n'est pas persiste et les routes
`/v1/operations*` sont desactivees.

## Dev workflow

### Smoke test API

```bash
.venv/bin/python tests/api_smoke_test.py
.venv/bin/python tests/stateless_api_smoke_test.py
```

### Migrations manuelles

```bash
.venv/bin/python -m src.server init-db
.venv/bin/python -m src.server db-current
```

### Lancer en local avec SQLite

```bash
API_AUTH_TOKEN=dev-token \
STATE_BACKEND=sql \
DATABASE_URL=sqlite:///./github_api.db \
.venv/bin/python -m src.server serve
```

## Structure du projet

```text
.
├── src/
│   ├── api_app.py
│   ├── api_db.py
│   ├── api_models.py
│   ├── api_schemas.py
│   ├── api_service.py
│   ├── auth.py
│   ├── client.py
│   ├── config.py
│   ├── github_async.py
│   ├── migrations.py
│   ├── server.py
│   └── ...
├── migrations/
│   ├── env.py
│   └── versions/
├── docs/
│   └── assets/
├── tests/
├── alembic.ini
├── Dockerfile
├── entrypoint.sh
├── requirements.txt
└── .env.example
```

## Notes de production

- garde `API_WORKERS=1` avec SQLite
- prefere PostgreSQL si tu utilises plusieurs workers Uvicorn
- utilise `STATE_BACKEND=none` seulement si tu acceptes de perdre le polling et l'audit persistant
- evite de mettre des valeurs trop agressives sur `QUEUE_WORKERS` sans mesurer
- GitHub reste le systeme le plus lent du flux, pas FastAPI
- si tu exposes l'API hors reseau interne, ajoute une couche reverse proxy + TLS + rate limiting
- garde Alembic comme source unique du schema et evite tout `create_all()` implicite en production

## Contribution

- guide de contribution : [CONTRIBUTING.md](file:///Users/user/code/bac%20a%20sable/test%20github/CONTRIBUTING.md)
- licence : [LICENSE](file:///Users/user/code/bac%20a%20sable/test%20github/LICENSE)

## Commandes utiles

```bash
# Installer les deps
pip install -r requirements.txt

# Initialiser la base
.venv/bin/python -m src.server init-db

# Lancer le serveur
.venv/bin/python -m src.server serve

# Smoke tests API
.venv/bin/python tests/api_smoke_test.py
.venv/bin/python tests/stateless_api_smoke_test.py
```
