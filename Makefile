# Raccourcis du projet. `make` ou `make help` affiche la liste des commandes.
.DEFAULT_GOAL := help
.PHONY: help install db-up db-down db-reset db-shell pipeline lint format test test-unit ci clean

FILES ?= 5
SEED  ?= 42

help: ## Affiche cette aide
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Installe le projet et les outils de développement
	pip install -e ".[dev]"

db-up: ## Démarre PostgreSQL et attend qu'il soit prêt
	docker compose up -d --wait

db-down: ## Arrête PostgreSQL (les données sont conservées)
	docker compose down

db-reset: ## Supprime toutes les données puis redémarre PostgreSQL
	docker compose down -v
	docker compose up -d --wait

db-shell: ## Ouvre un terminal SQL sur la base
	docker compose exec postgres psql -U pipeline -d pipeline

pipeline: db-up ## Pipeline complète : base, génération, traitement, insertion
	run-pipeline --input data --generate $(FILES) --seed $(SEED) --save

lint: ## Vérifie le style du code
	ruff check .
	ruff format --check .

format: ## Corrige automatiquement le style du code
	ruff check . --fix
	ruff format .

test-unit: ## Lance les tests unitaires
	pytest -m "not integration"

test: db-up ## Lance tous les tests (unitaires et intégration)
	pytest --cov=pipeline --cov-report=term-missing

ci: lint test ## Reproduit la CI en local

clean: ## Supprime les fichiers générés et les caches
	rm -rf data/*.csv .pytest_cache .ruff_cache .coverage htmlcov
