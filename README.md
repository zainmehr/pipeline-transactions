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
| Base de données | PostgreSQL 16 (psycopg 3) |
| Conteneurs | Docker Compose |

## Structure du projet

```
├── .github/workflows/ci.yml   → pipeline CI
├── src/pipeline/
│   ├── generation/            → génération des fichiers CSV
│   ├── loading/               → chargement des fichiers
│   ├── processing/            → traitements (fonctions pures)
│   ├── storage/               → schéma SQL et insertion en base
│   ├── models.py              → modèles de données partagés (Transaction, ...)
│   ├── validation.py          → validation des lignes CSV (fonctions pures)
│   └── main.py                → point d'entrée de la pipeline
├── tests/
│   ├── unit/                  → tests unitaires
│   └── integration/           → tests d'intégration
├── sql/init/                  → scripts d'initialisation du conteneur PostgreSQL
├── docker-compose.yml         → base PostgreSQL locale
├── .env.example               → modèle de configuration
├── data/                      → fichiers CSV générés (non versionnés)
└── docs/screenshots/          → captures de la simulation d'échec
```

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows : .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
docker compose up -d             # lance PostgreSQL (bases « pipeline » et « pipeline_test »)
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

### Charger et traiter les fichiers CSV

```bash
run-pipeline --input data
```

La commande charge tous les fichiers `.csv` du dossier, valide chaque ligne, puis affiche :

- la somme des montants envoyés par `iban_origine` ;
- la somme des montants envoyés par `banque_source` ;
- la somme des montants reçus par `iban_destinataire` ;
- les transactions flaggées (montant strictement supérieur à 5000).

Si un fichier est invalide, la commande affiche toutes les erreurs avec leur numéro de ligne
et se termine avec le code retour `1`, ce qui fait échouer la CI.

### Insérer les résultats en base

```bash
run-pipeline --input data --save
```

Le schéma est créé automatiquement s'il n'existe pas, puis chaque fichier est inséré avec
ses transactions et ses sommes. Un fichier déjà inséré (même contenu) est refusé.

Consulter la base :

```bash
docker compose exec postgres psql -U pipeline -d pipeline
```

### Architecture du traitement

```
Fichier CSV ──> loading (I/O) ──> validation (pure) ──> processing (pur) ──> résultats
```

- **`loading/`** : lecture des fichiers uniquement, aucune logique métier.
- **`validation.py`** : conversion d'une ligne en `Transaction` et contrôle du format
  (colonnes, date ISO 8601, montant positif à 2 décimales, IBAN valide et cohérent avec
  le pays, émetteur différent du destinataire). Toutes les erreurs d'une ligne sont
  signalées en une seule fois.
- **`processing/`** : traitements purs. Ils ne modifient pas leurs entrées (transactions
  immuables), ne dépendent d'aucun état externe et renvoient toujours le même résultat
  pour les mêmes entrées. Des tests vérifient explicitement ces trois propriétés.

## Base de données

Le schéma est défini dans `src/pipeline/storage/schema.sql`. Il est idempotent et embarqué
dans le paquet Python, pour fonctionner aussi dans un conteneur.

| Table | Contenu |
|---|---|
| `processed_files` | Fichiers insérés, identifiés par le hash SHA-256 de leur contenu (unique) |
| `accounts` | Comptes (IBAN, pays), le pays devant correspondre au préfixe de l'IBAN |
| `banks` | Banques émettrices |
| `transactions` | Transactions avec leur flag, rattachées à leur fichier, leurs comptes et leur banque |
| `total_sent_by_iban` | Somme des montants envoyés par IBAN |
| `total_sent_by_bank` | Somme des montants envoyés par banque |
| `total_received_by_iban` | Somme des montants reçus par IBAN |

**Normalisation.** Le pays est porté par le compte et la banque par sa table : aucune
information n'est dupliquée dans `transactions`.

**Sommes cumulées.** Les traitements Python calculent les sommes de chaque fichier, qui
s'ajoutent aux sommes existantes (`ON CONFLICT ... DO UPDATE SET total = total + ...`).
Les vues `v_total_sent_by_iban`, `v_total_sent_by_bank` et `v_total_received_by_iban`
recalculent les mêmes sommes depuis les transactions : les tests vérifient que tables et
vues sont toujours identiques.

**Atomicité.** L'insertion d'un fichier (fichier, transactions, sommes) se fait dans une
seule transaction SQL : en cas d'erreur, rien n'est écrit. Combinée à l'unicité du hash,
elle garantit qu'un fichier n'est jamais compté deux fois.

**Contraintes d'intégrité.** Montant strictement positif, émetteur différent du
destinataire, clés étrangères : la base refuse les données incohérentes même si la
validation Python était contournée.

## Tests et qualité

```bash
pytest                                           # tous les tests
pytest -m "not integration"                      # tests unitaires uniquement
pytest -m integration                            # tests d'intégration (PostgreSQL requis)
pytest --cov=pipeline --cov-report=term-missing  # avec couverture
ruff check . && ruff format --check .            # lint et formatage
```

Les tests d'intégration utilisent la base `pipeline_test`, vidée avant chaque test. Si
PostgreSQL n'est pas lancé, ils sont ignorés en local ; en CI (`REQUIRE_DB=1`), ils échouent.

Reproduire la CI en local avant de pousser :

```bash
ruff check . && ruff format --check . && pytest && generate-csv --files 5 && run-pipeline
```

## Intégration continue

La CI (GitHub Actions) se déclenche sur chaque Pull Request et sur chaque push sur `main`.
Elle comporte deux jobs :

1. **Qualité et tests unitaires** : lint, formatage, tests unitaires avec couverture.
2. **Base de données et tests d'intégration** (lancé seulement si le premier réussit) :
   démarre un service PostgreSQL, exécute les tests d'intégration, génère les CSV puis
   les insère en base avec `run-pipeline --save`.

## Workflow Git

- Aucun push direct sur `main` : chaque tâche est développée sur une branche
  `feature/<nom>` puis fusionnée via une Pull Request.
- Une Pull Request n'est fusionnée que si la CI est verte.
- Convention de commits : `feat:`, `fix:`, `test:`, `ci:`, `docs:`, `chore:`.

## Avancement

### Niveau 1 — Pipeline data locale
- [x] 1.1 Génération des données CSV
- [x] 1.2 Chargement et traitement des données
- [x] 1.3 Base de données relationnelle
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