# Pipeline Transactions — Mise en situation d'intégration continue

Projet réalisé par **Zain MEHR** et **Adam GHORIFA**.

## Objectif

Pipeline data automatisée allant de la génération de transactions financières au format CSV
jusqu'à leur insertion dans une base de données relationnelle, intégrée à une démarche CI/CD :

```
Génération CSV → Chargement → Validation → Traitement → Base de données
```

## Stack technique

| Élément | Outil |
|---|---|
| Langage | Python 3.12 |
| Tests | pytest, pytest-cov |
| Qualité du code | ruff (lint + formatage) |
| CI | GitHub Actions |
| Base de données | PostgreSQL (à venir) |
| Conteneurs | Docker Compose (à venir) |

## Structure du projet

```
├── .github/workflows/ci.yml   → pipeline CI
├── src/pipeline/
│   ├── generation/            → génération des fichiers CSV
│   ├── loading/               → chargement des fichiers
│   ├── processing/            → traitements (fonctions pures)
│   ├── storage/               → insertion en base
│   └── main.py                → orchestration de la pipeline
├── tests/
│   ├── unit/                  → tests unitaires
│   └── integration/           → tests d'intégration
├── sql/                       → schéma de la base
├── data/                      → fichiers CSV générés (non versionnés)
└── docs/screenshots/          → captures de la simulation d'échec
```

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows : .venv\Scripts\activate
pip install -e ".[dev]"
```

## Utilisation

### Générer les fichiers CSV

```bash
generate-csv --output data --files 5 --seed 42
```

| Option | Défaut | Description |
|---|---|---|
| `--output` | `data` | Dossier de sortie |
| `--files` | `5` | Nombre de fichiers générés |
| `--seed` | `42` | Graine aléatoire (même graine = mêmes fichiers) |

Chaque fichier contient entre 5 et 10 transactions au format suivant :

| Colonne | Description | Exemple |
|---|---|---|
| `datetime_transaction` | Date et heure (ISO 8601) | `2026-01-01T08:22:30` |
| `iban_origine` | IBAN de l'émetteur | `FR2430003012340001122334455` |
| `pays_source` | Pays de l'émetteur (ISO 3166) | `FR` |
| `banque_source` | Banque de l'émetteur | `Société Générale` |
| `iban_destinataire` | IBAN du destinataire | `BE86310123456789` |
| `pays_destinataire` | Pays du destinataire (ISO 3166) | `BE` |
| `montant` | Montant en euros, 2 décimales | `5000.01` |

### Choix de conception des données

- **Déterministes** : une graine fixe produit toujours exactement les mêmes fichiers,
  ce qui permet de tester la pipeline avec des valeurs attendues connues.
- **Référentiel réduit de 8 comptes** : chaque IBAN apparaît plusieurs fois, ce qui rend
  les agrégations significatives. Deux banques possèdent deux comptes afin de distinguer
  la somme par IBAN de la somme par banque.
- **Cas limites du flag** : le premier fichier contient toujours les montants `4999.99`,
  `5000.00` (non flaggé) et `5000.01` (flaggé).
- **IBAN valides** : clé de contrôle modulo 97 conforme à la norme ISO 13616.
- **Montants en `Decimal`** : aucune erreur d'arrondi liée aux nombres flottants.

## Tests et qualité

```bash
pytest                                           # tous les tests
pytest -m "not integration"                      # tests unitaires uniquement
pytest --cov=pipeline --cov-report=term-missing  # avec couverture
ruff check . && ruff format --check .            # lint et formatage
```

Reproduire la CI en local avant de pousser :

```bash
ruff check . && ruff format --check . && pytest -m "not integration" && generate-csv --files 5
```

## Intégration continue

La CI (GitHub Actions) se déclenche sur chaque Pull Request et sur chaque push sur `main`.
Elle vérifie le lint, le formatage, les tests unitaires avec couverture, puis exécute
la génération des CSV.

## Workflow Git

- Aucun push direct sur `main` : chaque tâche est développée sur une branche
  `feature/<nom>` puis fusionnée via une Pull Request.
- Une Pull Request n'est fusionnée que si la CI est verte.
- Convention de commits : `feat:`, `fix:`, `test:`, `ci:`, `docs:`, `chore:`.

## Avancement

### Niveau 1 — Pipeline data locale
- [x] 1.1 Génération des données CSV
- [ ] 1.2 Chargement et traitement des données
- [ ] 1.3 Base de données relationnelle
- [ ] 1.4 Pipeline complète
- [ ] 1.5 Tests d'intégration automatiques
- [ ] 1.6 Simulation d'un échec de pipeline
- [ ] 1.7 Retry lors de l'insertion en base

### Niveau 2 — Stockage S3
- [ ] 2.1 Stockage des fichiers dans un service S3
- [ ] 2.2 Upload automatique dans le S3
- [ ] 2.3 Lecture des données depuis le S3
- [ ] 2.4 Retry lors de la récupération depuis le S3

### Niveau 3 — Améliorations
- [ ] Option à choisir
- [ ] Option à choisir