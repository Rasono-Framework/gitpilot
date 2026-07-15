# ENGINEERING LOG — `github-api-cli`

> Format: most recent entry first. Do not delete older entries.

---

## 2026-07-15 | Stabilisation runtime, owners generiques et mode sans etat

- **Contexte :**
  Le backend FastAPI fonctionnait deja en mode SQL, mais il manquait trois
  capacites importantes pour le rendre exploitable proprement :
  (1) gerer la creation de repo de facon plus generique cote `user` ou `org`,
  (2) exposer `delete-repo`, et (3) permettre un mode sans base pour les
  deploiements simples ou les executions ephameres.

- **Modifications effectuees :**
  - `src/github_async.py` — `create_repo()` supporte maintenant
    `owner_type=auto|org|user`, resolution du type d'owner, cache du type
    d'owner, et resolution du login authentifie pour les repos user.
    `list_repos()` supporte aussi la lecture generique d'un owner.
  - `src/api_models.py` — ajout du type d'operation `delete_repo`.
  - `src/api_service.py` — refonte du service pour supporter deux modes :
      * `STATE_BACKEND=sql` : persistance + queue + polling d'operations
      * `STATE_BACKEND=none` : aucune persistance, `wait=true` obligatoire,
        execution inline, endpoints d'operations desactives
    Ajout de `delete_repo`, resolution propre du owner par defaut, et d'un
    format interne `OperationView` independant du stockage.
  - `src/api_app.py` — ajout de `GET /v1/owners/{owner}/repos`,
    conservation de `GET /v1/orgs/{org}/repos` comme raccourci, ajout de
    `DELETE /v1/repos/{owner}/{repo}`, reponses explicites quand
    `STATE_BACKEND=none`.
  - `src/config.py` — `GITHUB_ORGANIZATION` n'est plus obligatoire,
    ajout de `STATE_BACKEND`, normalisation des valeurs `sql|none`.
  - `src/server.py` — `init-db` devient un no-op en mode `none`.
  - `entrypoint.sh` — suppression de l'exigence shell sur
    `GITHUB_ORGANIZATION`.
  - `tests/api_smoke_test.py` — couvre les owners generiques et
    `delete_repo`.
  - `tests/stateless_api_smoke_test.py` — couvre le mode sans etat.
  - `README.md`, `CONTRIBUTING.md`, `LICENSE`, `pyproject.toml` — alignement
    du nom produit sur `gitpilot`, documentation du mode `none`, ajout d'un
    guide contributeur et licence MIT.

- **Decisions Techniques :**
  - **Mode sans etat = execution synchrone uniquement** : sans stockage, il
    n'existe pas de support fiable pour le polling d'operations. La decision
    est donc de forcer `wait=true` plutot que d'introduire un faux mode async
    introuvable apres crash/redeploy.
  - **`GITHUB_ORGANIZATION` rendu optionnel** : necessaire pour permettre un
    usage centré user sans configuration org par defaut.
  - **Alias `gitpilot` dans `pyproject.toml`** : permet d'aligner le packaging
    et les scripts avec le nom du produit, sans casser les alias existants.

- **Impacts & Dependances :**
  - Le backend fonctionne maintenant correctement avec :
      * SQLite
      * PostgreSQL
      * aucun stockage (`STATE_BACKEND=none`)
  - Les endpoints `/v1/operations*` ne sont disponibles qu'en mode `sql`.
  - La creation user-scoped depend du contexte auth GitHub et utilise
    l'identite authentifiee, pas un username arbitraire.

- **Prochaines etapes :**
  - Si le schema devient plus riche que la seule table `operations`, brancher
    Alembic deviendra pertinent pour les migrations prod.
  - Ajouter des tests live specifiques au scope user si un token OAuth dedie
    est disponible en environnement de test.

---

## 2026-07-15 | Documentation developpeur et hygiene repo

- **Contexte :**
  Le backend FastAPI a ete mis en place, mais il manquait une documentation
  d'entree de projet exploitable par un developpeur qui arrive a froid.
  L'objectif etait de produire un `README.md` utile, concret, tourne usage
  dev, avec les commandes de demarrage, la config, les endpoints et les
  exemples d'appel, tout en nettoyant `.gitignore` pour couvrir les variantes
  de secrets et fichiers locaux.

- **Modifications effectuees :**
  - `README.md` — creation d'une documentation developpeur complete :
    presentation du service, architecture, prerequis GitHub App, config
    `.env`, choix SQLite/PostgreSQL, installation locale, demarrage serveur,
    usage Docker, endpoints exposes, exemples `curl`, modele d'operation,
    workflow de dev, structure du projet et notes de production.
  - `.gitignore` — ajout de `.ENV` pour eviter un commit accidentel du
    fichier de secrets en majuscules, et ajout de `*.sqlite3` pour couvrir les
    variantes de base locale non encore ignorees.

- **Decisions Techniques :**
  - **README centre developpeur plutot que marketing** : le besoin est
    l'onboarding et l'exploitation rapide. Le document privilegie donc les
    commandes executables, la topologie du code, les choix d'architecture et
    les exemples d'appels.
  - **Documentation explicite de la limite "1000 req/s"** : il fallait eviter
    de survendre FastAPI seul. Le README distingue l'acceptation des jobs HTTP
    de la completion reelle cote GitHub, qui depend d'un systeme externe.
  - **Gitignore defensif sur `.ENV`** : le projet a utilise les deux variantes
    `.env` et `.ENV`. Ignorer uniquement la minuscule laissait une fuite
    possible en cas de commit manuel.

- **Impacts & Dependances :**
  - `README.md` devient le point d'entree principal pour lancer le backend et
    comprendre l'architecture.
  - `.gitignore` couvre mieux les secrets et artefacts locaux, sans impacter
    le runtime.

- **Prochaines etapes :**
  - Ajouter une section migrations si Alembic entre dans le projet.
  - Ajouter des exemples d'integration client (Python/JS) si plusieurs
    consommateurs internes apparaissent.

---

## 2026-07-15 | Migration CLI -> backend FastAPI async avec queue mémoire et DB selectable

- **Contexte :**
  Le besoin a évolué d'un CLI local vers un backend HTTP FastAPI capable
  d'encaisser une forte cadence d'entrée, sans Redis, tout en gardant la
  logique GitHub App existante. La contrainte produit est double :
  (1) exposer les opérations GitHub via API (`create-repo`, `create-branch`,
  `push-file`, `push-files`) ; (2) laisser l'utilisateur choisir entre
  SQLite et PostgreSQL via configuration, sans bifurquer l'architecture.

- **Modifications effectuées :**
  - `src/github_async.py` (nouveau) — client GitHub full-async basé sur
    `httpx.AsyncClient` réutilisé au niveau app, cache du token
    d'installation protégé par `asyncio.Lock`, retries bornés, capture du
    rate limit, et équivalents async de `create_repo`, `create_branch`,
    `push_file`, `push_files`.
  - `src/api_models.py` + `src/api_db.py` (nouveaux) — persistance SQLAlchemy
    async d'un journal d'opérations (`operations`), avec compatibilité
    SQLite/PostgreSQL et réglages SQLite (`WAL`, `synchronous=NORMAL`,
    `busy_timeout`) pour limiter les contentions locales.
  - `src/api_service.py` (nouveau) — file mémoire bornée `asyncio.Queue`,
    pool de workers async, persistance des jobs (`queued/running/succeeded/
    failed`), et décorrélation entre le débit HTTP entrant et la latence
    sortante vers GitHub. C'est le levier principal pour absorber beaucoup
    plus de RPS sans introduire Redis.
  - `src/api_schemas.py` + `src/api_app.py` (nouveaux) — factory FastAPI,
    `lifespan` pour mutualiser DB + client HTTP + workers, `request_id`
    middleware, endpoints versionnés `/v1`, Bearer auth obligatoire via
    `API_AUTH_TOKEN`, endpoints `healthz/readyz`, `list_repos`,
    `create_repo`, `create_branch`, `push_file`, `push_files`, lecture des
    opérations.
  - `src/server.py` (nouveau) — entrypoint backend avec `serve` et `init-db`.
  - `src/config.py` — extension de la config : `DATABASE_URL`,
    `API_AUTH_TOKEN`, `API_HOST`, `API_PORT`, `API_WORKERS`,
    `QUEUE_MAXSIZE`, `QUEUE_WORKERS`, normalisation automatique des URLs
    `sqlite:// -> sqlite+aiosqlite://` et `postgresql:// -> ...+asyncpg://`,
    plus priorité explicite aux variables d'environnement sur le fichier.
  - `requirements.txt` + `pyproject.toml` — ajout de `fastapi`, `uvicorn`,
    `httpx`, `SQLAlchemy[asyncio]`, `aiosqlite`, `asyncpg`, et du script
    `github-api-server`.
  - `Dockerfile` + `entrypoint.sh` — le conteneur lance maintenant le
    service FastAPI, initialise le schéma au boot (`init-db`), expose le
    port 8000 et reste non-root.
  - `.env.example`, `.gitignore`, `.dockerignore` — ajout des variables API/
    DB et exclusion des fichiers SQLite temporaires.
  - `tests/api_smoke_test.py` (nouveau) — smoke test ASGI avec faux client
    GitHub, vérifie `healthz`, auth Bearer, exécution synchrone (`wait=true`)
    et queue d'opérations.

- **Décisions techniques :**
  - **Queue mémoire + polling DB, pas de traitement inline par défaut** :
    pour viser une forte cadence d'entrée sans Redis, il faut découpler le
    coût d'une requête HTTP du coût des appels GitHub. Les endpoints
    renvoient donc un `operation_id` par défaut, et `wait=true` reste
    disponible pour les workflows unitaires ou les tests.
  - **FastAPI async + `httpx.AsyncClient` partagé via `lifespan`** : évite
    la création d'un client et d'un pool TCP par requête, ce qui est
    indispensable pour tenir la charge sans gaspiller CPU/sockets.
  - **Secure by default** : l'API refuse de démarrer sans `API_AUTH_TOKEN`.
    Exposer des endpoints de création de repo/push sans auth aurait été une
    faille critique immédiate.
  - **SQLite supporté mais pas présenté comme solution multi-writers à très
    grande échelle** : SQLite est idéal pour dev/single-node, PostgreSQL est
    la cible normale dès qu'on augmente `API_WORKERS` ou qu'on maintient une
    vraie concurrence d'écriture.
  - **Suppression de `ORJSONResponse`** : la doc FastAPI actuelle signale que
    ce n'est plus le chemin recommandé avec des `response_model` Pydantic.
    Le retirer évite une dépréciation et simplifie le runtime sans perdre en
    performance utile.

- **Impacts & dépendances :**
  - Le CLI historique reste présent (`github-api`) ; le backend s'ajoute sans
    casser le mode outillage local. Le point d'entrée serveur est
    `github-api-server serve`.
  - La nouvelle API écrit en base chaque opération ; les appels mutatifs sont
    maintenant auditables et récupérables via `/v1/operations/{id}`.
  - `DATABASE_URL` choisit le backend sans changement de code applicatif.
    Valeur par défaut : `sqlite:///./github_api.db`.
  - Avec `API_WORKERS>1`, PostgreSQL est le choix sain. SQLite reste supporté
    mais n'est pas la bonne cible pour du multi-process soutenu.

- **Prochaines étapes :**
  - Ajouter une pagination SQL sur `/v1/operations` si le volume d'audit
    grandit.
  - Ajouter des endpoints de suppression/rollback contrôlés si le produit en
    a besoin.
  - Introduire un mécanisme de quotas/rate limiting applicatif si l'API doit
    être exposée à plusieurs tenants.

---

## 2026-07-15 | Bootstrap: GitHub App → API CLI in Python

- **Contexte :**
  Le projet démarre vide (uniquement un `.env` contenant les credentials d'une
  GitHub App + installation GitHub, et des variables OAuth). Le besoin : pouvoir
  *par API* créer un repo, créer une branche, et pousser un/des fichier(s) sur
  l'organisation cible (`orvyx`), sans dépendre de la CLI `git` ni de
  `gh`/Playwright. La contrainte est de faire ça "proprement" : structure
  maintenable, secrets hors du repo, image Docker prête pour CI/CD.

- **Modifications effectuées :**
  - `src/config.py` — chargement `.env` via `python-dotenv`, validation des clés
    obligatoires, normalisation de la clé RSA (`\n` littéraux → retours ligne
    réels), avertissement si le fichier `.env` est lisible par d'autres
    utilisateurs.
  - `src/auth.py` — flux GitHub App en deux étapes (JWT court → token
    d'installation 1 h), cache thread-safe, refresh automatique 5 min avant
    expiration, erreurs explicites (401 vs 404 vs autre).
  - `src/client.py` — wrapper fin autour de l'API REST : `create_repo`,
    `get_repo`, `list_repos`, `delete_repo`, `create_branch`, `get_file`,
    `push_file`, `push_files` (commit atomique via Trees/Blobs/Commits/Refs).
    Retry exponentiel sur 5xx et sur 401 (token révoqué), capture des headers
    `x-ratelimit-*`, et exposition d'un `GitHubApiError` typé avec
    `x-github-request-id` pour le support.
  - `src/commands.py` — sous-commandes argparse (`whoami`, `create-repo`,
    `list-repos`, `delete-repo`, `create-branch`, `push-file`). Confirmation
    interactive sur les actions destructives, override `-y` pour les scripts.
  - `src/cli.py` + `src/__main__.py` — point d'entrée `python -m src` et
    console script `github-api` (déclaré dans `pyproject.toml`).
  - `Dockerfile` — build multi-stage `python:3.12-slim`, utilisateur non-root
    `app`, base sur `tini` pour la propagation propre des signaux, séparation
    deps/runtime.
  - `entrypoint.sh` — sanity-check des variables requises, warning sur les
    permissions du `.env` monté, `exec python -m src "$@"`.
  - `.dockerignore` — exclut `.env`, `.git`, caches Python, docs.
  - `.gitignore` + `.env.example` — secrets jamais versionnés.
  - `pyproject.toml` — packaging minimal + entry point `github-api`.

- **Décisions techniques :**
  - **Pas de `PyGithub` / `gh`** : on garde un wrapper `requests` direct pour
    (1) ne pas transporter la grosse surface de l'ORM de PyGithub, (2) avoir un
    typage strict et lisible, (3) permettre de gérer finement les retries, le
    rate-limit, et les codes d'erreur. Coût : on ré-implémente quelques DTOs,
    mais ce sont des payloads stables.
  - **`python:3.12-slim` côté Docker** plutôt que 3.14 : 3.12 est la dernière
    LTS au moment de l'écriture, slim réduit la surface d'attaque (~150 MB).
  - **JWT TTL = 9 min, refresh token 5 min avant expiration** : marge confortable
    contre la dérive d'horloge et contre un token révoqué manuellement.
  - **Confirmation interactive sur les actions destructives** (`delete-repo`,
    `create-repo`) — overridable via `-y` pour les pipelines CI.
  - **`push_files` (commit atomique)** : implémente le pattern Git Data API pour
    les commits multi-fichiers. C'est le bon réflexe quand on pousse > 1
    fichier : évite N commits et un état intermédiaire cassé sur la branche.
  - **Aucune dépendance `gh`, `git`, `playwright`** : tout passe par HTTPS vers
    `api.github.com`. C'est ce qui rend le tool utilisable dans des
    environnements verrouillés.

- **Impacts & dépendances :**
  - Aucun fichier runtime n'est touché en dehors de `src/`, `Dockerfile`,
    `entrypoint.sh`, `pyproject.toml`, `requirements.txt`.
  - Le code suppose que la GitHub App est installée sur l'organisation
    `orvyx` avec les permissions : `Contents: Write`, `Metadata: Read`,
    `Administration: Write` (pour créer/supprimer des repos). Si l'App n'a
    pas ces permissions, GitHub répondra 403/422 avec un message explicite
    que l'on remonte tel quel.
  - Le `.env` actuel n'est pas versionné ; pour recréer un environnement il
    faut repartir de `.env.example`.

- **Prochaines étapes (à valider) :**
  - Valider le build Docker (`docker build` + run `whoami`).
  - Lancer un dry-run complet : `create-repo` → `create-branch` →
    `push-file`, puis supprimer le repo de test.
  - Ajouter une suite de tests unitaires sur `auth.py` (JWT signing, refresh
    logic) — pas critique pour un outil interne, mais utile avant d'ouvrir le
    repo à d'autres contributeurs.
  - Si l'usage devient répétitif, exposer aussi un mode "pipeline" via
    `stdin → JSON → exécution batch` (n'est pas dans le périmètre actuel).

---

## 2026-07-15 | Hardening après tests CLI

- **Contexte :**
  Premier passage de tests bout-en-bout après livraison. Les credentials
  présents dans `.env` sont valides (la clé RSA signe un JWT accepté par
  GitHub), mais `GITHUB_INSTALLATION_ID=98765432` ne correspond à aucune
  installation existante : `GET /app/installations` renvoie `[]`. Les
  commandes qui dépendent du token d'installation (`create-repo`,
  `list-repos`, `create-branch`, `push-file`, `delete-repo`) renvoient
  donc toutes un 404. C'est un problème de configuration côté GitHub, pas
  un bug — mais cela a révélé deux soucis UX à corriger.

- **Modifications effectuées :**
  - `src/commands.py` — `cmd_whoami` réécrit pour utiliser le **JWT App**
    (et non le token d'installation) : il marche même quand
    `GITHUB_INSTALLATION_ID` est faux, et affiche la liste des
    installations disponibles + les repos accessibles quand
    l'installation est valide. `cmd_list_installations --json` câble
    désormais le format JSON (avant : la sortie texte était toujours
    envoyée, peu importe le flag).
  - `src/auth.py` — nouvelle méthode `_list_installations_hint` :
    quand `POST /app/installations/{id}/access_tokens` renvoie 404,
    on appelle `GET /app/installations` avec le JWT App et on **ajoute
    au message d'erreur** la liste des installations réellement
    disponibles. L'utilisateur n'a plus à deviner quel ID mettre.
  - `tests/smoke_test.py` — test end-to-end du flux
    `create_repo → create_branch → push_file` avec un `requests.Session`
    mocké (les credentials actuelles ne permettent pas un test réel).
    Vérifie le routing HTTP exact (`POST /orgs/{org}/repos`,
    `GET /repos/{owner}/{repo}/branches/main`, `POST .../git/refs`,
    `GET .../contents/{path}` puis `PUT .../contents/{path}`) et les
    payloads (`base64(content)`, `branch`, `message`, `sha` pour update).

- **Décisions techniques :**
  - **`whoami` avec le JWT App, pas le token d'installation** : un outil
    de diagnostic doit fonctionner *quand quelque chose est cassé*, sinon
    il n'a aucune utilité. Le coût (un appel API supplémentaire) est
    négligeable.
  - **Enrichir le 404 avec la liste des installations** plutôt que
    demander à l'utilisateur de lancer une autre commande : on réduit
    le "time-to-fix" de plusieurs minutes à une seule lecture du
    message d'erreur. La méthode est best-effort (ne raise jamais) —
    si GitHub est down, on affiche juste "(hint unavailable)".
  - **Smoke test en mockant `requests.Session`** : c'est la seule façon
    de valider la logique du `GitHubClient` sans dépendre d'une
    installation valide. Le mock est petit (~80 lignes) et explicite sur
    les routes attendues, ce qui sert aussi de **spécification** des
    endpoints GitHub utilisés.
  - **Pas de `--dry-run` ajouté** : avec le smoke-test on couvre déjà
    la chaîne complète hors réseau. Un dry-run dupliquerait la
    logique et ajouterait un mode parallèle à maintenir. À reconsidérer
    seulement si l'usage CI le demande.

- **Impacts & dépendances :**
  - `whoami` ne montre plus les repos tant que l'installation configurée
    n'est pas valide (logique : pas la peine de lister des repos
    auxquels on ne peut pas accéder). Dès que `GITHUB_INSTALLATION_ID`
    pointe sur une installation réelle, il liste jusqu'à 10 repos.
  - Le hint dans le 404 ajoute 1 appel API par erreur d'auth — ce n'est
    pas un problème tant que ça reste sur le chemin d'erreur.

- **Prochaines étapes (à valider) :**
  - Une fois l'App installée sur l'org, vérifier que `whoami` liste
    bien les repos accessibles.
  - Si on a plusieurs installations, ajouter un argument
    `--installation` qui override `GITHUB_INSTALLATION_ID` (utile pour
    les CI multi-org).
  - Brancher une vraie suite `pytest` autour de `tests/smoke_test.py`
    si le projet grandit.

---

## 2026-07-15 | Ajout de l'auth OAuth (user-to-server) en complément du JWT App

- **Contexte :**
  L'App GitHub (`4248933`) est owned par le **user `@hackville254`** (pas
  par l'org `orvyx`), et `list-installations` renvoie toujours `[]`. Le
  flow App → installation est donc inutilisable en l'état. Le user a
  remonté que la page de settings de l'App affiche maintenant le hint
  GitHub *"Using your App ID to get installation tokens? You can now use
  your Client ID instead."* — c'est la voie OAuth (user-to-server
  tokens, `gho_...`) qui permet d'agir en tant que `@hackville254` sans
  nécessiter d'installation de l'App.

- **Modifications effectuées :**
  - `src/oauth.py` (nouveau, ~200 lignes) — implémente le flow OAuth web :
    génération d'un `state` CSRF, serveur HTTP local loopback, capture
    du callback, échange code→token, persistance dans
    `~/.config/gh-api-cli/token.json` (mode 0o600). Refuse explicitement
    les redirect URI non-loopback (le serveur local ne peut pas écouter
    sur un domaine public ; il faut un backend qui proxifie).
  - `src/client.py` — `GitHubClient` accepte maintenant un
    `user_token: str | None`. Quand il est défini, **tous** les appels
    API utilisent le `Bearer gho_...` au lieu du token d'installation.
    Le refresh sur 401 ne s'applique plus (les user tokens sont
    statiques ; en cas de révocation, l'utilisateur relance `auth login`).
  - `src/commands.py` — trois nouvelles sous-commandes :
      * `auth-login` : lance le flow OAuth (ouvre le navigateur, attend
        le callback, sauvegarde le token, vérifie avec `GET /user`).
      * `auth-status` : affiche le token persisté (sans le révéler) et
        vérifie qu'il est toujours valide.
      * `auth-logout` : supprime le fichier de token.
  - `src/cli.py` — `_resolve_user_token()` cherche d'abord
    `GITHUB_USER_TOKEN` (env), puis le fichier persisté. Le user token
    est passé au `GitHubClient` qui l'utilise en priorité sur le flow
    App. Les logs indiquent le mode utilisé sans jamais loguer la valeur
    complète du token.
  - `tests/oauth_smoke_test.py` (nouveau) — end-to-end avec `webbrowser`
    et `requests.post` mockés : le "browser" simulé tire
    immédiatement le callback HTTP vers le serveur local, l'échange de
    token est stubbé. Vérifie l'URL authorize, le state, le scope, le
    mode 0o600 du fichier, et la résolution par `_resolve_user_token()`.

- **Décisions techniques :**
  - **Le user token prend le pas sur l'installation token** dans
    `GitHubClient._request`. Logique : si l'utilisateur a pris la peine
    de s'authentifier, c'est qu'il veut opérer en son nom — on respecte
    ce choix sans condition. Le `whoami` et `list-installations`
    continuent d'utiliser le JWT App (besoin d'identifiants App, pas
    user) — c'est OK, les deux coexistent.
  - **Serveur de callback strictement loopback** : refuser un host non
    `127.0.0.1`/`localhost`/`::1` dans `_parse_redirect_uri` évite la
    situation piège où le CLI attendrait 5 minutes un callback qu'il ne
    peut pas recevoir (cas du `zrok` URL). On remonte un message
    explicite qui dit quoi faire.
  - **Pas d'ouverture du navigateur par défaut en CI** : la fonction
    `open_browser` est injectable — `cmd_auth_login` peut donc être
    testée headless. C'est aussi utile pour du SSH sans `DISPLAY`.
  - **Token persisté en clair (mais 0o600)** plutôt que chiffré. C'est
    un compromis conscient : sur macOS le chiffrement additionnel passe
    par le Keychain (complexité non négligeable), et un fichier
    0o600 dans `~/.config` est déjà hors de portée des autres apps
    non-privilégiées. Si on monte en exigence, on bascule sur
    `keyring.get_keychain()` (macOS/Linux) ou `cryptography.fernet`
    (multi-plateforme).
  - **`auth login` reste interactif** : on a besoin que l'utilisateur
    clique "Authorize" sur github.com. C'est le seul moment non-fully-
    automated, et il n'arrive qu'une fois par session/token. Pour un
    usage CI, on documentera `GITHUB_USER_TOKEN=<token>` comme
    alternative.

- **Impacts & dépendances :**
  - Nouveau fichier : `src/oauth.py`. Aucune dépendance supplémentaire :
    tout vient de la stdlib (`http.server`, `urllib`, `secrets`,
    `webbrowser`, `threading`) sauf `requests` (déjà présent).
  - Les commandes `create-repo`, `create-branch`, `push-file`, etc.
    marchent maintenant dès qu'un user token valide est chargé — il
    n'est plus nécessaire que l'App soit installée sur l'org cible.
  - Le `redirect_uri` dans `.env` pointe vers le backend zrok. Pour
    utiliser le flow CLI, l'utilisateur doit :
      1. Ajouter `http://127.0.0.1:8888/callback` aux **Authorization
         callback URL** de l'App (sur github.com).
      2. Remplacer `GITHUB_OAUTH_REDIRECT_URI` dans `.env` par cette
         URL, OU laisser vide pour prendre le défaut.

- **Prochaines étapes (à valider) :**
  - Vérifier en vrai que `auth login` aboutit (le user doit ajouter
    `http://127.0.0.1:8888/callback` aux callbacks de l'App).
  - Une fois loggé, tester la chaîne `create-repo` → `create-branch` →
    `push-file` avec le user token (l'org cible sera `hackville254`
    par défaut, ou `orvyx` si `@hackville254` y a les droits).
  - Si l'usage CI se confirme, exposer un mode `--device` qui utilise
    le device flow GitHub (pas supporté par les GitHub Apps
    actuellement, à vérifier au moment du besoin).
