# Raccourcis du projet. `make` ou `make help` affiche la liste des commandes.
.DEFAULT_GOAL := help
.PHONY: help install up db-up db-down db-reset db-shell s3-ls pipeline pipeline-ocr demo-retry demo-retry-echec demo-panne demo-retry-s3 demo-retry-s3-echec demo-panne-s3 demo-ocr-echec lint format test test-unit ci clean docker-build k8s-up k8s-depot k8s-traitement k8s-etat k8s-down

FILES  ?= 5
IMAGES ?= 2
SEED   ?= 42
KIND_CLUSTER ?= pipeline
IMAGE  ?= pipeline-transactions:local

help: ## Affiche cette aide
	@grep -E '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Installe le projet et les outils de développement
	pip install -e ".[dev]"

up: ## Démarre PostgreSQL et le stockage S3, et attend qu'ils soient prêts
	docker compose up -d --wait
	@printf "Attente du stockage S3"; for i in $$(seq 60); do \
		if curl -fsS http://localhost:9000/health >/dev/null 2>&1; then echo " : prêt"; exit 0; fi; \
		printf "."; sleep 1; \
	done; echo " : le stockage S3 ne répond pas"; exit 1

db-up: up ## Alias de `make up`

db-down: ## Arrête les services (les données sont conservées)
	docker compose down

db-reset: ## Supprime toutes les données (base et S3) puis redémarre les services
	docker compose down -v
	$(MAKE) up

s3-ls: ## Liste les fichiers présents dans le bucket S3
	list-s3

db-shell: ## Ouvre un terminal SQL sur la base
	docker compose exec postgres psql -U pipeline -d pipeline

pipeline: up ## Pipeline complète : génération, envoi S3, lecture depuis S3, traitement, insertion
	run-pipeline --input data --generate $(FILES) --seed $(SEED) --upload --source s3 --save

pipeline-ocr: up ## Pipeline complète avec des relevés images lus par OCR (Tesseract requis)
	run-pipeline --input data --generate $(FILES) --images $(IMAGES) --seed $(SEED) --upload --source s3 --save

demo-ocr-echec: ## Démo : une image illisible arrête la pipeline, rien n'est inséré
	rm -rf data/demo-ocr-echec && mkdir -p data/demo-ocr-echec
	printf 'pas une image' > data/demo-ocr-echec/photo.png
	-run-pipeline --input data/demo-ocr-echec --images 1 --seed $$(date +%s)

demo-retry: up ## Démo : 2 pannes simulées à l'insertion, puis succès
	run-pipeline --input data/demo-retry --generate 3 --seed $$(date +%s) --save --simulate-insert-failures 2

demo-retry-echec: up ## Démo : pannes répétées, arrêt après 3 tentatives
	run-pipeline --input data/demo-echec --generate 3 --seed $$(date +%s) --save --simulate-insert-failures 10

demo-panne: up ## Démo réelle : PostgreSQL arrêté puis redémarré pendant la pipeline
	docker compose stop postgres
	(sleep 5 && docker compose start postgres) &
	run-pipeline --input data/demo-panne --generate 2 --seed $$(date +%s) --save --max-attempts 5

demo-retry-s3: up ## Démo : 2 pannes simulées à la récupération depuis S3, puis succès
	run-pipeline --input data/demo-s3 --generate 3 --seed $$(date +%s) --upload --source s3 --save --simulate-download-failures 2

demo-retry-s3-echec: up ## Démo : fichier S3 inaccessible, arrêt propre après 3 tentatives
	run-pipeline --input data/demo-s3-echec --generate 3 --seed $$(date +%s) --upload --source s3 --save --simulate-download-failures 10

demo-panne-s3: up ## Démo réelle : RustFS arrêté puis redémarré pendant la récupération
	run-pipeline --input data/demo-panne-s3 --generate 2 --seed $$(date +%s) --upload
	docker compose stop rustfs
	(sleep 5 && docker compose start rustfs) &
	run-pipeline --source s3 --save --max-attempts 5

lint: ## Vérifie le style du code
	ruff check .
	ruff format --check .

format: ## Corrige automatiquement le style du code
	ruff check . --fix
	ruff format .

test-unit: ## Lance les tests unitaires
	pytest -m "not integration"

test: up ## Lance tous les tests (unitaires et intégration)
	pytest --cov=pipeline --cov-report=term-missing

ci: lint test ## Reproduit la CI en local

clean: ## Supprime les fichiers générés et les caches
	rm -rf data/*.csv data/*.png data/demo-* .pytest_cache .ruff_cache .coverage htmlcov

docker-build: ## Construit l'image Docker du système de traitement
	docker build -t $(IMAGE) .

k8s-up: docker-build ## Déploie la production dans un cluster Kubernetes local (kind)
	kind get clusters | grep -qx $(KIND_CLUSTER) || kind create cluster --name $(KIND_CLUSTER)
	kind load docker-image $(IMAGE) --name $(KIND_CLUSTER)
	IMAGE=$(IMAGE) scripts/k8s.sh deployer

k8s-depot: ## Dépose des CSV et des images dans le bucket du cluster (FILES, IMAGES, SEED)
	IMAGE=$(IMAGE) scripts/k8s.sh deposer $(SEED) $(FILES) $(IMAGES)

k8s-traitement: ## Lance le traitement tout de suite, sans attendre le CronJob
	scripts/k8s.sh lancer

k8s-etat: ## État du cluster et fichiers traités en base
	scripts/k8s.sh etat

k8s-down: ## Supprime le cluster Kubernetes local
	kind delete cluster --name $(KIND_CLUSTER)
