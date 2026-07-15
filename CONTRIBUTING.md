# Contributing to gitpilot

Merci de contribuer a `gitpilot`.

## Objectif

- garder le code simple a exploiter
- preferer des changements petits et testables
- ne jamais committer de secrets
- garder la compatibilite des modes `sql` et `none`

## Setup local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Avant d'ouvrir une PR

- verifier que `.env`, `.ENV`, les cles PEM et les bases locales ne sont pas suivis
- lancer les smoke tests
- garder les commits clairs et petits
- documenter les changements qui modifient le comportement public

## Tests minimum

```bash
.venv/bin/python tests/api_smoke_test.py
.venv/bin/python tests/stateless_api_smoke_test.py
```

## Lignes directrices

- privilegier `SQLite` pour le dev local
- privilegier `PostgreSQL` pour les scenarios multi-workers
- en mode `STATE_BACKEND=none`, toujours tester `wait=true`
- ne pas ajouter de dependances lourdes sans justification
- ne pas logguer de secrets, tokens ou cles privees

## Documentation

Si tu modifies les endpoints, la configuration ou le comportement runtime :

- mets a jour `README.md`
- mets a jour `ENGINEERING_LOG.md`

## Style de contribution

- une PR = un objectif clair
- ajouter des tests quand le risque de regression est reel
- expliquer les compromis techniques dans la PR
