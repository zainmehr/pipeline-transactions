# Pipeline Transactions — Mise en situation d'intégration continue

Projet réalisé par **Zain MEHR** et **Adam GHORIFA**.

## Objectif

Pipeline data automatisée allant de la génération de transactions financières (fichiers CSV
et relevés au format image) jusqu'à leur insertion dans une base de données relationnelle,
intégrée à une démarche CI/CD, et déployée automatiquement sur Kubernetes :

```
CSV ──────────────┐
                  ├──> Stockage S3 → Récupération → Chargement → Validation → Traitement → Base de données
Images ──> OCR ───┘
```

## Stack technique

| Élément | Outil |
|---|---|
| Langage | Python 3.12 |
| Tests | pytest, pytest-cov |
| Qualité du code | ruff (lint + formatage) |
| CI | GitHub Actions |
| Base de données | PostgreSQL 16 (psycopg 3) |
| Stockage objet | RustFS 1.0, compatible S3 (boto3) |
| OCR | Tesseract 5 (langue française), images générées avec Pillow |
| Conteneurs | Docker, Docker Compose (local) |
| CD et production | Kubernetes (kind dans GitHub Actions), CronJob, registre GHCR |

## Structure du projet

```
├── .github/workflows/ci.yml   → pipeline CI
├── .github/workflows/cd.yml   → pipeline CD (déploiement Kubernetes)
├── src/pipeline/
│   ├── generation/            → génération des fichiers CSV et des relevés images
│   ├── loading/               → récupération (local ou S3), chargement des CSV, OCR des images
│   ├── extraction/            → extraction des transactions du texte OCR (fonctions pures)
│   ├── processing/            → traitements (fonctions pures)
│   ├── storage/               → schéma SQL, insertion en base et stockage S3
│   ├── models.py              → modèles de données partagés (Transaction, ...)
│   ├── validation.py          → validation des lignes CSV (fonctions pures)
│   ├── retry.py               → mécanisme de nouvelle tentative (retry)
│   └── main.py                → point d'entrée de la pipeline
├── tests/
│   ├── unit/                  → tests unitaires
│   └── integration/           → tests d'intégration
├── sql/init/                  → scripts d'initialisation du conteneur PostgreSQL
├── Dockerfile                 → image du système de traitement (pipeline + Tesseract)
├── k8s/                       → manifestes Kubernetes de la production (kustomize)
├── scripts/k8s.sh             → déploiement et vérification sur Kubernetes
├── docker-compose.yml         → PostgreSQL et RustFS (S3) en local
├── Makefile                   → raccourcis (make pipeline, make test, ...)
├── .env.example               → modèle de configuration
├── data/                      → fichiers CSV et images générés (non versionnés)
└── docs/screenshots/          → captures de la simulation d'échec
```

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows : .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
```

Le traitement des relevés images nécessite **Tesseract** avec la langue française (les
tests OCR sont ignorés en local s'il n'est pas installé) :

```bash
sudo apt install tesseract-ocr tesseract-ocr-fra   # Debian / Ubuntu
brew install tesseract tesseract-lang              # macOS
# Windows : installateur UB Mannheim (cocher la langue « French »)
```

## Démarrage rapide

```bash
make pipeline
```

Cette commande unique démarre PostgreSQL et le stockage S3 (en attendant qu'ils soient
prêts), génère les CSV, les envoie dans le bucket S3 et vérifie leur présence, puis les
récupère depuis le bucket, les charge, les valide, les traite et insère les résultats en
base. Le traitement ne lit aucun fichier sur le disque local. `make` seul affiche
toutes les commandes disponibles :

| Commande | Rôle |
|---|---|
| `make pipeline` | Pipeline complète (`FILES=10 SEED=7 make pipeline` pour changer les données) |
| `make test` | Tous les tests, unitaires et intégration |
| `make ci` | Reproduit la CI en local (style + tests) |
| `make format` | Corrige automatiquement le style du code |
| `make db-shell` | Terminal SQL sur la base |
| `make up` | Démarre PostgreSQL et le stockage S3 |
| `make s3-ls` | Liste les fichiers présents dans le bucket S3 |
| `make db-reset` | Vide la base et le stockage S3, puis les redémarre |
| `make demo-retry` | Démo : 2 pannes simulées à l'insertion, puis succès |
| `make demo-retry-echec` | Démo : pannes répétées, arrêt après 3 tentatives |
| `make demo-panne` | Démo réelle : PostgreSQL arrêté puis redémarré pendant la pipeline |
| `make demo-retry-s3` | Démo : 2 pannes simulées à la récupération depuis S3, puis succès |
| `make demo-retry-s3-echec` | Démo : fichier S3 inaccessible, arrêt propre après 3 tentatives |
| `make demo-panne-s3` | Démo réelle : RustFS arrêté puis redémarré pendant la récupération |
| `make pipeline-ocr` | Pipeline complète avec CSV **et** relevés images lus par OCR (`IMAGES=3 make pipeline-ocr`) |
| `make demo-ocr-echec` | Démo : une image illisible arrête la pipeline, rien n'est inséré |
| `make docker-build` | Construit l'image Docker du système de traitement |
| `make k8s-up` | Déploie la production dans un cluster Kubernetes local (kind) |
| `make k8s-depot` | Dépose des CSV et des images dans le bucket du cluster |
| `make k8s-traitement` | Lance le traitement tout de suite, sans attendre le CronJob |
| `make k8s-etat` | État du cluster et fichiers traités en base |
| `make k8s-down` | Supprime le cluster local |

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

### La pipeline en détail

```bash
run-pipeline --input data --generate 5 --seed 42 --upload --source s3 --save
```

| Option | Rôle |
|---|---|
| `--input` | Dossier des fichiers CSV et images (défaut : `data`) |
| `--generate N` | Génère N fichiers CSV avant le traitement |
| `--images N` | Génère N relevés au format image (PNG) avant le traitement |
| `--seed` | Graine de génération (défaut : 42) |
| `--upload` | Envoie les fichiers dans le bucket S3 et vérifie leur présence |
| `--upload-only` | S'arrête après l'envoi : le traitement est lancé séparément (CronJob) |
| `--source` | Origine des fichiers à traiter : `local` (défaut) ou `s3` |
| `--allow-empty` | Une source sans aucun fichier n'est pas une erreur (traitement planifié) |
| `--save` | Insère les résultats en base |
| `--max-attempts` | Nombre maximal de tentatives par accès à la base ou à S3 (défaut : 3) |
| `--retry-delay` | Attente avant la 2e tentative, doublée ensuite (défaut : 1 s) |
| `--simulate-insert-failures N` | Démonstration : fait échouer les N premières tentatives d'insertion |
| `--simulate-download-failures N` | Démonstration : fait échouer les N premières récupérations depuis S3 |

La pipeline s'exécute en six étapes affichées dans le terminal :

1. **Génération des fichiers** : CSV (avec `--generate`) et relevés images (avec `--images`).
2. **Envoi vers le stockage S3** (avec `--upload`) : création du bucket s'il n'existe pas,
   envoi des fichiers, puis vérification de leur présence et de leur intégrité.
3. **Préparation de la base** (avec `--save`) : création du schéma s'il n'existe pas, et
   lecture des empreintes des fichiers déjà traités.
4. **Récupération des fichiers** depuis le bucket S3 (`--source s3`) ou le dossier local,
   en excluant les fichiers déjà traités.
5. **Chargement et traitement** : lecture des CSV, lecture des images par OCR, validation
   de tous les nouveaux fichiers, puis calcul des
   sommes envoyées par `iban_origine`, envoyées par `banque_source` et reçues par
   `iban_destinataire`, et flag des transactions de plus de 5000 (rapport affiché).
6. **Insertion en base** (avec `--save`) : chaque fichier est inséré avec ses transactions
   et ses sommes, dans une transaction SQL unique.

**Aucun double traitement.** Un fichier est identifié par le hash SHA-256 de son contenu.
Un fichier déjà traité est ignoré, même renommé : la pipeline peut être relancée autant de
fois que nécessaire sans jamais compter une transaction deux fois.

**Tout ou rien.** Tous les nouveaux fichiers sont validés avant la première insertion. Si un
seul est invalide, la commande affiche toutes les erreurs avec leur numéro de ligne, n'insère
rien et se termine avec le code retour `1`, ce qui fait échouer la CI.

### Stockage S3

Les fichiers (CSV et images) sont stockés dans un service compatible S3, **RustFS**, lancé en local
avec Docker Compose et en CI comme service du job d'intégration. Aucun service cloud
n'est utilisé. Sa console web est accessible sur http://localhost:9001, avec les
identifiants `S3_ACCESS_KEY` et `S3_SECRET_KEY` du fichier `.env`.

| Variable | Rôle | Défaut |
|---|---|---|
| `S3_ENDPOINT_URL` | Adresse de l'API S3 | `http://localhost:9000` |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | Identifiants | `pipeline` / `pipeline-secret` |
| `S3_BUCKET` | Nom du bucket | `transactions` |

L'étape d'envoi, définie dans `src/pipeline/storage/s3.py` :

1. **crée le bucket** s'il n'existe pas (l'opération est idempotente) ;
2. **envoie chaque fichier** sous la clé `transactions/<début de l'empreinte>/<nom du fichier>`,
   avec en métadonnée l'empreinte SHA-256 complète de son contenu ;
3. **vérifie** que chaque fichier est présent dans le bucket et que l'empreinte stockée
   correspond à celle du fichier envoyé. Sinon, la pipeline s'arrête en erreur.

**La clé dépend du contenu.** Renvoyer le même fichier ne crée pas de doublon dans le
bucket, et un fichier de même nom mais de contenu différent n'écrase jamais le précédent,
même si celui-ci n'a pas encore été traité : aucune donnée ne peut être perdue.

Les accès au stockage S3 bénéficient du même mécanisme de retry que la base : un service
en cours de démarrage ou momentanément indisponible ne fait pas échouer la pipeline.
`make s3-ls` (ou la commande `list-s3`) affiche le contenu du bucket.

### Lecture depuis le stockage S3

Avec `--source s3`, les fichiers à traiter sont récupérés depuis le bucket, et non plus
depuis le disque local (`src/pipeline/loading/sources.py`) :

```
S3 ──> liste des fichiers ──> pour chaque fichier :
         empreinte déjà traitée ? ──oui──> ignoré, sans téléchargement
         sinon : téléchargement ──> contrôle d'intégrité ──> chargement ──> traitement ──> base
```

- **Pas de double traitement.** L'empreinte enregistrée sur l'objet est comparée à la
  table `processed_files` avant tout téléchargement : un fichier déjà traité n'est même pas
  téléchargé. Après téléchargement, l'empreinte est recalculée sur le contenu reçu.
- **Contrôle d'intégrité.** Si le contenu reçu ne correspond pas à l'empreinte enregistrée
  (transfert corrompu), la récupération est considérée comme ratée et réessayée.
- **Traçabilité.** L'URI complète de l'objet (`s3://bucket/transactions/.../fichier.csv`)
  est enregistrée dans `processed_files`.
- **Un seul format de chargement.** Le décodage et la validation (`parse_csv`) prennent un
  contenu en mémoire : ils sont identiques quelle que soit la source.

### Retry lors de la récupération depuis S3

Chaque accès au bucket (liste, métadonnées, téléchargement) utilise le mécanisme de retry :
l'échec est **détecté** et **affiché**, la pipeline **attend** avant de **réessayer**,
jusqu'au **nombre maximal** de tentatives. Si un fichier reste inaccessible, la pipeline
**s'arrête proprement** : comme la récupération a lieu avant toute insertion, aucune
donnée n'est insérée, et une relance reprend l'ensemble du traitement.

```
  ATTENTION : tentative 1/3 de « récupération de transactions_001.csv » échouée :
              TransientS3Error: ... Nouvelle tentative dans 1 s
  ÉCHEC : tentative 3/3 de « récupération de transactions_001.csv » échouée : ...

ERREUR : stockage S3 inaccessible (http://localhost:9000/transactions) : ...
Le traitement est arrêté : aucune donnée n'a été insérée.
```

Sont réessayées les erreurs réseau (service arrêté, connexion coupée, délai dépassé), les
erreurs 5xx du service et les contenus corrompus. Un fichier supprimé du bucket ou des
identifiants refusés arrêtent la pipeline immédiatement. La panne peut être simulée
(`--simulate-download-failures N`, `make demo-retry-s3`) ou réelle (`make demo-panne-s3`).

### Retry en cas de problème temporaire

Chaque accès à la base (connexion et préparation, puis insertion de chaque fichier) est
réessayé en cas d'erreur temporaire : base indisponible, connexion perdue, conflit de
transaction. Le mécanisme, défini dans `src/pipeline/retry.py` :

1. **détecte l'échec** et affiche l'erreur rencontrée avec le numéro de la tentative ;
2. **attend avant de réessayer**, avec un délai qui double à chaque échec (1 s, 2 s, 4 s...) ;
3. **effectue plusieurs tentatives**, jusqu'à un **nombre maximal** (`--max-attempts`) ;
4. **arrête la pipeline** au-delà, avec un message clair et le code retour `1`.

```
  ATTENTION : tentative 1/3 de « insertion de transactions_001.csv » échouée :
              OperationalError: connexion perdue. Nouvelle tentative dans 1 s
  ATTENTION : tentative 2/3 de « insertion de transactions_001.csv » échouée : ...
  transactions_001.csv inséré (id=1, 8 transactions)
```

**Seules les erreurs temporaires sont réessayées.** Une donnée invalide ou un fichier déjà
traité ne se résoudra pas en attendant : ces erreurs sont traitées immédiatement.

**Un retry ne crée jamais de doublon.** L'insertion d'un fichier est atomique : une panne en
cours d'insertion annule tout, et la tentative suivante repart de zéro. Si une tentative a
réussi mais que sa confirmation a été perdue, la suivante trouve le hash du fichier dans
`processed_files` et le fichier est ignoré au lieu d'être compté deux fois.

**Reprise après arrêt.** Les fichiers insérés avant l'arrêt restent en base : relancer la
pipeline ignore ces fichiers et reprend avec les suivants.

**Simulation.** L'option `--simulate-insert-failures N` provoque une panne juste avant la
validation de la transaction SQL, au pire moment possible, pour démontrer à la fois le retry
et l'atomicité. `make demo-panne` provoque une vraie panne : PostgreSQL est arrêté puis
redémarré 5 secondes plus tard, pendant que la pipeline réessaie de s'y connecter.

### Architecture du traitement

```
Disque ou S3 ──> loading (I/O) ──> parse_csv + validation (purs) ──> processing (pur) ──> résultats
```

- **`loading/`** : récupération des fichiers (disque local ou S3), décodage du CSV et
  appel de Tesseract pour les images, aucune logique métier.
- **`extraction/`** : extraction et normalisation des transactions à partir du texte OCR
  (fonctions pures).
- **`validation.py`** : conversion d'une ligne en `Transaction` et contrôle du format
  (colonnes, date ISO 8601, montant positif à 2 décimales, IBAN valide et cohérent avec
  le pays, émetteur différent du destinataire). Toutes les erreurs d'une ligne sont
  signalées en une seule fois.
- **`processing/`** : traitements purs. Ils ne modifient pas leurs entrées (transactions
  immuables), ne dépendent d'aucun état externe et renvoient toujours le même résultat
  pour les mêmes entrées. Des tests vérifient explicitement ces trois propriétés.

## Niveau 3 — Option 2 : relevés images lus par OCR

La pipeline traite aussi des transactions contenues dans des **images** (PNG, JPEG, TIFF).
Elles rejoignent exactement le même flux que les CSV : stockage S3, absence de double
traitement, validation, traitements, insertion.

```
Image ──> OCR (Tesseract) ──> extraction ──> normalisation ──> validation ──> traitement ──> base
          loading/ocr.py      extraction/statement.py         validation.py   (identiques aux CSV)
```

### Les relevés générés

`generate-images --output data --images 2 --seed 42` (ou `run-pipeline --images N`) produit
des relevés d'opérations de 3 transactions chacun, au format d'un document bancaire :

```
RELEVÉ D'OPÉRATIONS
Référence : REL-42-001
Nombre de transactions : 3

Transaction 1
Date : 01/01/2026 08:22:30
IBAN émetteur : FR24 3000 3012 3400 0112 2334 455
Pays émetteur : FR
Banque émettrice : Société Générale
IBAN destinataire : IT65 X020 0801 6000 0001 2345 678
Pays destinataire : IT
Montant : 4 999,99 EUR
...
```

- **Déterministes**, comme les CSV : même graine, mêmes images (la police DejaVu Sans Mono
  est embarquée dans le paquet pour un rendu identique sur toutes les machines), donc même
  empreinte SHA-256 et aucun double traitement.
- **Format différent du CSV** (IBAN groupés par 4, date française, montant `4 999,99 EUR`),
  pour que l'étape de transformation soit réellement nécessaire.
- **Cas limites du flag** : la première image contient toujours `4999,99`, `5000,00` et
  `5000,01`, pour vérifier que le seuil est bien appliqué après l'OCR.

### Extraction, transformation et validation

1. **OCR** : Tesseract lit l'image (langue `fra`, pour les accents des noms de banques).
2. **Extraction** : le texte est découpé en transactions (en-têtes « Transaction N »), et
   chaque ligne « libellé : valeur » est associée à une colonne, quels que soient les
   accents, la casse ou les espaces du libellé.
3. **Normalisation** vers le format CSV : IBAN sans espaces, date ISO 8601, montant
   `4999.99`. Les confusions classiques de l'OCR sont corrigées uniquement là où le format
   est sans ambiguïté (ex. `FRO8` → `FR08`, car la clé d'un IBAN est toujours numérique).
4. **Validation** : chaque transaction passe par **la même fonction de validation que les
   lignes CSV**. Une erreur de lecture plus profonde est détectée par la clé de contrôle
   modulo 97 de l'IBAN, et le nombre de transactions lues est comparé à celui annoncé par
   le relevé (une transaction manquée est détectée).

Une image illisible ou invalide est traitée comme un CSV invalide : **tout ou rien**, aucune
donnée n'est insérée et la pipeline s'arrête avec le code retour `1` (`make demo-ocr-echec`).

```
ERREUR : Image invalide : releve_002.png (1 erreur(s))
  - transaction 2 : iban_origine invalide : 'FR7630004000031234561890143'
Aucune donnée n'a été insérée.
```

**Fiabilité mesurée** : sur 120 relevés générés avec des graines différentes, l'OCR
retrouve exactement les transactions dessinées (accents compris) ; un test vérifie aussi un
scan de qualité moyenne (JPEG compressé, flou et légèrement penché).

## Niveau 3 — Option 1 : déploiement continu sur Kubernetes

L'environnement de production est décrit dans `k8s/` et déployé avec `kubectl apply -k k8s` :

| Ressource | Rôle |
|---|---|
| `StatefulSet/postgres` + volume persistant | Base de données relationnelle |
| `StatefulSet/rustfs` + volume persistant | Stockage S3 (API 9000, console 9001) |
| `CronJob/pipeline-traitement` | Système de traitement, lancé **toutes les 2 minutes** |
| `ConfigMap/pipeline-config`, `Secret/pipeline-secrets` | Configuration et identifiants |
| `k8s/jobs/depot-donnees.yaml` | Job de dépôt de données dans le bucket (source externe simulée) |

Le système de traitement est une **image Docker** (`Dockerfile` : Python 3.12, la pipeline et
Tesseract), exécutée sans privilèges et avec un système de fichiers en lecture seule.

### Traitement régulier des nouvelles données

Toutes les 2 minutes, le CronJob lance `run-pipeline --source s3 --save --allow-empty` :
il récupère dans le bucket les fichiers (CSV et images) **qui n'ont pas encore été traités**,
grâce aux empreintes de la table `processed_files`, puis les charge, les valide, les traite
et les insère. S'il n'y a rien de nouveau, il se termine normalement (`--allow-empty`).
`concurrencyPolicy: Forbid` empêche deux traitements de se chevaucher.

Le dépôt des données est séparé du traitement, comme en production où les fichiers arrivent
d'une source externe : `run-pipeline ... --upload --upload-only` envoie les fichiers dans le
bucket sans les traiter.

### La pipeline CD (`.github/workflows/cd.yml`)

```
Pull Request ou CI verte sur main
   │
   ├─ 1. Déploiement et vérification (cluster Kubernetes kind, créé dans GitHub Actions)
   │     construction de l'image ─> contrôle (Tesseract, utilisateur non root)
   │     ─> déploiement ─> 1er traitement planifié sur un bucket vide
   │     ─> dépôt de 3 CSV + 2 images ─> traitement automatique par le CronJob ─> contrôles SQL
   │     ─> nouveau dépôt (données déjà connues + nouvelles) ─> seules les nouvelles traitées
   │     ─> redémarrage de la base : les données sont conservées
   │
   ├─ 2. Publication de l'image dans GHCR (main uniquement, image vérifiée à l'étape 1)
   │     ghcr.io/zainmehr/pipeline-transactions:<commit> et :latest
   │
   └─ 3. Déploiement en production (main uniquement, environnement GitHub « production »)
         sur le cluster défini par le secret KUBE_CONFIG
```

Les contrôles SQL (`scripts/k8s.sh verifier`) vérifient le nombre de fichiers traités, la
présence de relevés images, l'absence de fichier traité deux fois, la cohérence entre
sommes stockées et recalculées, et le positionnement des flags.

**Cluster de production.** Le déploiement vérifié à l'étape 1 a lieu dans un cluster
Kubernetes éphémère (kind) : aucune infrastructure n'est nécessaire. Pour déployer sur un
vrai cluster, il suffit d'ajouter dans les secrets du dépôt `KUBE_CONFIG` (kubeconfig du
cluster) et les identifiants `DB_USER`, `DB_PASSWORD`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` :
l'étape 3 applique alors les mêmes manifestes avec l'image publiée. Sans `KUBE_CONFIG`,
elle se contente de l'indiquer.

### En local

Avec Docker et [kind](https://kind.sigs.k8s.io/) installés :

```bash
make k8s-up                       # cluster kind, image, déploiement
make k8s-depot SEED=7             # dépose 5 CSV et 2 images dans le bucket
make k8s-etat                     # au bout de 2 minutes au plus : fichiers traités en base
make k8s-traitement               # ou lancer le traitement tout de suite
make k8s-down
```

## Base de données

Le schéma est défini dans `src/pipeline/storage/schema.sql`. Il est idempotent et embarqué
dans le paquet Python, pour fonctionner aussi dans un conteneur.

| Table | Contenu |
|---|---|
| `processed_files` | Fichiers insérés, identifiés par le hash SHA-256 de leur contenu (unique), avec leur type (`csv` ou `image`) |
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
pytest -m ocr                                    # tests de lecture OCR (Tesseract requis)
pytest --cov=pipeline --cov-report=term-missing  # avec couverture
ruff check . && ruff format --check .            # lint et formatage
```

Les tests d'intégration (`tests/integration/`) couvrent la base seule (`test_database.py`)
et la pipeline complète de bout en bout (`test_pipeline.py`) : valeurs attendues calculées à
la main, données générées, relance sans doublon, ajout d'un nouveau fichier, fichier
invalide. `test_retry_pipeline.py` couvre les pannes simulées, l'arrêt après le nombre
maximal de tentatives, la reprise et l'absence de doublons. `test_s3.py` couvre la
création du bucket, l'envoi, la vérification d'intégrité et la commande `list-s3`.
`test_s3_source.py` couvre la lecture depuis S3 : fichiers déjà traités ignorés sans
téléchargement, contenu altéré, pannes simulées puis succès, arrêt propre et reprise,
traitement complet sans aucun fichier local, traitement planifié sur bucket vide.

Pour le niveau 3, `test_extraction.py` et `test_images.py` testent l'extraction et la
génération des images sans Tesseract (OCR simulé), `test_ocr.py` la lecture réelle par
Tesseract, et `test_ocr_pipeline.py` la pipeline de bout en bout avec des images : valeurs
en base identiques aux transactions dessinées, flags des cas limites, CSV et images
mélangés, image renommée non retraitée, image illisible, lecture depuis S3, retry, dépôt
puis traitement séparé.

Les tests utilisent la base `pipeline_test`, vidée avant chaque test, et un bucket S3
temporaire propre à chaque test, supprimé ensuite. Si un service n'est pas lancé, ses tests
sont ignorés en local ; en CI (`REQUIRE_DB=1`, `REQUIRE_S3=1`, `REQUIRE_OCR=1`), ils échouent.

Reproduire la CI en local avant de pousser :

```bash
make ci
```

## Intégration continue

La CI (GitHub Actions) se déclenche sur chaque Pull Request et sur chaque push sur `main`.
Elle comporte deux jobs :

1. **Qualité et tests unitaires** : lint, formatage, tests unitaires avec couverture (dont la
   lecture réelle d'images par Tesseract), puis contrôle du script de déploiement
   (shellcheck), du Dockerfile (hadolint) et des manifestes Kubernetes (kubeconform).
2. **Base de données et tests d'intégration** (lancé seulement si le premier réussit) :
   démarre PostgreSQL et RustFS, exécute les tests d'intégration, lance la pipeline
   complète (envoi vers S3 puis lecture depuis S3), la relance pour vérifier l'absence de
   doublons, contrôle le contenu du bucket, puis contrôle directement en SQL le nombre de
   fichiers en base et la cohérence des sommes. Il simule ensuite, à l'insertion puis à la
   récupération depuis S3, des pannes temporaires (retry puis succès), des pannes
   persistantes (arrêt attendu) et une reprise, et vérifie à nouveau le bucket et la base.
   Enfin, il traite des relevés images (envoi S3, OCR, insertion), sépare dépôt et
   traitement comme le CronJob, et vérifie en SQL les relevés en base et les flags lus par OCR.

La CD (déploiement Kubernetes) est décrite dans la section
[Option 1](#niveau-3--option-1--déploiement-continu-sur-kubernetes) : sur `main`, elle ne
démarre qu'après une CI verte.

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
- [x] 1.4 Pipeline complète
- [x] 1.5 Tests d'intégration automatiques
- [x] 1.6 Simulation d'un échec de pipeline ([détail](docs/simulation-echec.md))
- [x] 1.7 Retry lors de l'insertion en base

### Niveau 2 — Stockage S3
- [x] 2.1 Stockage des fichiers dans un service S3
- [x] 2.2 Upload automatique dans le S3
- [x] 2.3 Lecture des données depuis le S3
- [x] 2.4 Retry lors de la récupération depuis le S3

### Niveau 3 — Améliorations
- [x] Option 1 — Pipeline CD et déploiement sur Kubernetes (CronJob de traitement)
- [x] Option 2 — Extraction de transactions depuis des images avec OCR (Tesseract)
