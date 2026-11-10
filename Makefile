# Raccourcis du projet. `make` ou `make help` affiche la liste des commandes.
.DEFAULT_GOAL := help
.PHONY: help install up db-up db-down db-reset db-shell s3-ls pipeline demo-retry demo-retry-echec demo-panne lint format test test-unit ci clean

FILES ?= 5
SEED  ?= 42

help: ## Affiche cette aide
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

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

pipeline: up ## Pipeline complète : services, génération, envoi S3, traitement, insertion
	run-pipeline --input data --generate $(FILES) --seed $(SEED) --upload --save

demo-retry: up ## Démo : 2 pannes simulées à l'insertion, puis succès
	run-pipeline --input data/demo-retry --generate 3 --seed $$(date +%s) --save --simulate-insert-failures 2

demo-retry-echec: up ## Démo : pannes répétées, arrêt après 3 tentatives
	run-pipeline --input data/demo-echec --generate 3 --seed $$(date +%s) --save --simulate-insert-failures 10

demo-panne: up ## Démo réelle : PostgreSQL arrêté puis redémarré pendant la pipeline
	docker compose stop postgres
	(sleep 5 && docker compose start postgres) &
	run-pipeline --input data/demo-panne --generate 2 --seed $$(date +%s) --save --max-attempts 5

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
	rm -rf data/*.csv .pytest_cache .ruff_cache .coverage htmlcov
