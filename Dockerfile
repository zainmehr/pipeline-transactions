# Image du système de traitement : la pipeline Python, avec Tesseract pour l'OCR.
# Utilisée par le CronJob Kubernetes (traitement planifié) et par le job de dépôt de données.
FROM python:3.12-slim

# Tesseract et sa langue française (accents des noms de banques sur les relevés), et un
# utilisateur sans privilèges pour l'exécution.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-fra \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home-dir /app pipeline

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install .

# Exécution sans privilèges ; /tmp sert de dossier de travail au job de dépôt.
USER 10001

ENTRYPOINT ["run-pipeline"]
# Par défaut : traitement des nouveaux fichiers du bucket S3 et insertion en base.
CMD ["--source", "s3", "--save", "--allow-empty"]
